#!/usr/bin/env python3
"""
Bot de consultas de La Dulce Alianza (@ladulcealianzacom_bot).

Servicio permanente en el VPS (systemd). Escucha los mensajes privados que le
mandan los usuarios autorizados (TELEGRAM_USUARIOS en .env, ids separados por
comas) y responde con los mismos informes que se publican en el canal:

  /ventas [fecha]      ventas de hoy hasta ahora, o del día indicado
  /presencia [fecha]   fichajes del día (solo los cerrados) y € por hora
  /semana [fecha]      € por hora de la última semana cerrada (o la de la fecha)
  /productos [fecha]   top 10 productos: empresa y por local (añade "todo" para cada establecimiento)
  /ayuda

Fechas admitidas: hoy, ayer, 15/10, 15/10/2026, 2026-10-15.
"""
import datetime as dt
import json
import re
import sys
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cierres
import periodos
import presencia as base
import productos
import ventas

OFFSET = Path(__file__).resolve().parent / "bot_offset.json"
AYUDA = ("Comandos:\n"
         "/ventas [fecha] – ventas de hoy hasta ahora o del día indicado\n"
         "/presencia [fecha] – fichajes del día (solo cerrados) y € por hora\n"
         "/semana [fecha] – € por hora de la última semana cerrada\n"
         "/productos [fecha] [todo] – top 10 productos (con «todo», también cada establecimiento)\n"
         "/cierres [fecha] – cierres de caja (arqueos Z) del día\n"
         "/semanal [fecha] – ventas y top productos de la última semana cerrada (o la de la fecha)\n"
         "/mensual [fecha] – ventas y top productos del último mes cerrado (o el de la fecha)\n\n"
         "Fechas: hoy, ayer, 15/10, 15/10/2026, 2026-10-15")


def api(cfg, metodo, **params):
    datos = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/{metodo}",
                                data=datos, timeout=70) as r:
        return json.load(r)


def responder(cfg, chat_id, texto):
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > 3900:
            trozos.append(actual)
            actual = ""
        actual += linea + "\n"
    trozos.append(actual)
    for t in trozos:
        api(cfg, "sendMessage", chat_id=chat_id, text=t, parse_mode="HTML", disable_web_page_preview="true")


def parsear_fecha(txt):
    txt = (txt or "").strip().lower()
    hoy = dt.date.today()
    if txt in ("", "hoy"):
        return hoy
    if txt == "ayer":
        return hoy - dt.timedelta(days=1)
    if txt == "anteayer":
        return hoy - dt.timedelta(days=2)
    m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", txt)
    if m:
        d, mes, a = int(m.group(1)), int(m.group(2)), m.group(3)
        a = hoy.year if not a else (int(a) + 2000 if len(a) == 2 else int(a))
        f = dt.date(a, mes, d)
        if f > hoy and not m.group(3):   # "15/12" en octubre → diciembre del año pasado
            f = f.replace(year=a - 1)
        return f
    return dt.date.fromisoformat(txt)


def cmd_ventas(cfg, dia):
    hoy = dt.date.today()
    token = base.login(cfg)
    tiendas, totales = ventas.ventas_dia(token, dia)
    tiendas_ant, totales_ant = ventas.ventas_dia(token, dia - dt.timedelta(days=7))
    hora = dt.datetime.now().hour if dia == hoy else 23
    return ventas.mensaje(dia, hora, tiendas, totales, tiendas_ant, totales_ant)


def cmd_presencia(cfg, dia):
    hoy = dt.date.today()
    token = base.login(cfg)
    filas = base.consultar(token, dia - dt.timedelta(days=base.DIAS_VENTANA), hoy)
    texto = base.resumen_dia(filas, dia)
    if dia == hoy:
        texto += "\n\n<i>Solo aparecen los fichajes ya cerrados; quien sigue trabajando no sale hasta que fiche la salida.</i>"
    else:
        texto += "\n" + base.bloque_eur_hora(token, filas, dia)
    return texto


def cmd_semana(cfg, dia):
    hoy = dt.date.today()
    token = base.login(cfg)
    # Domingo de la semana pedida; si esa semana no ha terminado, la última cerrada
    domingo = dia + dt.timedelta(days=6 - dia.weekday())
    if domingo >= hoy:
        domingo -= dt.timedelta(days=7)
    filas = base.consultar(token, domingo - dt.timedelta(days=20), hoy)
    return base.bloque_semanal(token, filas, domingo).strip()


def cmd_productos(cfg, dia, todo):
    hoy = dt.date.today()
    token = base.login(cfg)
    filas = productos.articulos_dia(token, dia)
    hora = dt.datetime.now().hour if dia == hoy else 23
    return "\n\n".join(productos.mensajes(dia, hora, filas, solo_locales=not todo))


