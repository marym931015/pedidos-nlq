"""Genera data/orders_raw.csv: 1.200 pedidos con errores introducidos a proposito.

La semilla es fija, asi que quien clone el repo obtiene exactamente el mismo CSV
y los mismos conteos que afirma el README. Los errores estan contados para que
tests/test_cleaning.py pueda comprobar que el ETL los corrige.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEMILLA = 42
N_LIMPIAS = 1200          # filas validas antes de inyectar errores
N_DUPLICADAS = 40         # filas duplicadas exactas
N_FECHAS_MALAS = 50       # fechas en formato raro o imposible
N_IMPORTES_MALOS = 60     # importes vacios, con simbolo, coma decimal o negativos
N_PAIS_SUCIO = 70         # mayusculas/espacios inconsistentes en pais y cliente

RUTA_CSV = Path("data/orders_raw.csv")
RUTA_MUESTRA = Path("data/sample_orders.csv")
COLUMNAS = ["order_id", "fecha", "cliente", "pais", "producto", "importe", "estado"]

PAISES = ["España", "Portugal", "Francia", "Italia", "Alemania", "México"]
PRODUCTOS = ["Teclado", "Monitor 24\"", "Portatil", "Raton", "Webcam", "Auriculares", "Dock USB-C"]
ESTADOS = ["entregado", "enviado", "pendiente", "cancelado", "devuelto"]
NOMBRES = ["Ana", "Luis", "Marta", "Carlos", "Sofia", "Javier", "Lucia", "Diego", "Elena", "Pablo"]
APELLIDOS = ["Garcia", "Lopez", "Martinez", "Sanchez", "Romero", "Torres", "Vidal", "Nunez"]

INICIO = date(2024, 1, 1)
FIN = date(2024, 12, 31)


def fila_valida(rng: random.Random, order_id: int) -> dict:
    """Una fila bien formada: fecha ISO, importe con punto decimal, pais canonico."""
    fecha = INICIO + timedelta(days=rng.randint(0, (FIN - INICIO).days))
    return {
        "order_id": f"ORD-{order_id:05d}",
        "fecha": fecha.isoformat(),
        "cliente": f"{rng.choice(NOMBRES)} {rng.choice(APELLIDOS)}",
        "pais": rng.choice(PAISES),
        "producto": rng.choice(PRODUCTOS),
        "importe": f"{rng.uniform(9.99, 1499.0):.2f}",
        # los estados no son equiprobables: la mayoria de pedidos se entregan
        "estado": rng.choices(ESTADOS, weights=[55, 20, 12, 8, 5])[0],
    }


def ensuciar_fecha(rng: random.Random, fecha_iso: str) -> str:
    """Devuelve la fecha en un formato problematico. Algunos son recuperables, otros no."""
    anio, mes, dia = fecha_iso.split("-")
    variantes = [
        f"{dia}/{mes}/{anio}",      # recuperable: formato europeo
        f"{mes}-{dia}-{anio}",      # recuperable: formato US
        f"{dia}.{mes}.{anio}",      # recuperable: separador con puntos
        "2024-13-45",               # irrecuperable: mes y dia imposibles
        "",                         # irrecuperable: vacia
        "fecha desconocida",        # irrecuperable: texto libre
    ]
    return rng.choice(variantes)


def ensuciar_importe(rng: random.Random, importe: str) -> str:
    """Devuelve el importe en un formato problematico."""
    valor = float(importe)
    variantes = [
        "",                                        # vacio -> NULL en el ETL (no 0)
        f"{valor:.2f} €",                          # con simbolo de moneda
        f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),  # 1.234,56
        f"-{valor:.2f}",                           # negativo: imposible en un pedido
        "   ",                                     # solo espacios
    ]
    return rng.choice(variantes)


def generar() -> list[dict]:
    rng = random.Random(SEMILLA)
    filas = [fila_valida(rng, i) for i in range(1, N_LIMPIAS + 1)]

    # Los indices se eligen sin solapamiento para que cada fila tenga como maximo
    # un tipo de error: asi el informe de calidad del ETL es comprobable fila a fila.
    indices = rng.sample(range(N_LIMPIAS), N_FECHAS_MALAS + N_IMPORTES_MALOS + N_PAIS_SUCIO)
    corte_f = N_FECHAS_MALAS
    corte_i = corte_f + N_IMPORTES_MALOS

    for i in indices[:corte_f]:
        filas[i]["fecha"] = ensuciar_fecha(rng, filas[i]["fecha"])

    for i in indices[corte_f:corte_i]:
        filas[i]["importe"] = ensuciar_importe(rng, filas[i]["importe"])

    for i in indices[corte_i:]:
        filas[i]["pais"] = rng.choice([
            filas[i]["pais"].upper(),
            filas[i]["pais"].lower(),
            f"  {filas[i]['pais']} ",
        ])
        filas[i]["cliente"] = f" {filas[i]['cliente'].upper()}"

    # Duplicados exactos: se copian filas ya existentes (mismo order_id incluido)
    # y se insertan en posiciones aleatorias, no al final, para que el ETL no
    # pueda deduplicar por orden de aparicion.
    for fila in rng.sample(filas, N_DUPLICADAS):
        filas.insert(rng.randint(0, len(filas)), dict(fila))

    return filas


def escribir(ruta: Path, filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)


def main() -> None:
    filas = generar()
    escribir(RUTA_CSV, filas)
    # Muestra versionada: 20 filas para poder ver los datos sucios en GitHub
    # sin ejecutar nada. El CSV completo esta en .gitignore.
    escribir(RUTA_MUESTRA, filas[:20])

    print(f"CSV generado: {RUTA_CSV} ({len(filas)} filas, semilla {SEMILLA})")
    print(f"  filas base .............. {N_LIMPIAS}")
    print(f"  duplicados exactos ...... {N_DUPLICADAS}")
    print(f"  fechas con formato malo . {N_FECHAS_MALAS}")
    print(f"  importes con formato malo {N_IMPORTES_MALOS}")
    print(f"  pais/cliente sin limpiar  {N_PAIS_SUCIO}")
    print(f"Muestra versionada: {RUTA_MUESTRA} (20 filas)")


if __name__ == "__main__":
    main()
