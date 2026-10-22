#!/usr/bin/env python3
"""
Ventas del día, total y por establecimiento, por Telegram (HiOffice / Hiopos).

Se ejecuta en el VPS a las 15:00 y a las 22:10 (systemd timer). Usa el mismo
login y el mismo .env que presencia.py y manda al canal de Telegram el bloque
"Ventas → Por establecimiento" de HiOffice: venta (IVA incluido), tickets y
ticket medio de cada establecimiento, el total, y la comparación con el mismo
día de la semana anterior.

Uso: ventas.py [AAAA-MM-DD] [--no-enviar]
"""
import datetime as dt
import json
import sys
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import presencia as base  # login, cifrado, telegram y cargar_env

# Definición del bloque "Por establecimiento" del dashboard Ventas de HiOffice
DASHBOARD, BLOCK, DATASOURCE = 118, 50826, 250
# (nombre, tipo, origen, ids, posición, internalId, columnType)
COLUMNAS = [
    ("Cód. Establecimiento", "Integer", "Attribute", {"attributeId": 1060}, 1, 12, 1),
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 2, 1, 1),
    ("Uds.V", "BigDecimal", "Metric", {"metricId": 1352}, 3, 2, 1),
    ("Base", "BigDecimal", "Metric", {"metricId": 1359}, 5, 3, 1),
    ("Impuestos", "BigDecimal", "Metric", {"metricId": 1358}, 6, 4, 1),
    ("Neto", "BigDecimal", "Metric", {"metricId": 1357}, 7, 5, 1),
    ("Docs", "BigDecimal", "Metric", {"metricId": 1355}, 10, 7, 1),
]
FUSIONAR = {"** Bolleria Paseo **": "Tienda Paseo de Almeria"}
NOMBRE = {
    "Cafeteria Paseo": "Cafetería Paseo", "Cafeteria Torrecardenas": "Cafetería Torrecárdenas",
    "Cafeteria Rambla": "Cafetería Rambla", "Cafeteria Aguadulce": "Cafetería Aguadulce",
    "Cafeteria Playa": "Cafetería Playa", "Tienda Paseo de Almeria": "Tienda Paseo de Almería",
    "Tienda Torrecardenas": "Tienda Torrecárdenas", "** Bolleria Paseo ** ": "Bollería Paseo",
    "** Bolleria Paseo **": "Bollería Paseo", "Charcuteria Paseo": "Charcutería Paseo",
}


def columna(nombre, tipo, origen, ids, pos, internal_id, col_type):
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": True, "columnType": col_type,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def ventas_dia(token, dia, hasta=None):
    """Devuelve ({establecimiento: {neto, docs, uds}}, totales) del día o del rango dia..hasta."""
    hasta = hasta or dia
    f = dia.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 100, "totals": False, "rest": False,
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
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = json.load(r)
    tiendas = {}
    for fila in datos.get("rows") or []:
        _, _cod, nombre, uds, _base, _imp, neto, docs = fila[:8]
        if nombre:
            tiendas[nombre] = {"neto": float(neto or 0), "docs": float(docs or 0), "uds": float(uds or 0)}
    # Bollería Paseo y Tienda Paseo son una única caja: se suman en Tienda Paseo
    for alias, destino in FUSIONAR.items():
        src = next((k for k in tiendas if k.strip() == alias), None)
        if src:
            t = tiendas.pop(src)
            d = tiendas.setdefault(destino, {"neto": 0, "docs": 0, "uds": 0})
            for k in d:
                d[k] += t[k]
    tot = datos.get("totalsRow") or []
    totales = {"neto": float(tot[6] or 0) if len(tot) > 6 else sum(t["neto"] for t in tiendas.values()),
               "docs": float(tot[7] or 0) if len(tot) > 7 else sum(t["docs"] for t in tiendas.values())}
    return tiendas, totales


def eur(x):
    return f"{x:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def variacion(actual, anterior, con_importe=False):
    if not anterior:
        return ""
    v = (actual - anterior) / anterior * 100
    signo = f"{'+' if v >= 0 else ''}{v:.1f} %"
    return f" ({eur(anterior)}, {signo})" if con_importe else f" ({signo})"


def local(nombre):
    """Nombre del local sin el tipo: 'Cafeteria Paseo' y 'Tienda Paseo de Almeria' → 'Paseo'."""
    n = NOMBRE.get(nombre.strip(), nombre.strip().strip("*").strip())
    for pref in ("Cafetería ", "Tienda ", "Charcutería ", "Bollería "):
        n = n.replace(pref, "")
    return n.replace(" de Almería", "")


