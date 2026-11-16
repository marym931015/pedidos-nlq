"""Traduccion de pregunta en lenguaje natural a SQL, con Gemini.

Toda la interaccion con el modelo esta aqui dentro y detras de una funcion,
`generar_sql`, para que los tests puedan sustituirla sin tocar la API y sin
hacer ni una sola llamada real al proveedor.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

MODELO = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

PLANTILLA_PROMPT = """Eres un traductor de preguntas a SQL de DuckDB.
Devuelves UNICAMENTE una consulta SELECT, sin explicaciones y sin markdown.

Solo existen estas dos vistas, no hay ninguna tabla mas:

{esquema}

Reglas:
- Solo SELECT. Nada de INSERT, UPDATE, DELETE, DROP, ATTACH ni COPY.
- Usa solo las vistas de arriba y sus columnas exactas.
- `mes` es de tipo DATE y apunta al dia 1 de cada mes.
- Los importes ya excluyen pedidos cancelados y devueltos.
- Si la pregunta no se puede responder con estas vistas, devuelve exactamente: NO_RESPONDIBLE

Pregunta: {pregunta}
SQL:"""


class LlmNoDisponible(Exception):
    """No hay clave, el proveedor falla o la red esta caida. -> HTTP 503."""


class PreguntaFueraDeAlcance(Exception):
    """El modelo dice que la pregunta no se responde con estas vistas. -> HTTP 422."""


def formatear_esquema(esquema: dict[str, list[dict[str, str]]]) -> str:
    """Convierte el DESCRIBE de DuckDB en algo legible dentro del prompt.

    El esquema se inyecta en cada llamada en vez de escribirlo a mano en el
    prompt: si manana cambia una vista, el prompt se actualiza solo y el modelo
    no inventa columnas que ya no existen.
    """
    bloques = []
    for vista, columnas in esquema.items():
        campos = ", ".join(f"{c['columna']} {c['tipo']}" for c in columnas)
        bloques.append(f"- {vista}({campos})")
    return "\n".join(bloques)


def limpiar_respuesta(texto: str) -> str:
    """Quita el envoltorio markdown. Gemini devuelve casi siempre ```sql ... ```
    aunque el prompt lo prohiba, asi que se normaliza en vez de confiar."""
    limpio = texto.strip()
    bloque = re.search(r"```(?:sql)?\s*(.+?)```", limpio, re.S | re.I)
    if bloque:
        limpio = bloque.group(1)
    return limpio.strip().rstrip(";").strip()


def generar_sql(pregunta: str, esquema: dict[str, list[dict[str, str]]]) -> str:
    """Pregunta -> SQL. Lanza LlmNoDisponible o PreguntaFueraDeAlcance.

    Lo que devuelve NO es de fiar todavia: pasa por guard.validar antes de
    tocar la base de datos.
    """
    clave = os.getenv("GEMINI_API_KEY", "").strip()
    if not clave:
        # Que falte la clave no tumba el servicio: /health y /schema siguen
        # respondiendo y solo /ask devuelve 503. Asi el proyecto se puede
        # clonar y evaluar sin tener una clave de Gemini.
        raise LlmNoDisponible("GEMINI_API_KEY no esta configurada.")

    try:
        from google import genai  # import local: sin clave ni se importa

        cliente = genai.Client(api_key=clave)
        respuesta = cliente.models.generate_content(
            model=MODELO,
            contents=PLANTILLA_PROMPT.format(
                esquema=formatear_esquema(esquema), pregunta=pregunta
            ),
        )
        texto = (respuesta.text or "").strip()
    except Exception as err:
        # Se envuelve cualquier fallo del proveedor (red, cuota, modelo
        # inexistente) en un error propio: la API no debe filtrar al cliente
        # el stack trace ni nada que pueda contener la clave.
        raise LlmNoDisponible(f"Fallo al consultar el modelo: {type(err).__name__}") from err

    if not texto:
        raise LlmNoDisponible("El modelo devolvio una respuesta vacia.")

    sql = limpiar_respuesta(texto)
    if "NO_RESPONDIBLE" in sql.upper():
        raise PreguntaFueraDeAlcance(
            "La pregunta no se puede responder con las vistas disponibles."
        )
    return sql