def cmd_cierres(cfg, dia):
    token = base.login(cfg)
    todos = cierres.cierres(token, dia - dt.timedelta(days=cierres.DIAS_HISTORICO), dia)
    de_hoy = [c for c in todos if c["fecha"][:10] == dia.isoformat()]
    historico = [c for c in todos if c["fecha"][:10] != dia.isoformat()]
    return cierres.mensaje(dia, de_hoy, historico)


def cmd_periodo(cfg, dia, mensual, con_fecha):
    import calendar
    token = base.login(cfg)
    hoy = dt.date.today()
    if mensual:
        if con_fecha:
            desde = dia.replace(day=1)
            hasta = min(dia.replace(day=calendar.monthrange(dia.year, dia.month)[1]), hoy)
        else:
            desde, hasta = periodos.mes_cerrado(dia)
        desde_ant, hasta_ant = periodos.mes_anterior(desde)
        nombre = f"{periodos.MESES[desde.month - 1]} {desde.year}"
        partes = periodos.resumen(token, desde, hasta, desde_ant, hasta_ant,
                                  f"📅 <b>VENTAS DEL MES · {nombre}</b> ({desde:%d/%m} – {hasta:%d/%m})",
                                  f"🏆 <b>Top {productos.TOP} productos del mes · {nombre}</b>",
                                  periodos.MESES[desde_ant.month - 1])
    else:
        if con_fecha:
            desde = dia - dt.timedelta(days=dia.weekday())
            hasta = min(desde + dt.timedelta(days=6), hoy)
        else:
            desde, hasta = periodos.semana_cerrada(dia)
        desde_ant, hasta_ant = desde - dt.timedelta(days=7), hasta - dt.timedelta(days=7)
        partes = periodos.resumen(token, desde, hasta, desde_ant, hasta_ant,
                                  f"📅 <b>VENTAS DE LA SEMANA</b> {desde:%d/%m} – {hasta:%d/%m}",
                                  f"🏆 <b>Top {productos.TOP} productos de la semana</b> {desde:%d/%m} – {hasta:%d/%m}",
                                  f"la semana anterior ({desde_ant:%d/%m} – {hasta_ant:%d/%m})")
    return "\n\n".join(partes)


def atender(cfg, texto):
    partes = texto.strip().split(None, 1)
    cmd = partes[0].lower().split("@")[0]
    arg = partes[1] if len(partes) > 1 else ""
    if cmd in ("/start", "/ayuda", "/help"):
        return AYUDA
    todo = "todo" in arg.lower().split()
    arg = " ".join(w for w in arg.split() if w.lower() != "todo")
    try:
        dia = parsear_fecha(arg)
    except ValueError:
        return f"No entiendo la fecha «{arg}».\n\n{AYUDA}"
    if cmd == "/ventas":
        return cmd_ventas(cfg, dia)
    if cmd == "/presencia":
        return cmd_presencia(cfg, dia)
    if cmd == "/semana":
        return cmd_semana(cfg, dia)
    if cmd == "/productos":
        return cmd_productos(cfg, dia, todo)
    if cmd == "/cierres":
        return cmd_cierres(cfg, dia)
    if cmd in ("/semanal", "/mensual"):
        return cmd_periodo(cfg, dia, cmd == "/mensual", bool(arg.strip()))
    return AYUDA


def main():
    cfg = base.cargar_env()
    autorizados = {int(x) for x in cfg.get("TELEGRAM_USUARIOS", "").replace(";", ",").split(",") if x.strip()}
    if not autorizados:
        sys.exit("Falta TELEGRAM_USUARIOS en .env")
    offset = json.loads(OFFSET.read_text()).get("offset", 0) if OFFSET.exists() else 0
    print(f"bot en marcha; usuarios autorizados: {len(autorizados)}", flush=True)
    while True:
        try:
            res = api(cfg, "getUpdates", offset=offset, timeout=50, allowed_updates='["message","channel_post"]')
        except Exception as e:
            print("getUpdates:", e, flush=True)
            time.sleep(10)
            continue
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message") or u.get("channel_post") or {}
            texto = m.get("text") or ""
            chat = m.get("chat", {})
            uid = (m.get("from") or {}).get("id")
            if not texto.startswith("/"):
                continue
            # Se atiende en privado a los usuarios autorizados y en el propio canal del buzón
            if chat.get("type") == "private":
                if uid not in autorizados:
                    print(f"rechazado usuario {uid}", flush=True)
                    responder(cfg, chat["id"], "No tienes acceso a este bot.")
                    continue
            elif str(chat.get("id")) != str(cfg.get("TELEGRAM_CHAT_ID")):
                continue
            print(f"comando de {uid}: {texto[:40]}", flush=True)
            try:
                responder(cfg, chat["id"], atender(cfg, texto))
            except Exception as e:
                traceback.print_exc()
                responder(cfg, chat["id"], f"Error al consultar: {str(e)[:120]}")
            # El offset se guarda después de responder: si el servicio se reinicia a mitad,
            # el comando se vuelve a atender en vez de perderse.
            OFFSET.write_text(json.dumps({"offset": offset}))


if __name__ == "__main__":
    main()
