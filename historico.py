#!/usr/bin/env python3
"""
Histórico de HiOffice en SQLite (datos/historico.db) para la app de La Dulce Alianza.

Guarda, por día, lo mismo que leen los informes de Telegram, reutilizando sus consultas:
  - ventas por establecimiento (ventas.ventas_dia) y por hora (ventas.ventas_horas)
  - artículos por establecimiento (productos.articulos_dia)
  - fichajes cerrados (presencia.consultar)
  - cierres de caja (cierres.cierres)

Uso:
  historico.py                         refresco diario: ventas/productos/cierres de los últimos
                                       3 días (y hoy) y fichajes de los últimos 21 días
  historico.py --desde 2025-01-01      carga inicial desde esa fecha hasta hoy
  historico.py --desde X --hasta Y     solo ese rango
  historico.py --desde X --solo ventas,productos
"""
import argparse
import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cierres
import presencia as base
import productos
import ventas

DB = Path(__file__).resolve().parent / "datos" / "historico.db"
TRAMO_DIAS = 14        # fichajes y cierres se piden en bloques de 14 días
PAUSA = 0.3            # segundos entre consultas, para no cargar HiOffice

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ventas (
    dia TEXT, estab TEXT, local TEXT, grupo TEXT, neto REAL, docs REAL, uds REAL,
    PRIMARY KEY (dia, estab));
CREATE TABLE IF NOT EXISTS ventas_hora (
    dia TEXT, estab TEXT, local TEXT, grupo TEXT, hora INTEGER, neto REAL, docs REAL,
    PRIMARY KEY (dia, estab, hora));
CREATE TABLE IF NOT EXISTS productos (
    dia TEXT, estab TEXT, local TEXT, articulo TEXT, uds REAL, venta REAL, docs REAL,
    PRIMARY KEY (dia, estab, articulo));
CREATE TABLE IF NOT EXISTS fichajes (
    id TEXT PRIMARY KEY, dia TEXT, inicio TEXT, fin TEXT, estab TEXT, empleado TEXT, horas REAL);
CREATE TABLE IF NOT EXISTS cierres (
    estab TEXT, caja TEXT, numero INTEGER, dia TEXT, fecha TEXT, local TEXT, empleado TEXT,
    venta REAL, docs REAL, efectivo REAL, entrada REAL, salida REAL, calculado REAL,
    declarado REAL, descuadre REAL, retirado REAL, fianza REAL,
    PRIMARY KEY (estab, caja, numero));
