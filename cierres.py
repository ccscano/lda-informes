#!/usr/bin/env python3
"""
Cierres de caja (arqueos Z) del día por Telegram (HiOffice / Hiopos).

Se ejecuta en el VPS a las 23:00. Lee el bloque "Estadística → Cierres de caja"
de HiOffice (solo cierres cerrados) con fecha de hoy y manda al canal, por local
y TPV: hora del cierre, quién lo hizo, venta, efectivo calculado y declarado,
descuadre, retirado y fianza que queda. Avisa de los TPV que han cerrado otros
días de la última semana y hoy aún no.

Algunos TPV (p. ej. los de la cafetería de Paseo) suben el Z a HiOffice horas
después de hacerlo, así que a las 23:00 pueden faltar. Por eso se guardan los Z
enviados (datos/cierres_enviados.json) y a las 09:00 `--repaso` revisa el día
anterior: avisa solo si han llegado Z nuevos (con la venta corregida) o si alguna
caja sigue sin cierre (contando los Z de esa mañana, como el de Torrecárdenas).

Uso: cierres.py [AAAA-MM-DD] [--no-enviar] [--repaso]
"""
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presencia as base
import ventas

DASHBOARD, BLOCK, DATASOURCES = 196, 52461, [{"id": 37}, {"id": 79}]
DIAS_HISTORICO = 7      # días hacia atrás para saber qué TPV suelen cerrar
ENVIADOS = Path(__file__).resolve().parent / "datos" / "cierres_enviados.json"
# (nombre, tipo, origen, ids, posición, internalId)
COLUMNAS = [
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 1, 11),
    ("Caja", "String", "Attribute", {"attributeId": 12005}, 4, 9),
    ("Número", "Integer", "Attribute", {"attributeId": 1116}, 5, 4),
    ("Fecha", "Datetime", "Attribute", {"attributeId": 1118}, 6, 5),
    ("Empleado", "String", "Attribute", {"attributeId": 41}, 8, 10),
    ("Venta", "BigDecimal", "Metric", {"metricId": 1144}, 9, 26),
    ("Docs", "BigDecimal", "Metric", {"metricId": 1064}, 10, 23),
    ("Venta Efectivo", "BigDecimal", "Metric", {"metricId": 1344}, 13, 22),
    ("Entrada", "BigDecimal", "Metric", {"metricId": 1068}, 14, 14),
    ("Salida", "BigDecimal", "Metric", {"metricId": 1069}, 15, 15),
    ("Calculado", "BigDecimal", "Metric", {"metricId": 1132}, 17, 17),
    ("Declarado", "BigDecimal", "Metric", {"metricId": 1070}, 18, 18),
    ("Descuadre", "BigDecimal", "Metric", {"metricId": 1071}, 19, 19),
    ("Retirado", "BigDecimal", "Metric", {"metricId": 1072}, 20, 20),
    ("Nueva Fianza", "BigDecimal", "Metric", {"metricId": 1131}, 21, 21),
    ("Cerrado", "Boolean", "Attribute", {"attributeId": 15508}, 22, 25),
]
CAMPOS = ["estab", "caja", "numero", "fecha", "empleado", "venta", "docs", "efectivo", "entrada",
          "salida", "calculado", "declarado", "descuadre", "retirado", "fianza", "cerrado"]


def columna(nombre, tipo, origen, ids, pos, internal_id):
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": True, "columnType": 1,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def condicion(attr, valor, tipo, operador="EQUAL"):
    return {"id": -1, "attributeId": attr, "metricId": 0, "comparativeId": 0, "value": valor,
            "valueType": None, "arithmeticOperator": operador, "logicOperator": operador,
            "isBasic": False, "negation": False, "blockId": None, "filterRowId": -1, "type": tipo}


