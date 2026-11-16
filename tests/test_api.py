"""Test 3: el endpoint /ask con el LLM simulado.

Ni un solo test llama a Gemini de verdad: `generar_sql` se sustituye en cada
caso. Asi los tests pasan sin GEMINI_API_KEY, que es condicion para que quien
evalue el proyecto pueda ejecutarlos recien clonado.
"""

import pytest
from fastapi.testclient import TestClient

from src import api, llm


@pytest.fixture
def cliente(bd_temporal):
    with TestClient(api.app) as c:
        yield c


def simular_llm(monkeypatch, respuesta=None, excepcion=None):
    """Sustituye la llamada al modelo por una respuesta fija."""
    def falso(pregunta, esquema, contexto=None):
        if excepcion:
            raise excepcion
        return respuesta
    monkeypatch.setattr(api.llm, "generar_sql", falso)


def test_pregunta_valida_devuelve_filas(cliente, monkeypatch):
    simular_llm(monkeypatch, "SELECT pais, importe_total FROM v_ventas_mes_pais")
    r = cliente.post("/ask", json={"question": "¿Cuánto vendimos por país?"})

    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["row_count"] == len(cuerpo["rows"])
    assert cuerpo["rows"], "la BD de prueba tiene ventas, no deberia venir vacio"
    # El SQL ejecutado se devuelve siempre: la respuesta es auditable.
    assert "v_ventas_mes_pais" in cuerpo["sql"]
    assert "LIMIT" in cuerpo["sql"].upper()


def test_las_vistas_excluyen_los_cancelados(cliente, monkeypatch):
    simular_llm(monkeypatch, "SELECT sum(importe_total) AS total FROM v_ventas_mes_pais")
    r = cliente.post("/ask", json={"question": "total vendido"})
    # 100 + 250.50 + 30 = 380.50; el pedido cancelado de 999 no cuenta.
    assert r.json()["rows"][0]["total"] == pytest.approx(380.50)


def test_llm_caido_devuelve_503(cliente, monkeypatch):
    simular_llm(monkeypatch, excepcion=llm.LlmNoDisponible("GEMINI_API_KEY no esta configurada."))
    r = cliente.post("/ask", json={"question": "¿Cuánto vendimos en marzo?"})

    assert r.status_code == 503
    assert r.json()["error"] == "llm_unavailable"


def test_respuesta_del_llm_que_no_es_sql_devuelve_422(cliente, monkeypatch):
    simular_llm(monkeypatch, "Lo siento, no puedo ayudarte con eso.")
    r = cliente.post("/ask", json={"question": "¿Qué tiempo hace?"})

    assert r.status_code == 422
    assert r.json()["error"] == "sql_no_parseable"


def test_sql_contra_tabla_prohibida_devuelve_400(cliente, monkeypatch):
    # El caso que importa: el modelo devuelve SQL perfectamente valido, pero
    # contra la tabla base. La barrera no es el LLM, es el validador.
    simular_llm(monkeypatch, "SELECT * FROM orders")
    r = cliente.post("/ask", json={"question": "dame todos los pedidos crudos"})

    assert r.status_code == 400
    assert r.json()["error"] == "tabla_no_permitida"


def test_intento_de_escritura_devuelve_400(cliente, monkeypatch):
    simular_llm(monkeypatch, "DROP TABLE orders")
    r = cliente.post("/ask", json={"question": "borra la tabla"})

    assert r.status_code == 400
    assert r.json()["error"] == "solo_lectura"


def test_pregunta_vacia_la_rechaza_pydantic(cliente):
    # No llega ni al LLM: la validacion de entrada ahorra una llamada de pago.
    assert cliente.post("/ask", json={"question": "ab"}).status_code == 422


def test_health_y_schema_funcionan_sin_llm(cliente):
    assert cliente.get("/health").json()["status"] == "ok"
    vistas = cliente.get("/schema").json()["vistas"]
    assert set(vistas) == {"v_ventas_mes_pais", "v_top_clientes"}
