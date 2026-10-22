#!/usr/bin/env python3
"""
App de La Dulce Alianza: panel web (PWA, icono en el móvil) sobre datos/historico.db.

Servicio permanente (systemd de usuario) que escucha en 127.0.0.1:8765; se publica por
HTTPS con un proxy delante. Sirve la web de app/ y una API de solo lectura:

  POST /api/codigo        manda un código de 6 cifras por Telegram a TELEGRAM_USUARIOS
  POST /api/entrar        {"codigo": "123456"} → cookie de sesión de un año
  GET  /api/datos?desde=AAAA-MM-DD&hasta=AAAA-MM-DD[&local=Paseo]
  GET  /api/salir

Los datos los carga historico.py; la app no consulta HiOffice.
"""
import datetime as dt
import http.server
import json
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import historico
import presencia as base

DIR = Path(__file__).resolve().parent
WEB = DIR / "app"
SESIONES = DIR / "datos" / "sesiones.json"
PUERTO = 8765
DURACION_SESION = 365 * 24 * 3600
CODIGO_VALIDEZ = 600
MAX_INTENTOS = 5
MAX_DIAS_CIERRES = 62          # el detalle de cierres solo se manda en periodos cortos
TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".png": "image/png"}

cfg = base.cargar_env()
USUARIOS = [x.strip() for x in cfg.get("TELEGRAM_USUARIOS", "").replace(";", ",").split(",") if x.strip()]
cerrojo = threading.Lock()
codigo_actual = {"valor": None, "caduca": 0, "intentos": 0, "enviado": 0}


