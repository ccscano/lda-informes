#!/usr/bin/env python3
"""
Tiempos de cocina de las cafeterías por Telegram (HiOffice / Analytics → Auditoría Cocina → Resumen).

Se ejecuta cada 30 minutos de 9:00 a 21:00 (cocina.timer) y manda al canal, por cafetería, el
«Min. total» (minutos de media desde que se marcha una comanda hasta que se sirve) de lo que va de
día y de la última hora, con las unidades servidas y un semáforo (umbrales BUENO / REGULAR).

Uso: cocina.py [AAAA-MM-DD] [--no-enviar]
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presencia as base

# Bloque «Por horas» de la pestaña Resumen del dashboard Auditoría Cocina
DASHBOARD, BLOCK, DATASOURCE = 15005, 48302, 12031
# (nombre, tipo, origen, ids, posición, internalId)
COLUMNAS = [
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 1, 1),
    ("Hora", "Integer", "Attribute", {"attributeId": 1077}, 2, 2),
    ("Uds. Servidas", "BigDecimal", "Metric", {"metricId": 12066}, 3, 3),
    ("#", "BigDecimal", "Metric", {"metricId": 15008}, 4, 4),
    ("Min. total", "BigDecimal", "Metric", {"metricId": 15014}, 5, 5),
]
BUENO, REGULAR = 10, 15   # minutos: < BUENO 🟢, hasta REGULAR 🟡, más 🔴
NOMBRE = {"Cafeteria Paseo": "Paseo", "Cafeteria Torrecardenas": "Torrecárdenas", "Cafeteria Rambla": "Rambla",
          "Cafeteria Aguadulce": "Aguadulce", "Cafeteria Playa": "Playa"}


def columna(nombre, tipo, origen, ids, pos, internal_id):
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": True, "columnType": 1,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def tiempos(token, dia):
    """{cafetería: {hora: (uds, nº, min_total)}} del día."""
    f = dia.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 500, "totals": False, "rest": False,
        "datasources": [{"id": DATASOURCE}],
        "columns": [columna(*c) for c in COLUMNAS],
        "ordenableColumns": [],
        "filters": [{"permission": False, "toDelete": False, "id": -1, "name": None,
                     "dateRange": {"initialScale": 1, "finalScale": 0, "toDate": 1, "numLast": 0,
                                   "numNext": 0, "numMaxDays": 0, "scaleOffset": 0,
                                   "comparativeType": 2, "allowComparativeDates": False,
                                   "displacement": 0, "displacementScale": 1},
                     "dateValue": f, "dateValue2": f, "filterType": 1,
                     "profileType": None, "filterBlocks": []}],
        "actions": [], "blocksRefresh": [], "blockOperations": [], "blockBridgeExportations": [],
        "blockStyles": [], "blockStyleTypeId": 1,
        "elementId": str(int(dt.datetime.now().timestamp() * 1000)),
        "dashboard": DASHBOARD, "block": BLOCK, "linkLocation": None, "skipCache": True,
        "ignoreDates": False, "id": 1, "dateReference": dia.isoformat(),
        "columnarCalculation": False, "refresh": False, "isDimensionSelector": False,
        "calculateSubtotals": False, "useSizeInColumn": False, "forceNoResults": False,
        "selectorType": 0, "useDistinct": False, "maxResults": None,
    }
    req = urllib.request.Request(f"{base.HIOFFICE}/ErpCloud/report/query", data=json.dumps(body).encode(),
                                 headers={"x-auth-token": token, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = json.load(r)
    res = {}
    for fila in datos.get("rows") or []:
        _, estab, hora, uds, n, total = fila[:6]
        if not estab or hora is None:
            continue
        res.setdefault(estab.strip(), {})[int(hora)] = (float(uds or 0), float(n or 0), float(total or 0))
    return res


def media(horas):
    """(uds, min_total) de varias horas: la media de cada hora ponderada por su nº de líneas."""
    uds = sum(h[0] for h in horas)
    n = sum(h[1] for h in horas)
    return uds, (sum(h[1] * h[2] for h in horas) / n if n and uds else None)


def mins(m):
    return f"{m:.1f}".replace(".", ",") + " min"


def semaforo(m):
    return "🟢" if m < BUENO else "🟡" if m <= REGULAR else "🔴"


def texto(datos, dia, ahora):
    desde = (ahora - dt.timedelta(hours=1)).hour if ahora.date() == dia else None
    lineas = [f"👨‍🍳 <b>Tiempos de cocina</b> · {base.DIAS[dia.weekday()]} {dia:%d/%m}"
              + (f" a las {ahora:%H:%M}" if desde is not None else "")]
    for estab in sorted(NOMBRE, key=lambda e: NOMBRE[e]):
        horas = datos.get(estab, {})
        uds, m = media(list(horas.values()))
        if m is None:
            lineas.append(f"⚪ <b>{NOMBRE[estab]}</b>: sin comandas")
            continue
        linea = f"{semaforo(m)} <b>{NOMBRE[estab]}</b>: <b>{mins(m)}</b> ({uds:.0f} uds)"
        if desde is not None:
            uds_h, m_h = media([v for h, v in horas.items() if h >= desde])
            linea += f" · última hora {mins(m_h)} ({uds_h:.0f} uds)" if m_h is not None else " · última hora sin comandas"
        lineas.append(linea)
    lineas.append(f"<i>Min. total de media por unidad (Auditoría Cocina → Resumen). "
                  f"🟢 menos de {BUENO} · 🟡 {BUENO}-{REGULAR} · 🔴 más de {REGULAR}</i>")
    return "\n".join(lineas)


def main():
    cfg = base.cargar_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ahora = dt.datetime.now()
    dia = dt.date.fromisoformat(args[0]) if args else ahora.date()
    t = texto(tiempos(base.login(cfg), dia), dia, ahora)
    print(t)
    if "--no-enviar" not in sys.argv:
        base.telegram(cfg, t)


if __name__ == "__main__":
    main()
