#!/usr/bin/env python3
"""
Resumen diario del control de presencia (HiOffice / Hiopos) por Telegram.

Se ejecuta en el VPS a las 04:00 (systemd timer). Lee los fichajes de ayer en
HiOffice (dulcealianza01.hiopos.com) y manda al canal de Telegram:
  - un bloque por establecimiento: personas, horas, primera entrada, última salida
    y el detalle de cada persona;
  - avisos de "olvidó fichar la salida": tramos que cruzan de día o superan
    MAX_HORAS. HiOffice no muestra los fichajes abiertos, así que un olvido solo
    aparece cuando la persona vuelve a fichar; por eso se revisan los últimos
    DIAS_VENTANA días y cada tramo se avisa una sola vez (avisados.json).

Credenciales y destino en ~/presencia/.env (HIOFFICE_*, TELEGRAM_*).
"""
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BASE_DIR = Path(__file__).resolve().parent
ENV = BASE_DIR / ".env"
AVISADOS = BASE_DIR / "avisados.json"

HIOFFICE = "https://dulcealianza01.hiopos.com"
AES_KEY = b"B1B2B3B4B5B6B7B8"          # clave e IV fijas del front de HiOffice (cloudlicense)
MAX_HORAS = 14                          # un tramo más largo es casi seguro un olvido de salida
MIN_TRAMO_MIN = 3                       # tramos más cortos (fichajes dobles) no se listan
DIAS_VENTANA = 21                       # días hacia atrás que se leen (olvidos y comparación semanal)
NO_PLANTILLA = {"SUPERVISOR", "TECNICO SEISOFT", "JAVIER CS"}
# Local de cada establecimiento de presencia, para cruzar con las ventas (el Obrador no vende)
LOCAL_PRESENCIA = {"Tienda Paseo de Almeria": "Paseo", "Tienda Playa": "Playa", "Tienda Rambla": "Rambla",
                   "Tienda Torrecardenas": "Torrecárdenas", "Tienda Aguadulce": "Aguadulce"}
NOMBRE_ESTAB = {
    "Obrador": "Obrador",
    "Tienda Paseo de Almeria": "Paseo de Almería",
    "Tienda Playa": "Playa",
    "Tienda Rambla": "Rambla",
    "Tienda Torrecardenas": "Torrecárdenas",
    "Tienda Aguadulce": "Aguadulce",
}

# Definición del bloque "Control de presencia" tal como la pide el front de HiOffice
DASHBOARD, BLOCK, DATASOURCE, DIMENSION = 1565234, 26901, 135, 190
# (nombre, tipo, origen, ids, posición, internalId). Sin internalId el servidor agrupa todo en una fila.
COLUMNAS = [
    ("Inicio", "Datetime", "Attribute", {"attributeId": 15486}, 1, 10),
    ("Fin", "Datetime", "Attribute", {"attributeId": 15487}, 2, 11),
    ("Establecimiento", "String", "Attribute", {"attributeId": 1061}, 3, 5),
    ("Empleado", "String", "Attribute", {"attributeId": 41}, 4, 4),
    ("Horas", "BigDecimal", "Metric", {"metricId": 1230}, 5, 7),
    ("Id", "Guid", "Attribute", {"attributeId": 15485}, 6, 12),
]


def cargar_env():
    cfg = {}
    for linea in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.startswith("#"):
            k, v = linea.split("=", 1)
            cfg[k.strip()] = v.strip()
    for k in ("HIOFFICE_USER", "HIOFFICE_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        if not cfg.get(k):
            sys.exit(f"Falta {k} en {ENV}")
    return cfg


def cifrar(texto):
    """AES-CBC/PKCS7 con clave=IV fijas, base64, y los reemplazos que hace el front."""
    pad = padding.PKCS7(128).padder()
    datos = pad.update(texto.encode("utf-8")) + pad.finalize()
    enc = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_KEY)).encryptor()
    b64 = base64.b64encode(enc.update(datos) + enc.finalize()).decode()
    return b64.replace("+", "-666666-").replace("/", "-999999-")


