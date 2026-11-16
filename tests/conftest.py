"""Base de datos de prueba: pequena, en un fichero temporal y con las vistas
reales de views.sql. Los tests de la API nunca tocan la BD de desarrollo."""

import datetime as dt

import pytest

FILAS = [
    ("ORD-1", dt.date(2024, 3, 5), "Ana Garcia", "España", "Teclado", 100.00, "entregado"),
    ("ORD-2", dt.date(2024, 3, 20), "Ana Garcia", "España", "Monitor", 250.50, "enviado"),
    ("ORD-3", dt.date(2024, 3, 25), "Luis Lopez", "Francia", "Raton", 30.00, "entregado"),
    # Cancelado: existe en la tabla pero las vistas no deben sumarlo.
    ("ORD-4", dt.date(2024, 4, 2), "Luis Lopez", "España", "Webcam", 999.00, "cancelado"),
]


@pytest.fixture
def bd_temporal(tmp_path, monkeypatch):
    import duckdb

    from src import db

    ruta = tmp_path / "test.duckdb"
    con = duckdb.connect(str(ruta))
    con.execute("""
        CREATE TABLE orders (
            order_id VARCHAR, fecha DATE, cliente VARCHAR, pais VARCHAR,
            producto VARCHAR, importe DECIMAL(10,2), estado VARCHAR
        )
    """)
    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", FILAS)
    db.crear_vistas(con)
    con.close()

    # La API abre db.RUTA_BD al arrancar; se redirige al fichero temporal.
    monkeypatch.setattr(db, "RUTA_BD", ruta)
    return ruta
