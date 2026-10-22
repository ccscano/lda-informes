#!/usr/bin/env python3
"""
Excel de explotación (contabilidad) → tabla `explotacion` del histórico.

El Excel tiene una hoja por año con la cuenta de explotación de cada sección por trimestre
(columnas 1T..4T): CONFITERIA (= Tienda), CAFETERIA y CHARCUTERIA de CENTRO (= Paseo), PLAYA,
RAMBLA, TORRECARDENAS y AGUADULCE, con Ventas (sin IVA), Compras, Reparto Obrador, Var. Existencias,
Sueldos y Salarios, Gratificaciones, Seg.Soc., resultados… Se guarda tal cual, una fila por
año, trimestre, local, área y concepto; la app saca de ahí el coste de personal y el margen bruto.

Uso: explotacion.py fichero.xlsx       (también se sube desde la app)
"""
import re
import sqlite3
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico

LOCAL = {"CENTRO": "Paseo", "PLAYA": "Playa", "RAMBLA": "Rambla", "TORRECARDENAS": "Torrecárdenas",
         "AGUADULCE": "Aguadulce", "AGUDULCE": "Aguadulce"}
AREA = {"CONFITERIA": "Tienda", "CAFETERIA": "Cafetería", "CHARCUTERIA": "Charcutería"}
# Conceptos con nombre fijo para la app (el Excel tiene alguna errata, p. ej. «Repartto Obrador»)
CONCEPTO = {"repartto obrador": "Reparto Obrador"}
PERSONAL = ("Sueldos y Salarios", "Gratificaciones", "Seg.Soc.")
# Coste de lo vendido; «Otras Secciones» (negativo) es lo que la sección pasa a otras. Con esto el
# margen bruto coincide con la hoja «Ratios» del Excel.
COSTE_VENTAS = ("Compras", "Compras B", "Reparto Obrador", "Var. Existencias", "Otras Secciones")
TRIMESTRE = re.compile(r"([1-4])T(\d\d)")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS explotacion (
    anio INTEGER, trim INTEGER, local TEXT, area TEXT, concepto TEXT, importe REAL,
    PRIMARY KEY (anio, trim, local, area, concepto));
"""


def leer(fichero):
    """[(año, trimestre, local, área, concepto, importe)] de todas las hojas anuales."""
    wb = openpyxl.load_workbook(fichero, data_only=True, read_only=True)
    filas = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        columnas = {j: TRIMESTRE.fullmatch(c) for j, c in enumerate(rows[0][:10])
                    if isinstance(c, str) and TRIMESTRE.fullmatch(c.strip())}
        if not columnas:
            continue   # no es una hoja anual (Análisis, Ratios…)
        seccion = None
        for r in rows[1:]:
            etiqueta = r[0].strip() if isinstance(r[0], str) else None
            if not etiqueta:
                continue
            partes = etiqueta.split()
            if etiqueta.isupper() and len(partes) == 2 and partes[0] in AREA and partes[1] in LOCAL:
                seccion = (LOCAL[partes[1]], AREA[partes[0]])
                continue
            if etiqueta.isupper() and not etiqueta.startswith("RESULTADO"):
                seccion = None   # otra sección (OBRADOR, GASTOS …): no es de un local y área
                continue
            if not seccion:
                continue
            concepto = CONCEPTO.get(etiqueta.lower(), etiqueta)
            for j, m in columnas.items():
                v = r[j]
                if isinstance(v, (int, float)):
                    filas.append((2000 + int(m.group(2)), int(m.group(1)), *seccion, concepto, float(v)))
            if etiqueta == "RESULTADO TOTAL":
                seccion = None
    return filas


def importar(fichero):
    filas = leer(fichero)
    if not filas:
        raise ValueError("El Excel no tiene hojas con trimestres (1T..4T) y secciones de local")
    con = historico.conectar()
    con.executescript(ESQUEMA)
    with con:
        # Se reemplaza entero cada año que trae el Excel (las cifras se revisan a posteriori)
        for anio in {f[0] for f in filas}:
            con.execute("DELETE FROM explotacion WHERE anio = ?", (anio,))
        con.executemany("INSERT OR REPLACE INTO explotacion VALUES (?, ?, ?, ?, ?, ?)", filas)
    trimestres = sorted({(f[0], f[1]) for f in filas})
    con.close()
    return {"filas": len(filas), "desde": f"{trimestres[0][1]}T{trimestres[0][0]}",
            "hasta": f"{trimestres[-1][1]}T{trimestres[-1][0]}"}


if __name__ == "__main__":
    print(importar(sys.argv[1]))
