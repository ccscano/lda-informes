#!/usr/bin/env python3
"""
App de La Dulce Alianza: panel web (PWA, icono en el móvil) sobre datos/historico.db.

Servicio permanente (systemd de usuario) que escucha en 127.0.0.1:8765; se publica por
HTTPS con un proxy delante. Sirve la web de app/ y una API de solo lectura:

  POST /api/codigo        manda un código de 6 cifras por Telegram a TELEGRAM_USUARIOS
  POST /api/entrar        {"codigo": "123456"} → cookie de sesión de un año
  GET  /api/datos?desde=AAAA-MM-DD&hasta=AAAA-MM-DD[&local=Paseo]
                          si el periodo llega a hoy, antes lee hoy en directo de HiOffice;
                          &corte=1 corta el último día a la hora actual (comparar con hoy en curso)
  GET  /api/salir

Los datos los carga historico.py; la app solo consulta HiOffice para el día de hoy.
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
DIRECTO_CADA = 60              # segundos mínimos entre dos lecturas en directo de hoy en HiOffice
TOKEN_VIDA = 1200              # se rehace el login de HiOffice cada 20 minutos
TIPOS = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json",
         ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml", ".png": "image/png"}

cfg = base.cargar_env()
USUARIOS = [x.strip() for x in cfg.get("TELEGRAM_USUARIOS", "").replace(";", ",").split(",") if x.strip()]
cerrojo = threading.Lock()
codigo_actual = {"valor": None, "caduca": 0, "intentos": 0, "enviado": 0}
cerrojo_directo = threading.Lock()
directo = {"token": None, "token_t": 0, "ultimo": 0}


def refrescar_hoy():
    """Lee de HiOffice las ventas, artículos y cierres de hoy y los guarda en el histórico.
    Como mucho una vez por minuto; si HiOffice falla se sirve lo último guardado."""
    with cerrojo_directo:
        if time.time() - directo["ultimo"] < DIRECTO_CADA:
            return directo["ultimo"]
        hoy = dt.date.today()
        for intento in range(2):
            try:
                if not directo["token"] or time.time() - directo["token_t"] > TOKEN_VIDA:
                    directo["token"], directo["token_t"] = base.login(cfg), time.time()
                con = historico.conectar()
                try:
                    historico.cargar_ventas(con, directo["token"], hoy, hoy)
                    historico.cargar_ventas_hora(con, directo["token"], hoy, hoy)
                    historico.cargar_productos(con, directo["token"], hoy, hoy)
                    historico.cargar_cierres(con, directo["token"], hoy, hoy)
                finally:
                    con.close()
                directo["ultimo"] = time.time()
                break
            except (Exception, SystemExit) as e:   # login() hace sys.exit si no hay token
                print(f"directo: {str(e)[:120]}", flush=True)
                directo["token"] = None   # el token puede haber caducado: se rehace el login
        return directo["ultimo"]


def asegurar_horas(dia):
    """Las ventas por hora de un día pasado; si aún no están en el histórico, se leen de HiOffice."""
    with sqlite3.connect(f"file:{historico.DB}?mode=ro", uri=True) as con:
        if con.execute("SELECT 1 FROM cargas WHERE tabla = 'ventas_hora' AND dia = ?", (dia.isoformat(),)).fetchone():
            return True
    with cerrojo_directo:
        try:
            if not directo["token"] or time.time() - directo["token_t"] > TOKEN_VIDA:
                directo["token"], directo["token_t"] = base.login(cfg), time.time()
            con = historico.conectar()
            try:
                historico.cargar_ventas_hora(con, directo["token"], dia, dia)
            finally:
                con.close()
            return True
        except (Exception, SystemExit) as e:
            print(f"horas {dia}: {str(e)[:120]}", flush=True)
            directo["token"] = None
            return False


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


def datos(desde, hasta, local=None, corte=None):
    """Agregados del periodo. Con `corte` (una hora), el último día cuenta solo hasta esa hora, para
    comparar con hoy en curso: ventas por hora (la hora del corte, en proporción a los minutos),
    artículos en la misma proporción por local, y sin fichajes de ese día (los de hoy no están cerrados)."""
    cortar = corte is not None and hasta < dt.date.today() and asegurar_horas(hasta)
    con = sqlite3.connect(f"file:{historico.DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    d0, d1 = desde.isoformat(), hasta.isoformat()
    filtro, args = ("AND local = ?", [local]) if local else ("", [])

    ventas = [dict(r) for r in con.execute(
        f"SELECT dia, local, grupo, SUM(neto) neto, SUM(docs) docs FROM ventas "
        f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY dia, local, grupo ORDER BY dia", [d0, d1, *args])]
    proporcion = {}   # local → parte de la venta del último día hecha antes del corte
    if cortar:
        h, frac = corte.hour, corte.minute / 60
        hasta_corte = [dict(r, dia=d1) for r in con.execute(
            f"SELECT local, grupo, SUM(CASE WHEN hora < ? THEN neto WHEN hora = ? THEN neto * ? ELSE 0 END) neto, "
            f"SUM(CASE WHEN hora < ? THEN docs WHEN hora = ? THEN docs * ? ELSE 0 END) docs "
            f"FROM ventas_hora WHERE dia = ? {filtro} GROUP BY local, grupo", [h, h, frac, h, h, frac, d1, *args])]
        completo = {}
        for v in ventas:
            if v["dia"] == d1:
                completo[v["local"]] = completo.get(v["local"], 0) + v["neto"]
        cortado = {}
        for v in hasta_corte:
            cortado[v["local"]] = cortado.get(v["local"], 0) + v["neto"]
        proporcion = {k: (cortado.get(k, 0) / x if x else 0) for k, x in completo.items()}
        ventas = [v for v in ventas if v["dia"] != d1] + hasta_corte

    # Horas por local y día con la misma regla que el €/h de Telegram (olvidos estimados)
    fin_fich = (hasta - dt.timedelta(days=1)).isoformat() if corte is not None else d1
    fich = [dict(r) for r in con.execute(
        "SELECT * FROM fichajes WHERE dia BETWEEN ? AND ? ORDER BY inicio", [d0, fin_fich])]
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

    if cortar:
        suma = {}
        filas = con.execute(
            f"SELECT dia, local, articulo, SUM(uds) uds, SUM(venta) venta FROM productos "
            f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY dia = ?, local, articulo", [d0, d1, *args, d1])
        for r in filas:
            f = proporcion.get(r["local"], 0) if r["dia"] == d1 else 1
            a = suma.setdefault(r["articulo"], [0, 0])
            a[0] += r["uds"] * f
            a[1] += r["venta"] * f
        productos = sorted(({"articulo": k, "uds": u, "venta": v} for k, (u, v) in suma.items()),
                           key=lambda p: -p["venta"])[:1000]
    else:
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
    return {"desde": d0, "hasta": d1, "local": local, "corte": corte.strftime("%H:%M") if corte else None,
            "ventas": ventas, "horas": horas,
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
            en_directo = None
            if hasta >= dt.date.today():
                ts = refrescar_hoy()
                en_directo = dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else None
            corte = dt.datetime.now() if q.get("corte") == ["1"] else None
            res = datos(desde, hasta, (q.get("local") or [None])[0], corte)
            res["directo"] = en_directo
            return self.responder(200, res)
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
