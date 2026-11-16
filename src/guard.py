"""Validador del SQL que genera el LLM. Es la frontera de seguridad del proyecto.

Premisa: el texto que devuelve el modelo es *entrada no confiable*, igual que si
lo hubiera escrito un usuario anonimo. El validador no comprueba que el SQL sea
bueno, comprueba que sea inofensivo.

Por que no basta con buscar palabras prohibidas en el texto:

    SELECT * FROM v_top_clientes -- ; DROP TABLE orders
    SELECT/*DROP*/ * FROM orders
    select * from ORDERS

El primero engana a quien busque ';', el segundo a quien busque 'DROP' creyendo
que hay peligro donde no lo hay, y el tercero a quien compare en minusculas. Por
eso aqui se parsea el SQL a un arbol (sqlglot) y se pregunta al arbol que tablas
toca de verdad.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

DIALECTO = "duckdb"
LIMITE_FILAS = 500

# Unicas tablas consultables. Son vistas agregadas: ni la tabla base `orders`
# ni `orders_rejected` estan aqui, asi que el LLM no puede leer datos crudos.
TABLAS_PERMITIDAS = frozenset({"v_ventas_mes_pais", "v_top_clientes"})

# Funciones de DuckDB que leen o escriben en el sistema de ficheros. Un SELECT
# no es inofensivo por si mismo: `SELECT * FROM read_csv('/etc/passwd')` es un
# SELECT. Esta lista es especifica de DuckDB y no aparece en los tutoriales
# genericos de "proteccion contra inyeccion SQL".
FUNCIONES_PROHIBIDAS = frozenset({
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
    "read_text", "read_blob", "glob", "parquet_scan", "sniff_csv", "getenv",
})


class SqlNoValido(Exception):
    """El SQL no supera alguna de las capas. El mensaje es apto para el usuario:
    explica que se rechazo sin filtrar detalles internos."""

    def __init__(self, motivo: str, detalle: str = ""):
        self.motivo = motivo
        self.detalle = detalle
        super().__init__(detalle or motivo)


def _parsear(sql: str) -> exp.Expression:
    """Capa 1: tiene que ser UN solo statement y tiene que parsear."""
    try:
        sentencias = sqlglot.parse(sql, dialect=DIALECTO)
    except Exception as err:  # sqlglot lanza varios tipos distintos
        raise SqlNoValido("sql_no_parseable", f"No se pudo interpretar el SQL: {err}") from err

    reales = [s for s in sentencias if s is not None]
    if not reales:
        raise SqlNoValido("sql_vacio", "El modelo no devolvio ninguna consulta.")
    if len(reales) > 1:
        # Aqui muere el clasico "SELECT 1; DROP TABLE orders".
        raise SqlNoValido(
            "multiples_sentencias",
            f"Solo se permite una consulta, se recibieron {len(reales)}.",
        )
    return reales[0]


def _exigir_select(arbol: exp.Expression) -> None:
    """Capa 2: la raiz debe ser SELECT (o un WITH que termine en SELECT).

    Se comprueba sobre el arbol, no sobre el texto: `sql.strip().upper()
    .startswith("SELECT")` da por bueno un `SELECT ... INTO` o lo que venga
    detras de un comentario inicial.
    """
    if isinstance(arbol, exp.Select):
        return
    if isinstance(arbol, exp.Subquery) and isinstance(arbol.this, exp.Select):
        return
    tipo = type(arbol).__name__.upper()
    raise SqlNoValido("solo_lectura", f"Solo se permiten consultas SELECT (se recibio {tipo}).")


def _exigir_tablas_permitidas(arbol: exp.Expression) -> set[str]:
    """Capa 3: toda tabla referenciada debe estar en la allowlist.

    Los CTE se excluyen: `WITH ventas AS (SELECT ... FROM v_top_clientes)`
    define `ventas` como nombre temporal, no es una tabla que haya que permitir.
    """
    alias_cte = {cte.alias_or_name.lower() for cte in arbol.find_all(exp.CTE)}
    usadas: set[str] = set()

    for tabla in arbol.find_all(exp.Table):
        nombre = tabla.name.lower()
        if nombre in alias_cte:
            continue
        if tabla.db or tabla.catalog:
            # Esquema explicito: bloquea trucos del tipo `otra_bd.main.orders`.
            raise SqlNoValido("tabla_no_permitida", f"No se permite cualificar esquemas: {tabla.sql(dialect=DIALECTO)}")
        if nombre not in TABLAS_PERMITIDAS:
            raise SqlNoValido(
                "tabla_no_permitida",
                f"La tabla '{nombre}' no esta permitida. Disponibles: {', '.join(sorted(TABLAS_PERMITIDAS))}.",
            )
        usadas.add(nombre)

    if not usadas:
        raise SqlNoValido(
            "sin_tablas",
            "La consulta no lee ninguna de las vistas permitidas.",
        )
    return usadas


def _prohibir_funciones_de_ficheros(arbol: exp.Expression) -> None:
    """Capa 4: ninguna funcion que toque el sistema de ficheros o el entorno."""
    for funcion in arbol.find_all(exp.Anonymous, exp.Func):
        nombre = (getattr(funcion, "name", "") or "").lower()
        if nombre in FUNCIONES_PROHIBIDAS:
            raise SqlNoValido("funcion_no_permitida", f"La funcion '{nombre}' no esta permitida.")


def _aplicar_limite(arbol: exp.Expression) -> exp.Expression:
    """Capa 5: LIMIT obligatorio.

    No es seguridad, es contencion: una pregunta ambigua no puede devolver la
    tabla entera por la API. Si el LLM puso un limite mayor, se recorta.
    """
    limite = arbol.args.get("limit")
    if limite is None:
        return arbol.limit(LIMITE_FILAS)
    try:
        actual = int(limite.expression.name)
    except (AttributeError, ValueError):
        return arbol.limit(LIMITE_FILAS)
    return arbol.limit(LIMITE_FILAS) if actual > LIMITE_FILAS else arbol


def validar(sql: str) -> str:
    """Devuelve el SQL saneado y listo para ejecutar, o lanza SqlNoValido.

    El SQL que sale de aqui es el que se ejecuta y el que se devuelve al cliente
    en la respuesta: lo que el usuario ve es exactamente lo que corrio.
    """
    if not sql or not sql.strip():
        raise SqlNoValido("sql_vacio", "El modelo no devolvio ninguna consulta.")

    arbol = _parsear(sql)
    _exigir_select(arbol)
    _exigir_tablas_permitidas(arbol)
    _prohibir_funciones_de_ficheros(arbol)
    arbol = _aplicar_limite(arbol)

    # Se regenera el SQL desde el arbol en vez de devolver el texto original:
    # asi los comentarios y el formato raro del modelo no llegan a la base de
    # datos, solo llega lo que el parser entendio y validamos.
    return arbol.sql(dialect=DIALECTO, pretty=False, comments=False)
