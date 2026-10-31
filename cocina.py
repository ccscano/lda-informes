#!/usr/bin/env python3
"""
Tiempos de cocina de las cafeterías por Telegram (HiOffice / Analytics → Auditoría Cocina).

Se ejecuta cada 30 minutos de 9:00 a 21:00 (cocina.timer) y manda en privado a TELEGRAM_USUARIOS
(presencia.telegram), por cafetería, el «Min. total» (minutos de media desde que se marcha una línea
de comanda hasta que se sirve) de las líneas servidas en la última media hora, con un semáforo
(umbrales BUENO / REGULAR), y el de todo el día. Una línea cuenta como servida a su «Fecha Inicio»
más su «Min. total».

Uso: cocina.py [AAAA-MM-DD] [--no-enviar]
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presencia as base

# Bloque «Detalle tiempos cocina» del dashboard Auditoría Cocina (una fila por línea de comanda)
DASHBOARD, BLOCK, DATASOURCE = 15005, 48301, 12031
# (nombre, tipo, origen, ids, posición, internalId)
COLUMNAS = [
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 1, 1),
    ("Guid Línea", "Guid", "Attribute", {"attributeId": 12444}, 2, 2),
    ("Fecha Inicio", "Datetime", "Attribute", {"attributeId": 1663}, 3, 3),
    ("Uds. Servidas", "BigDecimal", "Metric", {"metricId": 12066}, 4, 4),
    ("#", "BigDecimal", "Metric", {"metricId": 15008}, 5, 5),
    ("Min. total", "BigDecimal", "Metric", {"metricId": 15014}, 6, 6),
]
VENTANA = 30              # minutos del semáforo
BUENO, REGULAR = 10, 15   # minutos: < BUENO 🟢, hasta REGULAR 🟡, más 🔴
NOMBRE = {"Cafeteria Paseo": "Paseo", "Cafeteria Torrecardenas": "Torrecárdenas", "Cafeteria Rambla": "Rambla",
          "Cafeteria Aguadulce": "Aguadulce", "Cafeteria Playa": "Playa"}


def columna(nombre, tipo, origen, ids, pos, internal_id):
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": True, "columnType": 1,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def tiempos(token, dia):
    """{cafetería: [(servida, uds, nº, min_total)]}: las líneas de comanda del día."""
    f = dia.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 20000, "totals": False, "rest": False,
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
        _, estab, _, inicio, uds, n, total = fila[:7]
        if not estab or not inicio:
            continue
        total = float(total or 0)
        servida = dt.datetime.fromisoformat(inicio) + dt.timedelta(minutes=total)
        res.setdefault(estab.strip(), []).append((servida, float(uds or 0), float(n or 0), total))
    return res


def media(lineas):
    """(uds, min_total) de varias líneas: la media ponderada por su nº."""
    uds = sum(x[1] for x in lineas)
    n = sum(x[2] for x in lineas)
    return uds, (sum(x[2] * x[3] for x in lineas) / n if n and uds else None)


def mins(m):
    return f"{m:.1f}".replace(".", ",") + " min"


def semaforo(m):
    return "🟢" if m < BUENO else "🟡" if m <= REGULAR else "🔴"


def texto(datos, dia, ahora):
    hoy = ahora.date() == dia
    lineas = [f"👨‍🍳 <b>Tiempos de cocina</b> · {base.DIAS[dia.weekday()]} {dia:%d/%m}"
              + (f" a las {ahora:%H:%M}" if hoy else "")]
    desde = ahora - dt.timedelta(minutes=VENTANA)
    for estab in sorted(NOMBRE, key=lambda e: NOMBRE[e]):
        todas = datos.get(estab, [])
        uds_d, m_d = media(todas)
        if not hoy:
            lineas.append(f"{semaforo(m_d)} <b>{NOMBRE[estab]}</b>: <b>{mins(m_d)}</b> ({uds_d:.0f} uds)"
                          if m_d is not None else f"⚪ <b>{NOMBRE[estab]}</b>: sin comandas")
            continue
        uds, m = media([x for x in todas if desde < x[0] <= ahora])
        dia_txt = f" · día {mins(m_d)} ({uds_d:.0f} uds)" if m_d is not None else ""
        if m is None:
            lineas.append(f"⚪ <b>{NOMBRE[estab]}</b>: sin comandas servidas en {VENTANA} min{dia_txt}")
        else:
            lineas.append(f"{semaforo(m)} <b>{NOMBRE[estab]}</b>: <b>{mins(m)}</b> ({uds:.0f} uds){dia_txt}")
    lineas.append(f"<i>Min. total de media por unidad (Auditoría Cocina)"
                  + (f" de lo servido en los últimos {VENTANA} min" if hoy else "")
                  + f". 🟢 menos de {BUENO} · 🟡 {BUENO}-{REGULAR} · 🔴 más de {REGULAR}</i>")
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
