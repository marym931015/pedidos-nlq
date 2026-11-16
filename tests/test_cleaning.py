"""Test 1: la limpieza. Entrada minima con los errores a mano y conteos exactos."""

import pandas as pd
import pytest

from src.clean import limpiar, normalizar_importe, normalizar_pais

COLUMNAS = ["order_id", "fecha", "cliente", "pais", "producto", "importe", "estado"]


def df(filas):
    return pd.DataFrame(filas, columns=COLUMNAS)


@pytest.fixture
def datos_sucios():
    """8 filas: 1 duplicada, 2 fechas raras (una recuperable), 1 fecha imposible,
    1 importe vacio, 1 con coma decimal y simbolo, 1 negativo, 1 pais sucio."""
    return df([
        ["ORD-1", "2024-03-15", "Ana Garcia", "España", "Teclado", "100.00", "entregado"],
        ["ORD-1", "2024-03-15", "Ana Garcia", "España", "Teclado", "100.00", "entregado"],
        ["ORD-2", "15/03/2024", "Luis Lopez", "  ESPAÑA ", "Monitor", "250.50", "enviado"],
        ["ORD-3", "2024-13-45", "Marta Ruiz", "Francia", "Raton", "30.00", "entregado"],
        ["ORD-4", "2024-04-01", " PEDRO SOLA", "mexico", "Webcam", "", "pendiente"],
        ["ORD-5", "2024-04-02", "Sara Vega", "Italia", "Dock", "1.234,56 €", "entregado"],
        ["ORD-6", "2024-04-03", "Iker Mas", "Portugal", "Portatil", "-99.00", "entregado"],
        ["ORD-7", "fecha desconocida", "Noa Gil", "Alemania", "Teclado", "45.00", "entregado"],
    ])


def test_elimina_duplicados(datos_sucios):
    _, _, informe = limpiar(datos_sucios)
    assert informe.duplicados_eliminados == 1
    assert informe.filas_leidas == 8


def test_fechas_malas_van_a_rechazadas_con_motivo(datos_sucios):
    validas, rechazadas, informe = limpiar(datos_sucios)

    # 2024-13-45 y "fecha desconocida" no se pueden salvar: se rechazan, no se tiran.
    assert informe.fechas_rechazadas == 2
    rechazadas_por_fecha = rechazadas[rechazadas["motivo"] == "fecha_irrecuperable"]
    assert set(rechazadas_por_fecha["order_id"]) == {"ORD-3", "ORD-7"}

    # 15/03/2024 si es recuperable y acaba como fecha real en las validas.
    assert informe.fechas_corregidas == 1
    assert str(validas.set_index("order_id").loc["ORD-2", "fecha"]) == "2024-03-15"


def test_importes_vacios_son_null_y_negativos_se_rechazan(datos_sucios):
    validas, rechazadas, informe = limpiar(datos_sucios)

    # Vacio -> NULL, nunca 0: un 0 falsearia las sumas de las vistas.
    assert informe.importes_nulos == 1
    assert pd.isna(validas.set_index("order_id").loc["ORD-4", "importe"])

    assert informe.importes_rechazados == 1
    assert rechazadas.set_index("order_id").loc["ORD-6", "motivo"] == "importe_negativo"


def test_normaliza_pais_y_cliente(datos_sucios):
    validas, _, _ = limpiar(datos_sucios)
    por_id = validas.set_index("order_id")
    assert por_id.loc["ORD-2", "pais"] == "España"      # "  ESPAÑA " -> canonico
    assert por_id.loc["ORD-4", "pais"] == "México"      # "mexico" -> con acento
    assert por_id.loc["ORD-4", "cliente"] == "Pedro Sola"


def test_ninguna_fila_se_pierde_en_silencio(datos_sucios):
    validas, rechazadas, informe = limpiar(datos_sucios)
    # Toda fila que entra o sale limpia, o sale rechazada con motivo, o era duplicada.
    assert len(validas) + len(rechazadas) + informe.duplicados_eliminados == informe.filas_leidas
    assert (rechazadas["motivo"] != "").all()


@pytest.mark.parametrize(
    "crudo, esperado",
    [
        ("374.64", 374.64),
        ("374.64 €", 374.64),
        ("1.234,56", 1234.56),   # formato europeo: punto de miles, coma decimal
        ("1234,56", 1234.56),
        ("", None),
        ("   ", None),
        ("no es un numero", None),
    ],
)
def test_normalizar_importe(crudo, esperado):
    assert normalizar_importe(crudo) == esperado


@pytest.mark.parametrize("crudo", ["España", " ESPAÑA ", "espana", "ESPANA"])
def test_normalizar_pais_colapsa_variantes(crudo):
    assert normalizar_pais(crudo) == "España"
