#!/usr/bin/env python3
"""
Resumen semanal y mensual de ventas y top 10 productos por Telegram.

  periodos.py --semana [AAAA-MM-DD]   semana lunes-domingo cerrada (la última, o la que
                                      contenga la fecha), comparada con la anterior
  periodos.py --mes [AAAA-MM-DD]      mes cerrado (el último, o el de la fecha),
                                      comparado con el mes anterior
  --no-enviar                         imprime en vez de enviar

Timers: los lunes a las 08:00 (semana) y el día 1 a las 08:00 (mes).
"""
import calendar
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presencia as base
import productos
import ventas

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def semana_cerrada(ref):
    """(lunes, domingo) de la última semana completa anterior o igual a ref."""
    domingo = ref - dt.timedelta(days=ref.weekday() + 1)
    return domingo - dt.timedelta(days=6), domingo


def mes_cerrado(ref):
    """(día 1, último día) del último mes completo anterior a ref."""
    primero_actual = ref.replace(day=1)
    fin = primero_actual - dt.timedelta(days=1)
    return fin.replace(day=1), fin


def mes_anterior(desde):
    fin = desde - dt.timedelta(days=1)
    return fin.replace(day=1), fin


def resumen(token, desde, hasta, desde_ant, hasta_ant, titulo, titulo_prod, etiqueta_ant):
    tiendas, totales = ventas.ventas_dia(token, desde, hasta)
    tiendas_ant, totales_ant = ventas.ventas_dia(token, desde_ant, hasta_ant)
    dias = (hasta - desde).days + 1
    texto_v = ventas.mensaje(hasta, 23, tiendas, totales, tiendas_ant, totales_ant, titulo, etiqueta_ant,
                             con_importe=True)
    if totales["neto"]:
        texto_v += f"\n<i>Media diaria: {ventas.eur(totales['neto'] / dias)} ({dias} días).</i>"
    filas = productos.articulos_dia(token, desde, hasta)
    filas_ant = productos.articulos_dia(token, desde_ant, hasta_ant)
    partes = productos.mensajes(hasta, 23, filas, solo_locales=True, titulo=titulo_prod,
                                filas_ant=filas_ant, nota_ant=f"importe y unidades de {etiqueta_ant}")
    return [texto_v] + partes


def main():
    cfg = base.cargar_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ref = dt.date.fromisoformat(args[0]) if args else dt.date.today()
    token = base.login(cfg)
    if "--mes" in sys.argv:
        if args:  # mes de la fecha dada (aunque esté en curso)
            desde = ref.replace(day=1)
            hasta = min(ref.replace(day=calendar.monthrange(ref.year, ref.month)[1]), dt.date.today())
        else:
            desde, hasta = mes_cerrado(ref)
        desde_ant, hasta_ant = mes_anterior(desde)
        nombre = f"{MESES[desde.month - 1]} {desde.year}"
        partes = resumen(token, desde, hasta, desde_ant, hasta_ant,
                         f"📅 <b>VENTAS DEL MES · {nombre}</b> ({desde:%d/%m} – {hasta:%d/%m})",
                         f"🏆 <b>Top {productos.TOP} productos del mes · {nombre}</b>",
                         f"{MESES[desde_ant.month - 1]}")
    else:
        if args:
            desde = ref - dt.timedelta(days=ref.weekday())
            hasta = min(desde + dt.timedelta(days=6), dt.date.today())
        else:
            desde, hasta = semana_cerrada(ref)
        desde_ant, hasta_ant = desde - dt.timedelta(days=7), hasta - dt.timedelta(days=7)
        partes = resumen(token, desde, hasta, desde_ant, hasta_ant,
                         f"📅 <b>VENTAS DE LA SEMANA</b> {desde:%d/%m} – {hasta:%d/%m}",
                         f"🏆 <b>Top {productos.TOP} productos de la semana</b> {desde:%d/%m} – {hasta:%d/%m}",
                         f"la semana anterior ({desde_ant:%d/%m} – {hasta_ant:%d/%m})")
    texto = "\n\n".join(partes)
    if "--no-enviar" in sys.argv:
        print(texto)
        return
    base.telegram(cfg, texto)
    print(f"Enviado: {desde} – {hasta}.")


if __name__ == "__main__":
    main()