def leer_sesiones():
    try:
        return json.loads(SESIONES.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def guardar_sesiones(s):
    SESIONES.parent.mkdir(exist_ok=True)
    SESIONES.write_text(json.dumps(s))
    SESIONES.chmod(0o600)


def local_fichaje(estab):
    return base.LOCAL_PRESENCIA.get(estab, base.NOMBRE_ESTAB.get(estab, estab))


def datos(desde, hasta, local=None):
    con = sqlite3.connect(f"file:{historico.DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    d0, d1 = desde.isoformat(), hasta.isoformat()
    filtro, args = ("AND local = ?", [local]) if local else ("", [])

    ventas = [dict(r) for r in con.execute(
        f"SELECT dia, local, grupo, SUM(neto) neto, SUM(docs) docs FROM ventas "
        f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY dia, local, grupo ORDER BY dia", [d0, d1, *args])]

    # Horas por local y día con la misma regla que el €/h de Telegram (olvidos estimados)
    fich = [dict(r) for r in con.execute(
        "SELECT * FROM fichajes WHERE dia BETWEEN ? AND ? ORDER BY inicio", [d0, d1])]
    horas = []
    por_dia = {}
    for f in fich:
        por_dia.setdefault(f["dia"], []).append(f)
    for dia, filas in por_dia.items():
        for loc, (h, est) in base.horas_por_local(filas, dt.date.fromisoformat(dia)).items():
            if not local or loc == local:
                horas.append({"dia": dia, "local": loc, "horas": round(h, 2), "estimados": est})

    personal = {}
    for f in fich:
        loc = local_fichaje(f["estab"])
        if local and loc != local:
            continue
        p = personal.setdefault(f["empleado"], {"empleado": f["empleado"].title(), "horas": 0, "dias": set(),
                                                "olvidos": 0, "locales": {}})
        if base.es_olvido(f):
            p["olvidos"] += 1
        else:
            p["horas"] += f["horas"]
        p["dias"].add(f["dia"])
        p["locales"][loc] = p["locales"].get(loc, 0) + 1
    personal = sorted(({"empleado": p["empleado"], "horas": round(p["horas"], 2), "dias": len(p["dias"]),
                        "olvidos": p["olvidos"], "local": max(p["locales"], key=p["locales"].get)}
                       for p in personal.values()), key=lambda p: -p["horas"])

    productos = [dict(r) for r in con.execute(
        f"SELECT articulo, SUM(uds) uds, SUM(venta) venta FROM productos WHERE dia BETWEEN ? AND ? {filtro} "
        f"GROUP BY articulo ORDER BY venta DESC LIMIT 1000", [d0, d1, *args])]

    cierres = []
    if (hasta - desde).days < MAX_DIAS_CIERRES:
        cierres = [dict(r) for r in con.execute(
            f"SELECT dia, fecha, local, estab, caja, numero, empleado, venta, docs, efectivo, retirado, fianza, "
            f"declarado, descuadre FROM cierres WHERE dia BETWEEN ? AND ? {filtro} ORDER BY fecha DESC",
            [d0, d1, *args])]

    locales = sorted({r[0] for r in con.execute("SELECT DISTINCT local FROM ventas")})
    actualizado = con.execute("SELECT MAX(actualizado) FROM cargas").fetchone()[0]
    primer_dia = con.execute("SELECT MIN(dia) FROM ventas WHERE neto > 0").fetchone()[0]
    con.close()
    return {"desde": d0, "hasta": d1, "local": local, "ventas": ventas, "horas": horas,
            "personal": personal, "productos": productos, "cierres": cierres,
            "locales": locales, "actualizado": actualizado, "primer_dia": primer_dia}


def enviar_codigo():
    with cerrojo:
        ahora = time.time()
        if ahora - codigo_actual["enviado"] < 30:
            return False
        codigo_actual.update(valor=f"{secrets.randbelow(10 ** 6):06d}", caduca=ahora + CODIGO_VALIDEZ,
                             intentos=0, enviado=ahora)
        valor = codigo_actual["valor"]
    texto = f"🔐 Código para entrar en la app de La Dulce Alianza: <b>{valor}</b>\nCaduca en 10 minutos."
    for uid in USUARIOS:
        datos_tg = urllib.parse.urlencode({"chat_id": uid, "text": texto, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{cfg['TELEGRAM_TOKEN']}/sendMessage",
                               data=datos_tg, timeout=20).read()
    return True


def comprobar_codigo(valor):
    with cerrojo:
        c = codigo_actual
        if not c["valor"] or time.time() > c["caduca"] or c["intentos"] >= MAX_INTENTOS:
            return False
        c["intentos"] += 1
        if not secrets.compare_digest(str(valor), c["valor"]):
            return False
        c["valor"] = None
    return True


class Manejador(http.server.BaseHTTPRequestHandler):
    server_version = "LDA"

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/datos"):
            print(f"{self.address_string()} {fmt % args}", flush=True)

    def responder(self, codigo, cuerpo, tipo="application/json", cabeceras=()):
        if isinstance(cuerpo, (dict, list)):
            cuerpo = json.dumps(cuerpo, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store" if tipo == "application/json" else "no-cache")
        for k, v in cabeceras:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def sesion_valida(self):
        for trozo in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = trozo.strip().partition("=")
            if k == "lda" and v:
                caduca = leer_sesiones().get(v)
                return bool(caduca and caduca > time.time())
        return False

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/datos":
            if not self.sesion_valida():
                return self.responder(401, {"error": "sesion"})
            q = urllib.parse.parse_qs(url.query)
            try:
                desde = dt.date.fromisoformat(q["desde"][0])
                hasta = dt.date.fromisoformat(q["hasta"][0])
            except (KeyError, ValueError):
                return self.responder(400, {"error": "fechas"})
            if hasta < desde or (hasta - desde).days > 1200:
                return self.responder(400, {"error": "rango"})
            return self.responder(200, datos(desde, hasta, (q.get("local") or [None])[0]))
        if url.path == "/api/salir":
            return self.responder(200, {"ok": True},
                                  cabeceras=[("Set-Cookie", "lda=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Strict")])
        ruta = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        fichero = (WEB / ruta).resolve()
        if WEB.resolve() not in fichero.parents or not fichero.is_file():
            return self.responder(404, b"No encontrado", "text/plain")
        self.responder(200, fichero.read_bytes(), TIPOS.get(fichero.suffix, "application/octet-stream"))

    def do_POST(self):
        largo = min(int(self.headers.get("Content-Length") or 0), 1000)
        try:
            cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        except ValueError:
            cuerpo = {}
        if self.path == "/api/codigo":
            try:
                enviado = enviar_codigo()
            except Exception as e:
                print("Telegram:", e, flush=True)
                return self.responder(502, {"error": "telegram"})
            return self.responder(200, {"ok": True, "enviado": enviado})
        if self.path == "/api/entrar":
            if not comprobar_codigo(cuerpo.get("codigo", "")):
                return self.responder(403, {"error": "codigo"})
            token = secrets.token_urlsafe(32)
            s = {k: v for k, v in leer_sesiones().items() if v > time.time()}
            s[token] = time.time() + DURACION_SESION
            guardar_sesiones(s)
            cookie = f"lda={token}; Max-Age={DURACION_SESION}; Path=/; HttpOnly; Secure; SameSite=Strict"
            return self.responder(200, {"ok": True}, cabeceras=[("Set-Cookie", cookie)])
        self.responder(404, {"error": "no existe"})


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    print(f"app en http://127.0.0.1:{PUERTO}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
