"""Conexion a DuckDB. Un solo sitio donde se decide la ruta y el modo de acceso."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

RUTA_BD = Path(os.getenv("DUCKDB_PATH", "orders.duckdb"))
RUTA_VISTAS = Path(__file__).with_name("views.sql")

# La API solo puede leer estas dos vistas. La lista vive aqui, junto a la
# conexion, porque es la misma frontera que aplica el validador de SQL.
VISTAS_PERMITIDAS = ("v_ventas_mes_pais", "v_top_clientes")


def conectar(solo_lectura: bool = False, ruta: Path | None = None):
    """Abre la base de datos.

    `solo_lectura=True` es lo que usa la API: DuckDB rechaza cualquier escritura
    a nivel de conexion. Es defensa en profundidad — aunque el validador de SQL
    tuviera un agujero, la base de datos fisicamente no deja escribir.
    """
    destino = Path(ruta) if ruta else RUTA_BD
    if solo_lectura and not destino.exists():
        raise FileNotFoundError(
            f"No existe {destino}. Ejecuta antes: python src/generate_data.py && python src/etl.py"
        )
    return duckdb.connect(str(destino), read_only=solo_lectura)


def crear_vistas(con) -> None:
    """Aplica views.sql. El SQL vive en su fichero, no incrustado en Python:
    asi se puede leer y ejecutar a mano en la consola de DuckDB."""
    con.execute(RUTA_VISTAS.read_text(encoding="utf-8"))


def esquema_vistas(con) -> dict[str, list[dict[str, str]]]:
    """Columnas y tipos de cada vista permitida. Lo usan el endpoint /schema y
    el prompt del LLM: si el modelo no conoce las columnas, inventa nombres."""
    esquema: dict[str, list[dict[str, str]]] = {}
    for vista in VISTAS_PERMITIDAS:
        filas = con.execute(f"DESCRIBE {vista}").fetchall()
        esquema[vista] = [{"columna": f[0], "tipo": f[1]} for f in filas]
    return esquema


def contexto_datos(con) -> dict:
    """Resumen de que contienen las vistas: periodo cubierto y valores de `pais`.

    Va al prompt del LLM. Sin esto el modelo escribe SQL sintacticamente perfecto
    contra un rango de fechas que no existe ("marzo" -> 2023 en vez de 2024) y
    devuelve cero filas, que es el peor fallo posible: no da error, da vacio.
    """
    desde, hasta = con.execute(
        "SELECT min(mes), max(mes) FROM v_ventas_mes_pais"
    ).fetchone()
    paises = [f[0] for f in con.execute(
        "SELECT DISTINCT pais FROM v_ventas_mes_pais ORDER BY 1"
    ).fetchall()]
    return {"desde": str(desde), "hasta": str(hasta), "paises": paises}
