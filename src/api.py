"""API FastAPI: pregunta en lenguaje natural -> SQL validado -> resultado.

El flujo completo y donde esta cada barrera:

    pregunta -> llm.generar_sql -> guard.validar -> conexion read_only -> filas
                      |                  |                 |
                 503 si falla      400/422 si el SQL    la BD no deja
                                   no es admisible      escribir ni aunque
                                                        lo anterior fallara
"""

from __future__ import annotations

import datetime as dt
import decimal
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src import db, guard, llm

estado: dict[str, Any] = {"con": None, "esquema": {}}


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Una sola conexion de solo lectura para todo el proceso.

    Abrirla al arrancar tiene dos efectos buenos: no se paga el coste por
    peticion, y si la base de datos no existe el fallo aparece al levantar el
    servidor y no a mitad de una consulta.
    """
    con = db.conectar(solo_lectura=True)
    estado["con"] = con
    estado["esquema"] = db.esquema_vistas(con)
    try:
        yield
    finally:
        con.close()


app = FastAPI(
    title="Pedidos NLQ",
    description="Consulta de pedidos en lenguaje natural sobre DuckDB.",
    version="1.0.0",
    lifespan=ciclo_de_vida,
)


class Pregunta(BaseModel):
    question: str = Field(
        min_length=3,
        max_length=500,
        description="Pregunta en lenguaje natural",
        examples=["¿Cuánto vendimos en España en marzo?"],
    )


def _serializable(valor: Any) -> Any:
    """DuckDB devuelve Decimal y date, que json no sabe serializar."""
    if isinstance(valor, decimal.Decimal):
        return float(valor)
    if isinstance(valor, (dt.date, dt.datetime)):
        return valor.isoformat()
    return valor


def _error(codigo_http: int, error: str, detalle: str) -> JSONResponse:
    """Formato unico de error. Nunca incluye trazas ni valores de entorno."""
    return JSONResponse(status_code=codigo_http, content={"error": error, "detail": detalle})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "vistas": list(db.VISTAS_PERMITIDAS)}


@app.get("/schema")
def schema() -> dict[str, Any]:
    """Lo que el LLM ve. Util para depurar una respuesta rara del modelo."""
    return {"vistas": estado["esquema"]}


@app.post("/ask")
def ask(peticion: Pregunta):
    # 1. Pregunta -> SQL. Si el proveedor falla, el servicio sigue vivo.
    try:
        sql_crudo = llm.generar_sql(peticion.question, estado["esquema"])
    except llm.LlmNoDisponible as err:
        return _error(503, "llm_unavailable", str(err))
    except llm.PreguntaFueraDeAlcance as err:
        return _error(422, "question_out_of_scope", str(err))

    # 2. Validacion. El SQL del modelo no llega a la base de datos sin pasar.
    try:
        sql = guard.validar(sql_crudo)
    except guard.SqlNoValido as err:
        # 400 cuando el modelo pidio algo prohibido (una tabla que no toca),
        # 422 cuando lo que devolvio no es una consulta utilizable.
        codigo = 400 if err.motivo in {"tabla_no_permitida", "solo_lectura", "funcion_no_permitida"} else 422
        return _error(codigo, err.motivo, err.detalle)

    # 3. Ejecucion sobre la conexion de solo lectura.
    try:
        # Cursor propio por peticion: la conexion de DuckDB no es segura para
        # usarse desde varios hilos a la vez, y FastAPI atiende en un threadpool.
        # (DuckDB no tiene statement_timeout; la contencion aqui es el LIMIT que
        # fuerza el validador.)
        con = estado["con"].cursor()
        resultado = con.execute(sql)
        columnas = [c[0] for c in resultado.description]
        filas = [
            {col: _serializable(val) for col, val in zip(columnas, fila)}
            for fila in resultado.fetchall()
        ]
    except Exception as err:
        return _error(400, "sql_execution_error", f"No se pudo ejecutar la consulta: {type(err).__name__}")

    return {
        "question": peticion.question,
        "sql": sql,          # siempre visible: es auditable que se ejecuto
        "row_count": len(filas),
        "rows": filas,
    }
