"""ETL: CSV crudo -> limpieza -> DuckDB (orders + orders_rejected) -> vistas.

Se puede relanzar las veces que haga falta: las tablas se reemplazan y las
vistas usan CREATE OR REPLACE.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from src import db
from src.clean import limpiar

RUTA_CSV = Path("data/orders_raw.csv")


def cargar(validas: pd.DataFrame, rechazadas: pd.DataFrame, con) -> None:
    """Crea las tablas desde los DataFrames.

    DuckDB lee los DataFrames que esten en el scope de Python como si fueran
    tablas, por eso el SELECT * FROM validas funciona sin INSERT fila a fila.
    Los tipos se declaran explicitamente: fecha DATE e importe DECIMAL(10,2),
    no texto ni float — un float acumula error al sumar miles de importes.
    """
    con.register("validas", validas)
    con.register("rechazadas", rechazadas)

    con.execute("""
        CREATE OR REPLACE TABLE orders AS
        SELECT
            order_id::VARCHAR        AS order_id,
            fecha::DATE              AS fecha,
            cliente::VARCHAR         AS cliente,
            pais::VARCHAR            AS pais,
            producto::VARCHAR        AS producto,
            importe::DECIMAL(10,2)   AS importe,
            estado::VARCHAR          AS estado
        FROM validas
    """)

    con.execute("""
        CREATE OR REPLACE TABLE orders_rejected AS
        SELECT
            order_id::VARCHAR        AS order_id,
            fecha::VARCHAR           AS fecha_original,
            cliente::VARCHAR         AS cliente,
            pais::VARCHAR            AS pais,
            producto::VARCHAR        AS producto,
            importe::VARCHAR         AS importe_original,
            estado::VARCHAR          AS estado,
            motivo::VARCHAR          AS motivo
        FROM rechazadas
    """)

    con.unregister("validas")
    con.unregister("rechazadas")


def main() -> None:
    if not RUTA_CSV.exists():
        sys.exit(f"Falta {RUTA_CSV}. Ejecuta antes: python src/generate_data.py")

    # dtype=str: leemos TODO como texto a proposito. Si dejamos que pandas
    # adivine los tipos, convierte los importes vacios en NaN y los raros en
    # texto, y perdemos la pista de que habia originalmente en la celda.
    crudo = pd.read_csv(RUTA_CSV, dtype=str, keep_default_na=False)
    validas, rechazadas, informe = limpiar(crudo)

    con = db.conectar()
    try:
        cargar(validas, rechazadas, con)
        db.crear_vistas(con)
        resumen_vistas = {
            vista: con.execute(f"SELECT count(*) FROM {vista}").fetchone()[0]
            for vista in db.VISTAS_PERMITIDAS
        }
    finally:
        # Cerrar es obligatorio: DuckDB bloquea el fichero mientras haya una
        # conexion de escritura abierta, y la API no podria abrirlo despues.
        con.close()

    print(informe.texto())
    print(f"\nBase de datos: {db.RUTA_BD}")
    for vista, filas in resumen_vistas.items():
        print(f"  {vista:<22} {filas:>4} filas")


if __name__ == "__main__":
    main()
