#!/usr/bin/env python3
"""
Avisos por Telegram cuando un local va muy por debajo de lo normal.

Se ejecuta cada hora de 10:40 a 21:40 (avisos.timer). Lee de HiOffice las ventas por hora de hoy y
compara la venta de cada local hasta ahora con la del mismo día de la semana pasada hasta la misma
hora (la hora en curso, en proporción a los minutos). Si va un UMBRAL % o más por debajo, manda un
aviso en privado a TELEGRAM_USUARIOS (no al canal). Cada local se avisa una vez al día, y otra vez
solo si empeora otros 10 puntos.

Uso: avisos.py [--no-enviar]
"""
import datetime as dt
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico
import presencia as base

UMBRAL = 25            # % por debajo del mismo día de la semana pasada
MINIMO = 300           # € que debía llevar el local a esta hora para que el aviso tenga sentido
ENVIADOS = Path(__file__).resolve().parent / "datos" / "avisos_enviados.json"
NO_SON_LOCALES = ("Obrador", "Oficina Central", "Opera Prima")


def hasta_ahora(con, dia, ahora):
    """{local: venta} del día hasta la hora `ahora` (la hora en curso en proporción a los minutos)."""
    h, frac = ahora.hour, ahora.minute / 60
    return dict(con.execute(
        "SELECT local, SUM(CASE WHEN hora < ? THEN neto WHEN hora = ? THEN neto * ? ELSE 0 END) FROM ventas_hora "
        f"WHERE dia = ? AND local NOT IN ({', '.join('?' * len(NO_SON_LOCALES))}) GROUP BY local",
        [h, h, frac, dia.isoformat(), *NO_SON_LOCALES]))


def main():
    cfg = base.cargar_env()
    ahora = dt.datetime.now()
    hoy = ahora.date()
    antes = hoy - dt.timedelta(days=7)
    con = historico.conectar()
    token = base.login(cfg)
    historico.cargar_ventas_hora(con, token, hoy, hoy)
    if not con.execute("SELECT 1 FROM cargas WHERE tabla = 'ventas_hora' AND dia = ?", (antes.isoformat(),)).fetchone():
        historico.cargar_ventas_hora(con, token, antes, antes)
    actual, anterior = hasta_ahora(con, hoy, ahora), hasta_ahora(con, antes, ahora)
    con.close()

    try:
        enviados = json.loads(ENVIADOS.read_text())
    except (FileNotFoundError, ValueError):
        enviados = {}
    enviados = {k: v for k, v in enviados.items() if k.startswith(hoy.isoformat())}
    lineas = []
    for loc, ant in sorted(anterior.items()):
        if ant < MINIMO:
            continue
        act = actual.get(loc, 0)
        caida = (1 - act / ant) * 100
        clave = f"{hoy.isoformat()}|{loc}"
        if caida >= UMBRAL and caida >= enviados.get(clave, 0) + 10:
            lineas.append(f"⚠️ <b>{loc}</b> va un <b>{caida:.0f} % por debajo</b> del {base.DIAS[antes.weekday()]} "
                          f"pasado a las {ahora:%H:%M}: {act:,.0f} € frente a {ant:,.0f} €".replace(",", "."))
            enviados[clave] = caida
    if not lineas:
        print(f"{ahora:%H:%M} sin avisos: " + ", ".join(f"{l} {actual.get(l, 0):.0f}/{a:.0f}" for l, a in sorted(anterior.items())))
        return
    texto = "\n".join(lineas)
    if "--no-enviar" in sys.argv:
        print(texto)
        return
    usuarios = [x.strip() for x in cfg.get("TELEGRAM_USUARIOS", "").replace(";", ",").split(",") if x.strip()]
    for uid in usuarios:
        datos = urllib.parse.urlencode({"chat_id": uid, "text": texto, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage", data=datos, timeout=30).read()
    ENVIADOS.write_text(json.dumps(enviados))
    print(texto)


if __name__ == "__main__":
    main()