def login(cfg):
    params = {
        "user": cifrar(cfg["HIOFFICE_USER"]),
        "password": cifrar(cfg["HIOFFICE_PASSWORD"]),
        "customerId": cfg.get("HIOFFICE_CUSTOMER_ID", "51021"),
        "languageIsoCode": "es", "specType": "2", "isErp": "true",
        "canAddUserToCompany": "false", "ipWS": "", "dbName": "", "encrypted": "true",
    }
    req = urllib.request.Request(f"{HIOFFICE}/ErpCloud/session/login?" + urllib.parse.urlencode(params),
                                 headers={"Content-Type": "application/json"})
    token = None
    for intento in range(4):   # HiOffice devuelve 500 de forma esporádica; se reintenta
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                token = r.headers.get("x-auth-token")
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if intento == 3:
                raise
            print(f"login HiOffice falló ({e}); reintento en 15 s", file=sys.stderr, flush=True)
            __import__("time").sleep(15)
    if not token:
        sys.exit("Login en HiOffice sin token")
    # Inicializa la sesión en el servidor; sin esta llamada report/query devuelve 500
    req = urllib.request.Request(f"{HIOFFICE}/ErpCloud/report/getUser?mobileMode=false",
                                 headers={"x-auth-token": token, "Accept": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    return token


def columna(nombre, tipo, origen, ids, pos, internal_id):
    # Ojo: con campos de más (totalType, groupId, specTypeId…) el servidor deja de agrupar
    # por empleado y devuelve una sola fila con la suma de horas.
    return {"@type": "BlockColumn", "name": nombre, "type": tipo, "sourceType": origen,
            "position": pos, "shown": nombre != "Id", "columnType": 1,
            "isAttribute": origen == "Attribute", "isMetric": origen == "Metric",
            "blockId": BLOCK, "internalId": internal_id, **ids}


def consultar(token, desde, hasta):
    """Fichajes cerrados cuyo día está entre desde y hasta (incluidos)."""
    f = lambda d: d.strftime("%Y/%m/%d")
    body = {
        "offset": 0, "limit": 5000, "totals": False, "rest": False,
        "datasources": [{"id": DATASOURCE}],
        "columns": [columna(*c) for c in COLUMNAS],
        "ordenableColumns": [],
        "filters": [{"permission": False, "toDelete": False, "id": -1, "name": None,
                     "dateRange": {"initialScale": 1, "finalScale": 0, "toDate": 1, "numLast": 0,
                                   "numNext": 0, "numMaxDays": 0, "scaleOffset": -3,
                                   "comparativeType": 2, "allowComparativeDates": False,
                                   "displacement": 0, "displacementScale": 1},
                     "dateValue": f(desde), "dateValue2": f(hasta), "filterType": 1,
                     "profileType": None, "filterBlocks": []}],
        "actions": [], "blocksRefresh": [], "blockOperations": [],
        "blockStyles": [{"id": -1, "blockId": None, "metricId": None, "metric": None, "position": 1,
                         "breakColumnPosition": -1, "color1": None, "color2": None, "backgroundColor1": None,
                         "backgroundColor2": None, "fontWeight": None, "fontStyle": None}],
        "blockBridgeExportations": [], "blockStyleTypeId": 0,
        "elementId": str(int(dt.datetime.now().timestamp() * 1000)),
        "dashboard": DASHBOARD, "block": BLOCK, "linkLocation": None, "skipCache": True,
        "ignoreDates": False, "id": 4, "dateReference": hasta.strftime("%Y-%m-%d"),
        "columnarCalculation": False, "refresh": False, "isDimensionSelector": False,
        "calculateSubtotals": False, "useSizeInColumn": False, "forceNoResults": False,
        "dimensionId": DIMENSION, "selectorType": 0, "useDistinct": False, "maxResults": None,
    }
    req = urllib.request.Request(f"{HIOFFICE}/ErpCloud/report/query", data=json.dumps(body).encode(),
                                 headers={"x-auth-token": token, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        datos = json.load(r)
    filas = []
    for fila in datos.get("rows") or []:
        _, inicio, fin, estab, empleado, horas, rid = fila[:7]
        if not inicio or empleado in NO_PLANTILLA:
            continue
        filas.append({"inicio": inicio, "fin": fin, "estab": estab, "empleado": empleado,
                      "horas": float(horas or 0), "id": rid})
    return filas


def hhmm(ts):
    return ts[11:16] if ts else "—"


def fmt_horas(h):
    return f"{int(h)}:{int(round((h - int(h)) * 60)):02d}"


def resumen_dia(filas, dia):
    por_estab = {}
    for f in filas:
        if f["inicio"][:10] == dia.isoformat():
            por_estab.setdefault(f["estab"], []).append(f)
    partes = [f"📋 <b>Presencia {dia:%A %d/%m/%Y}</b>".replace(dia.strftime("%A"), DIAS[dia.weekday()])]
    if not por_estab:
        partes.append("Sin fichajes registrados.")
    for estab in sorted(por_estab, key=lambda e: list(NOMBRE_ESTAB).index(e) if e in NOMBRE_ESTAB else 99):
        fs = sorted(por_estab[estab], key=lambda x: x["inicio"])
        personas = {}
        for f in fs:
            p = personas.setdefault(f["empleado"], {"h": 0, "tramos": []})
            p["h"] += f["horas"]
            if f["horas"] * 60 >= MIN_TRAMO_MIN:
                p["tramos"].append(f"{hhmm(f['inicio'])}-{hhmm(f['fin'])}")
        total = sum(p["h"] for p in personas.values())
        ultima = max(f["fin"] or "" for f in fs)
        partes.append(f"\n<b>🏪 {NOMBRE_ESTAB.get(estab, estab)}</b> · {len(personas)} pers. · {fmt_horas(total)} h"
                      f" · {hhmm(fs[0]['inicio'])} → {hhmm(ultima)}")
        for nombre, p in sorted(personas.items()):
            partes.append(f"  • {nombre.title()}: {fmt_horas(p['h'])} h ({', '.join(p['tramos'])})")
    return "\n".join(partes)


def olvidos(filas, avisados):
    nuevos = []
    for f in filas:
        if f["id"] in avisados or not f["fin"]:
            continue
        if f["fin"][:10] != f["inicio"][:10] or f["horas"] > MAX_HORAS:
            nuevos.append(f)
    if not nuevos:
        return ""
    lineas = ["\n⚠️ <b>Olvidaron fichar la salida</b> (el fichaje se cerró al volver a fichar):"]
    for f in sorted(nuevos, key=lambda x: x["inicio"]):
        d1, d2 = f["inicio"][:10], f["fin"][:10]
        cierre = hhmm(f["fin"]) if d1 == d2 else f"{d2[8:10]}/{d2[5:7]} {hhmm(f['fin'])}"
        lineas.append(f"  • {f['empleado'].title()} ({NOMBRE_ESTAB.get(f['estab'], f['estab'])}): "
                      f"entró el {d1[8:10]}/{d1[5:7]} a las {hhmm(f['inicio'])}, cerrado el {cierre} "
                      f"→ {fmt_horas(f['horas'])} h")
        avisados[f["id"]] = f["inicio"]
    return "\n".join(lineas)


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def es_olvido(f):
    return bool(f["fin"]) and (f["fin"][:10] != f["inicio"][:10] or f["horas"] > MAX_HORAS)


def horas_por_local(filas, dia):
    """{local: [horas, nº de tramos estimados]} del día. Un olvido de salida se estima
    hasta la última salida normal de ese local ese día (o 8 h si no hay ninguna)."""
    del_dia = [f for f in filas if f["inicio"][:10] == dia.isoformat() and f["estab"] in LOCAL_PRESENCIA]
    ultima_salida = {}
    for f in del_dia:
        if not es_olvido(f) and f["fin"]:
            loc = LOCAL_PRESENCIA[f["estab"]]
            ultima_salida[loc] = max(ultima_salida.get(loc, ""), f["fin"])
    res = {}
    for f in del_dia:
        loc = LOCAL_PRESENCIA[f["estab"]]
        r = res.setdefault(loc, [0.0, 0])
        if es_olvido(f):
            fin = ultima_salida.get(loc)
            if fin and fin > f["inicio"]:
                h = (dt.datetime.fromisoformat(fin) - dt.datetime.fromisoformat(f["inicio"])).total_seconds() / 3600
            else:
                h = 8.0
            r[0] += min(h, MAX_HORAS)
            r[1] += 1
        else:
            r[0] += f["horas"]
    return res


def ventas_por_local(token, dia):
    import ventas  # usa el mismo login; se importa aquí para evitar la importación circular
    tiendas, _ = ventas.ventas_dia(token, dia)
    res = {}
    for nombre, t in tiendas.items():
        res[ventas.local(nombre)] = res.get(ventas.local(nombre), 0) + t["neto"]
    return res


def eur(x):
    return f"{x:,.0f} €".replace(",", ".")


def linea_eur_hora(nombre, venta, horas, estimados=0, ant=None):
    if not horas:
        return f"   • {nombre}: {eur(venta)} / sin fichajes"
    ratio = venta / horas
    comp = ""
    if ant:
        v = (ratio - ant) / ant * 100
        comp = f" ({'+' if v >= 0 else ''}{v:.0f} %)"
    nota = f"  ⚠️ {estimados} fichaje{'s' if estimados > 1 else ''} estimado{'s' if estimados > 1 else ''}" if estimados else ""
    return f"   • {nombre}: {eur(venta)} / {fmt_horas(horas)} h = <b>{ratio:.1f} €/h</b>{comp}{nota}"


def bloque_eur_hora(token, filas, dia):
    ventas = ventas_por_local(token, dia)
    horas = horas_por_local(filas, dia)
    lineas = ["", "📈 <b>€ POR HORA TRABAJADA</b> (ayer, provisional)"]
    for loc, v in sorted(ventas.items(), key=lambda kv: -kv[1]):
        h, est = horas.get(loc, [0, 0])
        lineas.append(linea_eur_hora(loc, v, h, est))
    tv, th = sum(ventas.values()), sum(h for h, _ in horas.values())
    if th:
        lineas.append(f"   <b>Total: {eur(tv)} / {fmt_horas(th)} h = {tv / th:.1f} €/h</b>")
    lineas.append("   <i>Sin los fichajes que aún no se han cerrado; el lunes va el consolidado semanal.</i>")
    return "\n".join(lineas)


def bloque_semanal(token, filas, domingo):
    """Semana lunes-domingo terminada en `domingo`, comparada con la anterior."""
    def semana(fin):
        dias = [fin - dt.timedelta(days=i) for i in range(6, -1, -1)]
        v, h, est = {}, {}, {}
        for d in dias:
            for loc, x in ventas_por_local(token, d).items():
                v[loc] = v.get(loc, 0) + x
            for loc, (hh, e) in horas_por_local(filas, d).items():
                h[loc] = h.get(loc, 0) + hh
                est[loc] = est.get(loc, 0) + e
        return dias, v, h, est
    dias, v, h, est = semana(domingo)
    _, v_ant, h_ant, _ = semana(domingo - dt.timedelta(days=7))
    lineas = ["", f"📊 <b>SEMANA {dias[0]:%d/%m} – {dias[-1]:%d/%m}: € por hora trabajada</b> (consolidado)"]
    for loc, venta in sorted(v.items(), key=lambda kv: -kv[1]):
        ant = (v_ant.get(loc, 0) / h_ant[loc]) if h_ant.get(loc) else None
        lineas.append(linea_eur_hora(loc, venta, h.get(loc, 0), est.get(loc, 0), ant))
    tv, th = sum(v.values()), sum(h.values())
    tva, tha = sum(v_ant.values()), sum(h_ant.values())
    if th:
        comp = f" (semana anterior: {tva / tha:.1f} €/h)" if tha else ""
        lineas.append(f"   <b>Total: {eur(tv)} / {fmt_horas(th)} h = {tv / th:.1f} €/h</b>{comp}")
    lineas.append("   <i>Entre paréntesis, variación del €/h frente a la semana anterior. "
                  "Los olvidos de salida se cuentan hasta la última salida del local ese día.</i>")
    return "\n".join(lineas)


def telegram(cfg, texto):
    """Envía por trozos de < 4000 caracteres (límite de Telegram)."""
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > 3900:
            trozos.append(actual)
            actual = ""
        actual += linea + "\n"
    trozos.append(actual)
    for t in trozos:
        datos = urllib.parse.urlencode({"chat_id": cfg["TELEGRAM_CHAT_ID"], "text": t,
                                        "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        with urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage",
                                    data=datos, timeout=30) as r:
            if not json.load(r).get("ok"):
                sys.exit("Telegram no aceptó el mensaje")


def main():
    cfg = cargar_env()
    hoy = dt.date.today()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dia = dt.date.fromisoformat(args[0]) if args else hoy - dt.timedelta(days=1)
    solo_mostrar = "--no-enviar" in sys.argv

    token = login(cfg)
    filas = consultar(token, dia - dt.timedelta(days=DIAS_VENTANA), hoy)
    avisados = json.loads(AVISADOS.read_text()) if AVISADOS.exists() else {}
    # Se guardan solo los avisos de los últimos 60 días para que el fichero no crezca
    limite = (hoy - dt.timedelta(days=60)).isoformat()
    avisados = {k: v for k, v in avisados.items() if v >= limite}

    texto = resumen_dia(filas, dia) + "\n" + olvidos(filas, avisados)
    try:
        texto += "\n" + bloque_eur_hora(token, filas, dia)
        if dia.weekday() == 6 or "--semanal" in sys.argv:  # el lunes de madrugada, la semana cerrada
            texto += "\n" + bloque_semanal(token, filas, dia if dia.weekday() == 6 else dia - dt.timedelta(days=dia.weekday() + 1))
    except Exception as e:  # que un fallo en ventas no tumbe el resumen de presencia
        texto += f"\n\n(No se ha podido calcular el € por hora: {str(e)[:80]})"
    if solo_mostrar:
        print(texto)
        return
    telegram(cfg, texto)
    AVISADOS.write_text(json.dumps(avisados, indent=1))
    print(f"Enviado: {len(filas)} fichajes leídos.")


if __name__ == "__main__":
    main()