def grupo(nombre):
    return "cafeterias" if nombre.strip().lower().startswith("cafeteria") else "tiendas"


def mensaje(dia, hora, tiendas, totales, tiendas_ant, totales_ant, titulo=None, etiqueta_ant=None,
            con_importe=False):
    cierre = hora >= 21
    if titulo is None:
        titulo = f"💶 <b>Ventas {base.DIAS[dia.weekday()]} {dia:%d/%m}</b> · " + ("cierre del día" if cierre else f"hasta las {hora:02d}:00")
    if etiqueta_ant is None:
        etiqueta_ant = f"el {base.DIAS[dia.weekday()]} pasado"
    lineas = [titulo]
    if not cierre:  # a media tarde no tiene sentido comparar con un día completo
        tiendas_ant, totales_ant = {}, {}
    for g, cab, icono in (("cafeterias", "CAFETERÍAS", "☕"), ("tiendas", "TIENDAS Y CHARCUTERÍA", "🛍")):
        items = {n: t for n, t in tiendas.items() if grupo(n) == g}
        if not items:
            continue
        neto = sum(t["neto"] for t in items.values())
        docs = sum(t["docs"] for t in items.values())
        ant = sum(tiendas_ant.get(n, {}).get("neto", 0) for n in items) if tiendas_ant else 0
        lineas += ["", f"{icono} <b>{cab}: {eur(neto)}</b>{variacion(neto, ant, con_importe)} · {int(docs)} tickets"]
        for nombre, t in sorted(items.items(), key=lambda kv: -kv[1]["neto"]):
            a = tiendas_ant.get(nombre, {}).get("neto")
            limpio = NOMBRE.get(nombre.strip(), nombre.strip().strip("*").strip())
            limpio = limpio.replace("Cafetería ", "").replace("Tienda ", "")
            medio = t["neto"] / t["docs"] if t["docs"] else 0
            lineas.append(f"   • {limpio}: {eur(t['neto'])}{variacion(t['neto'], a, con_importe)}"
                          f" · {int(t['docs'])} tk · {eur(medio)}/tk")
    # Suma de cafetería + tienda (+ charcutería) de cada local
    locales, locales_ant = {}, {}
    for nombre, t in tiendas.items():
        locales[local(nombre)] = locales.get(local(nombre), 0) + t["neto"]
        locales_ant[local(nombre)] = locales_ant.get(local(nombre), 0) + tiendas_ant.get(nombre, {}).get("neto", 0)
    lineas += ["", "🏬 <b>POR ESTABLECIMIENTO (cafetería + tienda)</b>"]
    for n, v in sorted(locales.items(), key=lambda kv: -kv[1]):
        lineas.append(f"   • {n}: <b>{eur(v)}</b>{variacion(v, locales_ant.get(n), con_importe)}")
    medio = totales["neto"] / totales["docs"] if totales["docs"] else 0
    lineas += ["", f"💶 <b>TOTAL: {eur(totales['neto'])}</b>{variacion(totales['neto'], totales_ant.get('neto'), con_importe)}"
                   f" · {int(totales['docs'])} tickets · {eur(medio)}/ticket"]
    if totales_ant.get("neto"):
        if con_importe:
            lineas.append(f"<i>Entre paréntesis, importe de {etiqueta_ant} y variación.</i>")
        else:
            lineas.append(f"<i>Entre paréntesis, variación frente a {etiqueta_ant} "
                          f"({eur(totales_ant['neto'])}).</i>")
    return "\n".join(lineas)


def main():
    cfg = base.cargar_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dia = dt.date.fromisoformat(args[0]) if args else dt.date.today()
    hora = dt.datetime.now().hour
    token = base.login(cfg)
    tiendas, totales = ventas_dia(token, dia)
    tiendas_ant, totales_ant = ventas_dia(token, dia - dt.timedelta(days=7))
    texto = mensaje(dia, hora, tiendas, totales, tiendas_ant, totales_ant)
    if "--no-enviar" in sys.argv:
        print(texto)
        return
    base.telegram(cfg, texto)
    print(f"Enviado: {len(tiendas)} establecimientos, total {eur(totales['neto'])}.")


if __name__ == "__main__":
    main()