CREATE TABLE IF NOT EXISTS cargas (tabla TEXT, dia TEXT, actualizado TEXT, PRIMARY KEY (tabla, dia));
CREATE INDEX IF NOT EXISTS ix_productos_dia ON productos (dia);
CREATE INDEX IF NOT EXISTS ix_fichajes_dia ON fichajes (dia);
CREATE INDEX IF NOT EXISTS ix_cierres_dia ON cierres (dia);
"""


def conectar():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)   # la app lee a la vez; se espera si está ocupada
    con.executescript(ESQUEMA)
    return con


def dias(desde, hasta):
    d = desde
    while d <= hasta:
        yield d
        d += dt.timedelta(days=1)


def tramos(desde, hasta):
    d = desde
    while d <= hasta:
        fin = min(d + dt.timedelta(days=TRAMO_DIAS - 1), hasta)
        yield d, fin
        d = fin + dt.timedelta(days=1)


def marcar(con, tabla, dia):
    con.execute("INSERT OR REPLACE INTO cargas VALUES (?, ?, ?)",
                (tabla, dia.isoformat(), dt.datetime.now().isoformat(timespec="seconds")))


def reintentar(fn, *args):
    """HiOffice da 500 de vez en cuando: tres intentos antes de rendirse con ese día."""
    for intento in range(3):
        try:
            return fn(*args)
        except Exception as e:
            if intento == 2:
                raise
            print(f"  {fn.__name__} falló ({str(e)[:80]}); reintento", file=sys.stderr, flush=True)
            time.sleep(10)


def cargar_ventas(con, token, desde, hasta):
    for d in dias(desde, hasta):
        tiendas, _ = reintentar(ventas.ventas_dia, token, d)
        con.execute("DELETE FROM ventas WHERE dia = ?", (d.isoformat(),))
        con.executemany("INSERT INTO ventas VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(d.isoformat(), n.strip(), ventas.local(n), ventas.grupo(n), t["neto"], t["docs"], t["uds"])
                         for n, t in tiendas.items()])
        marcar(con, "ventas", d)
        con.commit()
        time.sleep(PAUSA)


def cargar_ventas_hora(con, token, desde, hasta):
    for d in dias(desde, hasta):
        filas = reintentar(ventas.ventas_horas, token, d)
        con.execute("DELETE FROM ventas_hora WHERE dia = ?", (d.isoformat(),))
        con.executemany("INSERT INTO ventas_hora VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(d.isoformat(), f["estab"], ventas.local(f["estab"]), ventas.grupo(f["estab"]),
                          f["hora"], f["neto"], f["docs"]) for f in filas])
        marcar(con, "ventas_hora", d)
        con.commit()
        time.sleep(PAUSA)


def cargar_productos(con, token, desde, hasta):
    for d in dias(desde, hasta):
        filas = reintentar(productos.articulos_dia, token, d)
        suma = {}
        for f in filas:   # tras fusionar Bollería en Tienda Paseo un artículo puede repetirse
            s = suma.setdefault((f["estab"], f["articulo"]), [0, 0, 0])
            s[0] += f["uds"]
            s[1] += f["venta"]
            s[2] += f["docs"]
        con.execute("DELETE FROM productos WHERE dia = ?", (d.isoformat(),))
        con.executemany("INSERT INTO productos VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(d.isoformat(), e, ventas.local(e), a, u, v, dc) for (e, a), (u, v, dc) in suma.items()])
        marcar(con, "productos", d)
        con.commit()
        time.sleep(PAUSA)


def cargar_fichajes(con, token, desde, hasta):
    for a, b in tramos(desde, hasta):
        filas = reintentar(base.consultar, token, a, b)
        con.executemany("INSERT OR REPLACE INTO fichajes VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(f["id"], f["inicio"][:10], f["inicio"], f["fin"], f["estab"], f["empleado"], f["horas"])
                         for f in filas])
        for d in dias(a, b):
            marcar(con, "fichajes", d)
        con.commit()
        time.sleep(PAUSA)


def cargar_cierres(con, token, desde, hasta):
    for a, b in tramos(desde, hasta):
        filas = reintentar(cierres.cierres, token, a, b)
        con.executemany("INSERT OR REPLACE INTO cierres VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(c["estab"], c["caja"], c["numero"], c["fecha"][:10], c["fecha"], ventas.local(c["estab"]),
                          c["empleado"], c["venta"], c["docs"], c["efectivo"], c["entrada"], c["salida"],
                          c["calculado"], c["declarado"], c["descuadre"], c["retirado"], c["fianza"])
                         for c in filas if c["fecha"]])
        for d in dias(a, b):
            marcar(con, "cierres", d)
        con.commit()
        time.sleep(PAUSA)


CARGAS = {"ventas": cargar_ventas, "ventas_hora": cargar_ventas_hora, "productos": cargar_productos,
          "fichajes": cargar_fichajes, "cierres": cargar_cierres}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--desde", type=dt.date.fromisoformat)
    p.add_argument("--hasta", type=dt.date.fromisoformat)
    p.add_argument("--solo", default=",".join(CARGAS))
    a = p.parse_args()
    hoy = dt.date.today()
    hasta = a.hasta or hoy
    cfg = base.cargar_env()
    token = base.login(cfg)
    con = conectar()
    for nombre in a.solo.split(","):
        # En el refresco diario los fichajes miran más atrás: un olvido de salida
        # solo aparece cuando la persona vuelve a fichar
        desde = a.desde or hoy - dt.timedelta(days=base.DIAS_VENTANA if nombre == "fichajes" else 3)
        t0 = time.time()
        print(f"{nombre}: {desde} → {hasta}", flush=True)
        try:
            CARGAS[nombre](con, token, desde, hasta)
        except Exception as e:   # que un fallo en una tabla no impida cargar las demás
            print(f"  {nombre} con error: {e}", file=sys.stderr, flush=True)
            continue
        print(f"  hecho en {time.time() - t0:.0f} s", flush=True)
    con.close()


if __name__ == "__main__":
    main()
