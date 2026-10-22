#!/usr/bin/env python3
"""
Los 10 productos más vendidos del día por Telegram (HiOffice / Hiopos).

Se ejecuta en el VPS a las 15:05 y a las 22:15, justo después de las ventas.
Una sola consulta al bloque "Ventas → Por artículos" con la columna
Establecimiento añadida devuelve artículo × establecimiento; de ahí salen:
  - top 10 de cada establecimiento (cafeterías, tiendas, charcutería);
  - top 10 de cada local (cafetería + tienda + …);
  - top 10 de la empresa.

Uso: productos.py [AAAA-MM-DD] [--no-enviar] [--solo-locales]
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presencia as base
import ventas

DASHBOARD, BLOCK, DATASOURCE = 118, 50821, 250
TOP = 10
# (nombre, tipo, origen, ids, posición, internalId). Establecimiento no está en el bloque
# original: con internalId 99 el servidor la acepta y agrupa por ella.
COLUMNAS = [
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 0, 99),
    ("Artículo", "String", "Attribute", {"attributeId": 2}, 2, 2),
    ("Subartículo", "String", "Attribute", {"attributeId": 1555}, 4, 11),
    ("Uds.V", "BigDecimal", "Metric", {"metricId": 1352}, 6, 4),
    ("Venta", "BigDecimal", "Metric", {"metricId": 1353}, 8, 5),
    ("Docs", "BigDecimal", "Metric", {"metricId": 1355}, 11, 7),
]
EXCLUIR = ("descuento",)   # líneas que no son productos


def columna(nombre, tipo, origen, ids, pos, internal_id):
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": True, "columnType": 1,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def articulos_dia(token, dia, hasta=None):
    """[{estab, articulo, uds, venta, docs}] del día o del rango dia..hasta."""
    hasta = hasta or dia
    f = dia.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 5000, "totals": False, "rest": False,
        "datasources": [{"id": DATASOURCE}],
        "columns": [columna(*c) for c in COLUMNAS],
        "ordenableColumns": [],
        "filters": [{"permission": False, "toDelete": False, "id": -1, "name": None,
                     "dateRange": {"initialScale": 1, "finalScale": 0, "toDate": 1, "numLast": 0,
                                   "numNext": 0, "numMaxDays": 0, "scaleOffset": 0,
                                   "comparativeType": 2, "allowComparativeDates": False,
                                   "displacement": 0, "displacementScale": 1},
                     "dateValue": f, "dateValue2": hasta.strftime("%Y/%m/%d"), "filterType": 1,
                     "profileType": None, "filterBlocks": []}],
        "actions": [], "blocksRefresh": [], "blockOperations": [], "blockBridgeExportations": [],
        "blockStyles": [], "blockStyleTypeId": 1,
        "elementId": str(int(dt.datetime.now().timestamp() * 1000)),
        "dashboard": DASHBOARD, "block": BLOCK, "linkLocation": None, "skipCache": True,
        "ignoreDates": False, "id": 1, "dateReference": hasta.isoformat(),
        "columnarCalculation": False, "refresh": False, "isDimensionSelector": False,
        "calculateSubtotals": False, "useSizeInColumn": False, "forceNoResults": False,
        "selectorType": 0, "useDistinct": False, "maxResults": None,
    }
    req = urllib.request.Request(f"{base.HIOFFICE}/ErpCloud/report/query", data=json.dumps(body).encode(),
                                 headers={"x-auth-token": token, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        datos = json.load(r)
    filas = []
    for fila in datos.get("rows") or []:
        _, estab, art, sub, uds, venta, docs = fila[:7]
        if not estab or not art or any(x in art.lower() for x in EXCLUIR):
            continue
        nombre = art.strip().title()
        if sub and sub.strip():
            nombre += f" ({sub.strip().lower()})"
        estab = estab.strip()
        estab = ventas.FUSIONAR.get(estab, estab)   # Bollería Paseo y Tienda Paseo son una misma caja
        filas.append({"estab": estab, "articulo": nombre, "uds": float(uds or 0),
                      "venta": float(venta or 0), "docs": float(docs or 0)})
    return filas


def agrupar(filas, clave):
    """Suma uds y venta por (clave(fila), artículo)."""
    res = {}
    for f in filas:
        k = clave(f)
        if k is None:
            continue
        a = res.setdefault(k, {}).setdefault(f["articulo"], {"uds": 0, "venta": 0})
        a["uds"] += f["uds"]
        a["venta"] += f["venta"]
    return res


def top(arts):
    return sorted(arts.items(), key=lambda kv: -kv[1]["venta"])[:TOP]


def uds(x):
    return f"{x:,.0f}".replace(",", ".") if x == int(x) else f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def bloque(titulo, arts, arts_ant=None):
    """arts_ant: mismo diccionario del periodo anterior; si se da, va entre paréntesis."""
    total = sum(a["venta"] for a in arts.values())
    cab = f"<b>{titulo}</b> · {ventas.eur(total)}"
    if arts_ant is not None:
        cab += f" ({ventas.eur(sum(a['venta'] for a in arts_ant.values()))})"
    lineas = [cab]
    for i, (nombre, a) in enumerate(top(arts), 1):
        pct = a["venta"] / total * 100 if total else 0
        linea = f"{i:>2}. {nombre}: {ventas.eur(a['venta'])} · {uds(a['uds'])} uds · {pct:.0f} %"
        if arts_ant is not None:
            b = arts_ant.get(nombre)
            linea += f" ({ventas.eur(b['venta'])} · {uds(b['uds'])} uds)" if b else " (sin ventas antes)"
        lineas.append(linea)
    return "\n".join(lineas)


def nombre_estab(e):
    n = ventas.NOMBRE.get(e, e.strip("* ").strip())
    return n


def mensajes(dia, hora, filas, solo_locales=False, titulo=None, filas_ant=None, nota_ant=None):
    """filas_ant: artículos del periodo anterior; si se dan, cada línea lleva su venta entre paréntesis."""
    cierre = hora >= 21
    cab = titulo or (f"🏆 <b>Top {TOP} productos {base.DIAS[dia.weekday()]} {dia:%d/%m}</b> · " + ("cierre del día" if cierre else f"hasta las {hora:02d}:00"))
    if filas_ant is not None and nota_ant:
        cab += f"\n<i>Entre paréntesis, {nota_ant}.</i>"
    ant = lambda clave, k: agrupar(filas_ant, clave).get(k, {}) if filas_ant is not None else None
    partes = []
    # 1) Empresa
    todo = lambda f: "todo"
    por_todo = agrupar(filas, todo)
    partes.append(cab + "\n\n" + bloque("🏢 TODA LA EMPRESA", por_todo.get("todo", {}), ant(todo, "todo")))
    # 2) Por local (cafetería + tienda + charcutería + bollería)
    por_loc = lambda f: ventas.local(f["estab"])
    por_local = agrupar(filas, por_loc)
    for loc, arts in sorted(por_local.items(), key=lambda kv: -sum(a["venta"] for a in kv[1].values())):
        partes.append(bloque(f"🏬 {loc.upper()} (cafetería + tienda)", arts, ant(por_loc, loc)))
    if solo_locales:
        return partes
    # 3) Por establecimiento, cafeterías primero
    por_est = lambda f: f["estab"]
    por_estab = agrupar(filas, por_est)
    orden = sorted(por_estab.items(), key=lambda kv: (ventas.grupo(kv[0]) != "cafeterias",
                                                      -sum(a["venta"] for a in kv[1].values())))
    for estab, arts in orden:
        icono = "☕" if ventas.grupo(estab) == "cafeterias" else "🛍"
        partes.append(bloque(f"{icono} {nombre_estab(estab)}", arts, ant(por_est, estab)))
    return partes


def main():
    cfg = base.cargar_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dia = dt.date.fromisoformat(args[0]) if args else dt.date.today()
    hora = dt.datetime.now().hour if dia == dt.date.today() else 23
    token = base.login(cfg)
    filas = articulos_dia(token, dia)
    partes = mensajes(dia, hora, filas, "--solo-locales" in sys.argv)
    texto = "\n\n".join(partes)
    if "--no-enviar" in sys.argv:
        print(texto)
        return
    base.telegram(cfg, texto)
    print(f"Enviado: {len(filas)} líneas de artículo, {len(partes)} bloques.")


if __name__ == "__main__":
    main()
