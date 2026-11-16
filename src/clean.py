"""Limpieza de los pedidos crudos. Sin dependencias de base de datos: recibe un
DataFrame y devuelve (filas validas, filas rechazadas, informe de calidad).

Principio: ninguna fila desaparece en silencio. Lo que no se puede arreglar va a
`rechazadas` con el motivo, y el informe dice cuantas y por que.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict

import pandas as pd

COLUMNAS = ["order_id", "fecha", "cliente", "pais", "producto", "importe", "estado"]

# Formatos que SI intentamos recuperar, en orden de preferencia. El ISO va primero
# porque es el mayoritario; dayfirst antes que monthfirst porque los datos son
# europeos y "03-15-2024" solo encaja como US (mes 15 no existe).
FORMATOS_FECHA = ["%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%m-%d-%Y"]

# Paises canonicos. La clave es el nombre sin acentos, sin espacios y en minusculas,
# asi " ESPAÑA " y "españa" caen en la misma entrada.
PAISES_CANONICOS = {
    "espana": "España",
    "portugal": "Portugal",
    "francia": "Francia",
    "italia": "Italia",
    "alemania": "Alemania",
    "mexico": "México",
}

ESTADOS_VALIDOS = {"entregado", "enviado", "pendiente", "cancelado", "devuelto"}

_ACENTOS = str.maketrans("áàäéèëíìïóòöúùüñç", "aaaeeeiiiooouuunc")


@dataclass
class Informe:
    """Lo que el ETL arreglo. Se imprime al final y los tests lo comprueban."""

    filas_leidas: int = 0
    duplicados_eliminados: int = 0
    fechas_corregidas: int = 0
    fechas_rechazadas: int = 0
    importes_corregidos: int = 0
    importes_nulos: int = 0
    importes_rechazados: int = 0
    paises_normalizados: int = 0
    clientes_normalizados: int = 0
    estados_rechazados: int = 0
    filas_validas: int = 0
    filas_rechazadas: int = 0

    def texto(self) -> str:
        etiquetas = {
            "filas_leidas": "Filas leidas del CSV",
            "duplicados_eliminados": "Duplicados eliminados (mismo order_id)",
            "fechas_corregidas": "Fechas reparadas (formato no ISO)",
            "fechas_rechazadas": "Fechas irrecuperables -> rechazadas",
            "importes_corregidos": "Importes reparados (simbolo o coma decimal)",
            "importes_nulos": "Importes vacios -> NULL (no 0)",
            "importes_rechazados": "Importes negativos -> rechazados",
            "paises_normalizados": "Paises normalizados",
            "clientes_normalizados": "Clientes normalizados",
            "estados_rechazados": "Estados desconocidos -> rechazados",
            "filas_validas": "FILAS CARGADAS en orders",
            "filas_rechazadas": "FILAS EN orders_rejected",
        }
        datos = asdict(self)
        ancho = max(len(v) for v in etiquetas.values())
        lineas = ["", "INFORME DE CALIDAD", "-" * (ancho + 8)]
        lineas += [f"{etiquetas[k]:<{ancho}}  {datos[k]:>5}" for k in etiquetas]
        return "\n".join(lineas)


def _sin_acentos(texto: str) -> str:
    return texto.strip().lower().translate(_ACENTOS)


def normalizar_texto(valor) -> str | None:
    """Quita espacios sobrantes y colapsa los internos. Capitaliza cada palabra."""
    if pd.isna(valor):
        return None
    limpio = re.sub(r"\s+", " ", str(valor)).strip()
    return limpio.title() if limpio else None


def normalizar_pais(valor) -> str | None:
    """Mapea a un nombre canonico. Si no esta en la tabla, devuelve el valor
    capitalizado en vez de descartarlo: un pais nuevo no es un error de datos."""
    if pd.isna(valor):
        return None
    clave = _sin_acentos(str(valor))
    if not clave:
        return None
    return PAISES_CANONICOS.get(clave, normalizar_texto(valor))


def normalizar_importe(valor) -> float | None:
    """Convierte el importe a float. Devuelve None si esta vacio.

    Casos que cubre: "374.64", "374.64 €", "1.234,56", "1234,56", "   ", "".
    Regla para los separadores: el ultimo separador que aparece es el decimal,
    el otro es de miles. Es la unica interpretacion que no rompe "1.234,56".
    """
    if pd.isna(valor):
        return None
    texto = str(valor).replace("€", "").replace("\xa0", " ").strip()
    if not texto:
        return None

    tiene_punto, tiene_coma = "." in texto, "," in texto
    if tiene_punto and tiene_coma:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        texto = texto.replace(miles, "").replace(decimal, ".")
    elif tiene_coma:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        # No es un numero en absoluto; se trata como ausente y la fila se rechaza
        # mas arriba si hace falta.
        return None


def _parsear_fechas(serie: pd.Series) -> pd.Series:
    """Prueba los formatos conocidos en cascada: cada pasada solo toca lo que
    sigue sin parsear. Lo que no encaja en ninguno queda como NaT."""
    resultado = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    for formato in FORMATOS_FECHA:
        pendientes = resultado.isna()
        if not pendientes.any():
            break
        resultado[pendientes] = pd.to_datetime(
            serie[pendientes], format=formato, errors="coerce"
        )
    return resultado


def limpiar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, Informe]:
    """Limpia el DataFrame crudo. Devuelve (validas, rechazadas, informe)."""
    informe = Informe(filas_leidas=len(df))
    df = df.copy()

    # 1. Duplicados: por order_id, nos quedamos con la primera aparicion.
    #    Es arbitrario pero deterministico, y las copias son identicas en origen.
    antes = len(df)
    df = df.drop_duplicates(subset="order_id", keep="first")
    informe.duplicados_eliminados = antes - len(df)

    # 2. Fechas. Contamos como "corregida" toda la que no venia ya en ISO.
    crudas = df["fecha"].astype("string").fillna("").str.strip()
    df["fecha"] = _parsear_fechas(crudas)
    era_iso = crudas.str.fullmatch(r"\d{4}-\d{2}-\d{2}").fillna(False)
    informe.fechas_corregidas = int((df["fecha"].notna() & ~era_iso).sum())

    # 3. Importes. Vacio -> NULL (un 0 falsearia los agregados). Negativo -> rechazo.
    crudos = df["importe"].astype("string").fillna("")
    df["importe"] = crudos.map(normalizar_importe)
    era_canonico = crudos.str.fullmatch(r"\d+\.\d{2}").fillna(False)
    informe.importes_corregidos = int((df["importe"].notna() & ~era_canonico).sum())
    informe.importes_nulos = int(df["importe"].isna().sum())

    # 4. Texto: pais canonico, cliente y producto sin ruido, estado en minusculas.
    pais_original, cliente_original = df["pais"].copy(), df["cliente"].copy()
    df["pais"] = df["pais"].map(normalizar_pais)
    df["cliente"] = df["cliente"].map(normalizar_texto)
    df["producto"] = df["producto"].map(normalizar_texto)
    df["estado"] = df["estado"].astype("string").str.strip().str.lower()
    informe.paises_normalizados = int((df["pais"] != pais_original).sum())
    informe.clientes_normalizados = int((df["cliente"] != cliente_original).sum())

    # 5. Reparto entre validas y rechazadas, con el motivo siempre explicito.
    motivos = pd.Series("", index=df.index, dtype="string")
    motivos[df["fecha"].isna()] = "fecha_irrecuperable"
    negativos = df["importe"].notna() & (df["importe"] < 0)
    motivos[negativos & (motivos == "")] = "importe_negativo"
    estado_malo = ~df["estado"].isin(ESTADOS_VALIDOS)
    motivos[estado_malo & (motivos == "")] = "estado_desconocido"

    informe.fechas_rechazadas = int((motivos == "fecha_irrecuperable").sum())
    informe.importes_rechazados = int((motivos == "importe_negativo").sum())
    informe.estados_rechazados = int((motivos == "estado_desconocido").sum())

    rechazadas = df[motivos != ""].copy()
    rechazadas["motivo"] = motivos[motivos != ""]
    # La fecha se guarda como texto en rechazadas: justamente puede ser invalida.
    rechazadas["fecha"] = rechazadas["fecha"].astype("string").fillna("(invalida)")
    validas = df[motivos == ""].copy()
    validas["fecha"] = validas["fecha"].dt.date

    informe.filas_validas = len(validas)
    informe.filas_rechazadas = len(rechazadas)
    return validas, rechazadas, informe
