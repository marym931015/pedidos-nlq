"""Test 2: el validador de SQL. Lo que debe pasar y, sobre todo, lo que no."""

import pytest

from src.guard import LIMITE_FILAS, SqlNoValido, validar


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM v_top_clientes",
        "SELECT pais, sum(importe_total) FROM v_ventas_mes_pais GROUP BY pais",
        "WITH t AS (SELECT * FROM v_ventas_mes_pais) SELECT * FROM t",
        "select MES, PAIS from V_VENTAS_MES_PAIS where pais = 'España'",
    ],
)
def test_acepta_consultas_legitimas(sql):
    salida = validar(sql)
    assert salida.upper().startswith(("SELECT", "WITH"))


@pytest.mark.parametrize(
    "sql, motivo",
    [
        ("DROP TABLE orders", "solo_lectura"),
        ("DELETE FROM v_top_clientes", "solo_lectura"),
        ("INSERT INTO orders VALUES (1)", "solo_lectura"),
        ("UPDATE orders SET importe = 0", "solo_lectura"),
        ("ATTACH 'otra.db'", "solo_lectura"),
        ("COPY v_top_clientes TO 'robado.csv'", "solo_lectura"),
        # Dos sentencias: el clasico que burla a quien solo mira como empieza el texto.
        ("SELECT * FROM v_top_clientes; DROP TABLE orders", "multiples_sentencias"),
        # Tablas fuera de la allowlist, incluidas las del propio proyecto.
        ("SELECT * FROM orders", "tabla_no_permitida"),
        ("SELECT * FROM orders_rejected", "tabla_no_permitida"),
        ("SELECT * FROM duckdb_settings()", "tabla_no_permitida"),
        # Un JOIN legitimo con una tabla prohibida colada dentro.
        ("SELECT * FROM v_top_clientes JOIN orders USING (cliente)", "tabla_no_permitida"),
        # Subconsulta: la tabla prohibida no esta en el FROM principal.
        ("SELECT * FROM v_top_clientes WHERE cliente IN (SELECT cliente FROM orders)", "tabla_no_permitida"),
        # DuckDB puede leer ficheros desde un SELECT. Un SELECT no es inofensivo.
        # sqlglot ve read_csv_auto() en el FROM como una tabla, asi que lo para
        # la allowlist antes que la lista de funciones. Rechazado igual.
        ("SELECT * FROM read_csv_auto('/etc/passwd')", "tabla_no_permitida"),
        ("SELECT getenv('GEMINI_API_KEY')", "sin_tablas"),
        ("", "sql_vacio"),
        ("esto no es sql", "sql_no_parseable"),
    ],
)
def test_rechaza_lo_peligroso(sql, motivo):
    with pytest.raises(SqlNoValido) as err:
        validar(sql)
    assert err.value.motivo == motivo


def test_el_comentario_no_esconde_una_segunda_sentencia():
    # Quien valide con `"DROP" in sql.upper()` rechaza esto por error, y quien
    # parta por ';' lo acepta por error. El parser ve una sola sentencia y el
    # SQL regenerado sale sin comentarios.
    salida = validar("SELECT * FROM v_top_clientes -- ; DROP TABLE orders")
    assert "DROP" not in salida.upper()
    assert "--" not in salida


def test_anade_limit_si_falta():
    assert f"LIMIT {LIMITE_FILAS}" in validar("SELECT * FROM v_top_clientes").upper()


def test_recorta_un_limit_excesivo():
    salida = validar("SELECT * FROM v_ventas_mes_pais LIMIT 100000")
    assert f"LIMIT {LIMITE_FILAS}" in salida.upper()
    assert "100000" not in salida


def test_respeta_un_limit_razonable():
    assert "LIMIT 5" in validar("SELECT * FROM v_top_clientes LIMIT 5").upper()