def cierres(token, desde, hasta):
    f = lambda d: d.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 500, "totals": False, "rest": False,
        "datasources": DATASOURCES,
        "columns": [columna(*c) for c in COLUMNAS],
        "ordenableColumns": [],
        "filters": [
            {"permission": False, "toDelete": False, "id": -1, "name": None,
             "dateRange": {"initialScale": 1, "finalScale": 0, "toDate": 1, "numLast": 0, "numNext": 0,
                           "numMaxDays": 0, "scaleOffset": 0, "comparativeType": 2,
                           "allowComparativeDates": False, "displacement": 0, "displacementScale": 1},
             "dateValue": f(desde), "dateValue2": f(hasta), "filterType": 1, "profileType": None,
             "filterBlocks": []},
            {"permission": False, "toDelete": False, "id": -1, "name": None, "dateRange": None,
             "dateValue": None, "dateValue2": None, "filterType": 1, "profileType": None,
             "filterBlocks": [{"id": -1, "logicOperator": 1, "negation": False, "filterGroups": [
                 {"id": -1, "logicOperator": 1, "negation": False, "filterRows": [
                     {"@type": "FilterRow", "id": -1, "logicOperator": 1, "negation": False, "position": 0,
                      "filterConditions": [condicion(15508, 1, "Boolean")]}]}]}]},
        ],
        "actions": [], "blocksRefresh": [], "blockOperations": [], "blockBridgeExportations": [],
        "blockStyles": [], "blockStyleTypeId": 0,
        "elementId": str(int(dt.datetime.now().timestamp() * 1000)),
        "dashboard": DASHBOARD, "block": BLOCK, "linkLocation": None, "skipCache": True,
        "ignoreDates": False, "id": 1, "dateReference": hasta.isoformat(),
        "columnarCalculation": False, "refresh": False, "isDimensionSelector": False,
        "calculateSubtotals": False, "useSizeInColumn": False, "forceNoResults": False,
        "dimensionId": None, "selectorType": 0, "useDistinct": False, "maxResults": None,
    }
    req = urllib.request.Request(f"{base.HIOFFICE}/ErpCloud/report/query", data=json.dumps(body).encode(),
                                 headers={"x-auth-token": token, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = json.load(r)
    filas = []
    for fila in datos.get("rows") or []:
        c = dict(zip(CAMPOS, fila[1:1 + len(CAMPOS)]))
        if not c["estab"]:
            continue
        for k in CAMPOS[5:15]:
            c[k] = float(c[k] or 0)
        c["estab"] = c["estab"].strip()
        filas.append(c)
    return filas


def eur(x):
    return ventas.eur(x)


def mensaje(dia, de_hoy, historico):
    lineas = [f"🧾 <b>Cierres de caja {base.DIAS[dia.weekday()]} {dia:%d/%m}</b>"]
    por_local = {}
    for c in de_hoy:
        por_local.setdefault(ventas.local(c["estab"]), []).append(c)
    tot = {"venta": 0, "efectivo": 0, "retirado": 0, "fianza": 0}
    vacios = []
    for loc, cs in sorted(por_local.items(), key=lambda kv: -sum(c["venta"] for c in kv[1])):
        lineas += ["", f"🏬 <b>{loc.upper()}</b> · venta {eur(sum(c['venta'] for c in cs))}"]
        for c in sorted(cs, key=lambda c: c["fecha"]):
            if c["venta"] == 0 and c["calculado"] == 0 and c["retirado"] == 0:
                vacios.append(f"{c['caja']} (Z {c['numero']}, {c['fecha'][11:16]})")
                continue
            quien = c["empleado"].title().split()[0] if c["empleado"] else "—"
            linea = (f"   • <b>{c['caja']}</b> · Z{c['numero']} · {c['fecha'][11:16]} · {quien} · "
                     f"<b>{eur(c['venta'])}</b> · efectivo {eur(c['efectivo'])}")
            if c["declarado"]:
                aviso = " ⚠️" if abs(c["descuadre"]) >= 1 else ""
                linea += f" · declarado {eur(c['declarado'])} · descuadre {eur(c['descuadre'])}{aviso}"
            lineas.append(linea)
            for k in tot:
                tot[k] += c[k]
    if de_hoy:
        n = len(de_hoy) - len(vacios)
        lineas += ["", f"💶 <b>TOTAL {n} cierres</b> · venta {eur(tot['venta'])} · efectivo {eur(tot['efectivo'])}"]
        if vacios:
            lineas.append(f"<i>Z vacíos: {', '.join(vacios)}</i>")
    else:
        lineas += ["", "Hoy no hay ningún cierre de caja todavía."]
    cerradas_hoy = {(c["estab"], c["caja"]) for c in de_hoy}
    habituales = {(c["estab"], c["caja"]) for c in historico if c["venta"] > 0}
    pendientes = sorted(habituales - cerradas_hoy)
    if pendientes:
        lineas += ["", "⏳ <b>Sin cierre en HiOffice a las 23:00</b> (cierran otros días; "
                       "puede que aún no se haya subido, se revisa mañana a las 09:00):"]
        lineas += [f"   • {ventas.local(e)} · {caja}" for e, caja in pendientes]
    return "\n".join(lineas)


def clave(c):
    return f"{c['estab']}|{c['caja']}|{c['numero']}"


def vacio(c):
    return c["venta"] == 0 and c["calculado"] == 0 and c["retirado"] == 0


def repaso(dia, todos, enviados):
    """Mensaje del repaso de la mañana siguiente, o None si no hay nada que contar."""
    del_dia = [c for c in todos if c["fecha"][:10] == dia.isoformat()]
    # Z hechos la mañana siguiente (antes de las 12) cuentan para el día
    manana = [c for c in todos if c["fecha"][:10] == (dia + dt.timedelta(days=1)).isoformat()
              and c["fecha"][11:13] < "12"]
    historico = [c for c in todos if c["fecha"][:10] < dia.isoformat()]
    cerradas = {(c["estab"], c["caja"]) for c in del_dia + manana}
    habituales = {(c["estab"], c["caja"]) for c in historico if c["venta"] > 0}
    pendientes = sorted(habituales - cerradas)
    nuevos = [] if enviados is None else [c for c in del_dia if clave(c) not in enviados and not vacio(c)]
    nuevos += [c for c in manana if not vacio(c)]
    if not nuevos and not pendientes:
        return None
    lineas = [f"🔁 <b>Repaso cierres {base.DIAS[dia.weekday()]} {dia:%d/%m}</b>"]
    if nuevos:
        lineas += ["", "Han llegado a HiOffice después del informe de las 23:00:"]
        for c in sorted(nuevos, key=lambda c: c["fecha"]):
            quien = c["empleado"].title().split()[0] if c["empleado"] else "—"
            dia_z = "" if c["fecha"][:10] == dia.isoformat() else f"{c['fecha'][8:10]}/{c['fecha'][5:7]} "
            lineas.append(f"   • {ventas.local(c['estab'])} · <b>{c['caja']}</b> · Z{c['numero']} · "
                          f"{dia_z}{c['fecha'][11:16]} · {quien} · <b>{eur(c['venta'])}</b> · "
                          f"efectivo {eur(c['efectivo'])}")
        todos_dia = [c for c in del_dia + manana if not vacio(c)]
        locales = sorted({ventas.local(c["estab"]) for c in nuevos})
        corregida = [f"{loc} {eur(sum(c['venta'] for c in todos_dia if ventas.local(c['estab']) == loc))}"
                     for loc in locales]
        lineas += ["", f"💶 Venta corregida: {' · '.join(corregida)}",
                   f"<b>TOTAL {len(todos_dia)} cierres</b> · venta {eur(sum(c['venta'] for c in todos_dia))}"
                   f" · efectivo {eur(sum(c['efectivo'] for c in todos_dia))}"]
    if pendientes:
        lineas += ["", "⚠️ <b>Siguen sin cierre</b> (cierran otros días esta semana):"]
        lineas += [f"   • {ventas.local(e)} · {caja}" for e, caja in pendientes]
    return "\n".join(lineas)


def main():
    cfg = base.cargar_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    es_repaso = "--repaso" in sys.argv
    hoy = dt.date.today()
    dia = dt.date.fromisoformat(args[0]) if args else hoy - dt.timedelta(days=1 if es_repaso else 0)
    guardados = json.loads(ENVIADOS.read_text()) if ENVIADOS.exists() else {}
    token = base.login(cfg)
    if es_repaso:
        todos = cierres(token, dia - dt.timedelta(days=DIAS_HISTORICO), dia + dt.timedelta(days=1))
        texto = repaso(dia, todos, set(guardados[dia.isoformat()]) if dia.isoformat() in guardados else None)
        if "--no-enviar" in sys.argv:
            print(texto or "Repaso: nada que avisar.")
            return
        if texto:
            base.telegram(cfg, texto)
        print("Repaso enviado." if texto else "Repaso: nada que avisar.")
        return
    todos = cierres(token, dia - dt.timedelta(days=DIAS_HISTORICO), dia)
    de_hoy = [c for c in todos if c["fecha"][:10] == dia.isoformat()]
    historico = [c for c in todos if c["fecha"][:10] != dia.isoformat()]
    texto = mensaje(dia, de_hoy, historico)
    if "--no-enviar" in sys.argv:
        print(texto)
        return
    base.telegram(cfg, texto)
    limite = (dia - dt.timedelta(days=14)).isoformat()
    guardados = {k: v for k, v in guardados.items() if k >= limite}
    guardados[dia.isoformat()] = [clave(c) for c in de_hoy]
    ENVIADOS.write_text(json.dumps(guardados, indent=1))
    print(f"Enviado: {len(de_hoy)} cierres.")


if __name__ == "__main__":
    main()
