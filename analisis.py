"""
Indicadores de la pestaña Análisis de la app (y de Locales, Personal y Cajas) sobre el histórico:

  por_hora    ventas, tickets y personas trabajando en cada hora del día, por área
  mapa        venta media por día de la semana y hora
  franjas     ticket medio de desayuno, mediodía y tarde, por mes
  coste       coste de personal del periodo por local y área: horas fichadas × coste por hora del
              local en el trimestre (Excel: sueldos + gratificaciones + seg. social ÷ horas fichadas)
  margen      cuenta de explotación de los trimestres del periodo (margen bruto, personal, resultados)
  objetivo    mes en curso: objetivo = mismo mes del año anterior × (1 + subida media de los últimos
              12 meses), avance y previsión a fin de mes
  anomalias   fichajes a revisar: olvidos de salida, turnos de más de 12 h, local distinto del habitual
  cajas       cierres que faltan, Z vacíos y % de efectivo por caja
"""
import datetime as dt
import os

import explotacion
import plantilla
import presencia as base

NO_SON_LOCALES = ("Obrador", "Oficina Central", "Opera Prima")
AREA = ("CASE WHEN estab LIKE 'Cafeteria%' THEN 'Cafetería' "
        "WHEN estab LIKE 'Charcuteria%' THEN 'Charcutería' ELSE 'Tienda' END")
FRANJAS = (("Desayuno", 0, 12), ("Mediodía", 12, 16), ("Tarde", 16, 24))   # [desde, hasta) en horas
TURNO_LARGO = 12          # horas: en domingos y festivos se llega a 12 h, así que solo se avisa por encima
MIN_HABITUAL = 10         # fichajes en un local para considerarlo el habitual de una persona


def _filtro(local):
    fuera = f"AND local NOT IN ({', '.join('?' * len(NO_SON_LOCALES))})"
    return (f"AND local = ? {fuera}", [local, *NO_SON_LOCALES]) if local else (fuera, [*NO_SON_LOCALES])


def _trimestre(dia):
    return dia.year, (dia.month - 1) // 3 + 1


def horas_por_dia(con):
    """[(día, local, área, horas, fichaje, inicio, fin)] de todos los fichajes de locales que venden,
    con el área de cada persona. Se recuerda hasta que cambian los fichajes o la plantilla."""
    def calcular():
        area_de = plantilla.asignador(con)
        fich = [dict(zip(("id", "dia", "inicio", "fin", "estab", "empleado", "horas"), r))
                for r in con.execute("SELECT id, dia, inicio, fin, estab, empleado, horas FROM fichajes ORDER BY inicio")]
        res = []
        for f, h, estimado in plantilla.horas_fichajes(fich):
            loc = plantilla.local_fichaje(f["estab"])
            if loc in NO_SON_LOCALES or f["empleado"] in base.NO_PLANTILLA:
                continue
            ini = dt.datetime.fromisoformat(f["inicio"])
            res.append((f["dia"], loc, area_de(f["empleado"], f["estab"], f["dia"]), h, f, ini,
                        ini + dt.timedelta(hours=h), estimado))
        return res
    mtimes = []
    for f in (plantilla.FICHERO, plantilla.DOTACIONES):
        try:
            mtimes.append(os.path.getmtime(f))
        except OSError:
            mtimes.append(0)
    firma = (plantilla._firma(con, "fichajes"), plantilla._firma(con, "ventas_empleado"), *mtimes)
    return plantilla._recordado("horas_dia", firma, calcular)


def por_hora(con, desde, hasta, local):
    """{(área, hora): {ventas, docs, horas}} y nº de días con ventas: personas por hora a partir de
    los tramos fichados (cada tramo reparte sus minutos en las horas que cubre)."""
    filtro, args = _filtro(local)
    res = {}
    for area, hora, neto, docs in con.execute(
            f"SELECT {AREA} area, hora, SUM(neto), SUM(docs) FROM ventas_hora WHERE dia BETWEEN ? AND ? {filtro} "
            f"GROUP BY area, hora", [desde, hasta, *args]):
        r = res.setdefault((area, hora), {"ventas": 0, "docs": 0, "horas": 0})
        r["ventas"] += neto
        r["docs"] += docs
    hoy = dt.date.today().isoformat()      # HiOffice solo da fichajes cerrados: hoy está a medias
    for dia, loc, area, h, f, ini, fin, estimado in horas_por_dia(con):
        if not (desde <= dia <= hasta) or dia >= hoy or (local and loc != local) or estimado:
            continue
        t = ini
        while t < fin:
            siguiente = min(fin, t.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1))
            r = res.setdefault((area, t.hour), {"ventas": 0, "docs": 0, "horas": 0})
            r["horas"] += (siguiente - t).total_seconds() / 3600
            t = siguiente
    dias = con.execute(f"SELECT COUNT(DISTINCT dia) FROM ventas WHERE dia BETWEEN ? AND ? AND neto > 0 {filtro}",
                       [desde, hasta, *args]).fetchone()[0]
    filas = [{"area": a, "hora": h, **{k: round(v, 2) for k, v in r.items()}} for (a, h), r in res.items()]
    return {"dias": dias, "filas": sorted(filas, key=lambda x: (x["area"], x["hora"]))}


def mapa(con, desde, hasta, local):
    """Venta media por día de la semana (0 = domingo) y hora."""
    filtro, args = _filtro(local)
    veces = dict(con.execute(
        f"SELECT CAST(strftime('%w', dia) AS INTEGER) w, COUNT(DISTINCT dia) FROM ventas "
        f"WHERE dia BETWEEN ? AND ? AND neto > 0 {filtro} GROUP BY w", [desde, hasta, *args]))
    return [{"dow": w, "hora": h, "ventas": round(v / veces[w], 2), "docs": round(d / veces[w], 2)}
            for w, h, v, d in con.execute(
                f"SELECT CAST(strftime('%w', dia) AS INTEGER) w, hora, SUM(neto), SUM(docs) FROM ventas_hora "
                f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY w, hora", [desde, hasta, *args]) if veces.get(w)]


def franjas(con, desde, hasta, local):
    """Por mes y franja: ventas, tickets y ticket medio."""
    filtro, args = _filtro(local)
    caso = " ".join(f"WHEN hora >= {a} AND hora < {b} THEN '{n}'" for n, a, b in FRANJAS)
    return [{"mes": m, "franja": f, "ventas": round(v, 2), "docs": d, "ticket": round(v / d, 2) if d else 0}
            for m, f, v, d in con.execute(
                f"SELECT substr(dia, 1, 7) m, CASE {caso} END f, SUM(neto), SUM(docs) FROM ventas_hora "
                f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY m, f ORDER BY m", [desde, hasta, *args])]


def coste_hora(con):
    """{(año, trim, local): €/hora} = coste de personal del local en el trimestre (Excel) ÷ horas
    fichadas en el local. Es por local y no por área porque la nómina de quien cubre otra área
    (p. ej. suplentes de cafetería en la tienda de Rambla) sigue cargada en su área de contrato:
    con el precio del local, el coste sigue a las horas trabajadas de verdad en cada área."""
    horas = {}
    for dia, loc, area, h, *_ in horas_por_dia(con):
        k = (*_trimestre(dt.date.fromisoformat(dia)), loc)
        horas[k] = horas.get(k, 0) + h
    coste = {}
    for anio, trim, loc, imp in con.execute(
            f"SELECT anio, trim, local, SUM(importe) FROM explotacion WHERE concepto IN "
            f"({', '.join('?' * len(explotacion.PERSONAL))}) GROUP BY 1, 2, 3", explotacion.PERSONAL):
        coste[(anio, trim, loc)] = imp
    return {k: coste[k] / horas[k] for k in coste if horas.get(k)}, coste, horas


def coste(con, desde, hasta, local):
    """Por local y área: horas, coste estimado (horas × €/h de su trimestre; si el trimestre aún no
    está en el Excel, el último que haya), ventas con y sin IVA y % de coste sobre ventas sin IVA."""
    try:
        eur_h, _, _ = coste_hora(con)
    except Exception:   # aún no se ha subido el Excel
        return {"filas": [], "ultimo": None}
    ultimo = {}
    for (a, t, loc), v in sorted(eur_h.items()):
        ultimo[loc] = v
    res = {}
    for dia, loc, area, h, *_ in horas_por_dia(con):
        if not (desde <= dia <= hasta) or (local and loc != local):
            continue
        q = _trimestre(dt.date.fromisoformat(dia))
        precio = eur_h.get((*q, loc), ultimo.get(loc))
        r = res.setdefault((loc, area), {"horas": 0, "coste": 0, "sin_precio": 0})
        r["horas"] += h
        if precio:
            r["coste"] += h * precio
        else:
            r["sin_precio"] += h
    filtro, args = _filtro(local)
    for loc, area, neto, b in con.execute(
            f"SELECT local, {AREA} area, SUM(neto), SUM(COALESCE(base, neto / 1.1)) FROM ventas "
            f"WHERE dia BETWEEN ? AND ? {filtro} GROUP BY 1, 2", [desde, hasta, *args]):
        r = res.setdefault((loc, area), {"horas": 0, "coste": 0, "sin_precio": 0})
        r["ventas"], r["base"] = neto, b
    trimestres = sorted({(a, t) for a, t, *_ in eur_h})
    filas = [{"local": l, "area": a, "horas": round(r["horas"], 1), "coste": round(r["coste"], 2),
              "sin_precio": round(r["sin_precio"], 1), "ventas": round(r.get("ventas", 0), 2),
              "base": round(r.get("base", 0), 2),
              "pct": round(r["coste"] / r["base"] * 100, 1) if r.get("base") else None,
              "eur_h": round(r["coste"] / r["horas"], 2) if r["horas"] else None}
             for (l, a), r in sorted(res.items())]
    return {"filas": filas, "ultimo": f"{trimestres[-1][1]}T{trimestres[-1][0]}" if trimestres else None}


def margen(con, desde, hasta, local):
    """Cuenta de explotación de los trimestres que toca el periodo, por local y área."""
    d0, d1 = dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta)
    qs = {_trimestre(d0), _trimestre(d1)}
    a, t = _trimestre(d0)
    while (a, t) < _trimestre(d1):
        t += 1
        if t == 5:
            a, t = a + 1, 1
        qs.add((a, t))
    try:
        filas = con.execute("SELECT anio, trim, local, area, concepto, importe FROM explotacion").fetchall()
    except Exception:
        return []
    # Si el Excel aún no llega a los trimestres del periodo, se enseñan los últimos cuatro que tenga
    hay = sorted({(a, t) for a, t, *_ in filas})
    if not qs & set(hay):
        qs = set(hay[-4:])
    res = {}
    for anio, trim, loc, area, concepto, imp in filas:
        if (anio, trim) not in qs or (local and loc != local):
            continue
        r = res.setdefault((anio, trim, loc, area), {"ventas": 0, "coste_ventas": 0, "personal": 0,
                                                     "resultado_directo": 0, "resultado_total": 0})
        if concepto == "Ventas":
            r["ventas"] += imp
        elif concepto in explotacion.COSTE_VENTAS:
            r["coste_ventas"] += imp
        elif concepto in explotacion.PERSONAL:
            r["personal"] += imp
        elif concepto == "RESULTADO DIRECTO":
            r["resultado_directo"] += imp
        elif concepto == "RESULTADO TOTAL":
            r["resultado_total"] += imp
    return [{"trimestre": f"{t}T{a}", "local": l, "area": ar, **{k: round(v, 2) for k, v in r.items()},
             "margen_pct": round((r["ventas"] - r["coste_ventas"]) / r["ventas"] * 100, 1) if r["ventas"] else None,
             "personal_pct": round(r["personal"] / r["ventas"] * 100, 1) if r["ventas"] else None}
            for (a, t, l, ar), r in sorted(res.items())]


def objetivo(con, local, hoy=None):
    """Mes en curso por local: objetivo = mismo mes del año anterior × (1 + subida media de los
    últimos 12 meses cerrados frente a los mismos meses del año antes); previsión a fin de mes =
    venta del año anterior en el mes × ritmo actual (venta hasta hoy ÷ venta del año anterior hasta
    el mismo día)."""
    hoy = hoy or dt.date.today()
    filtro, args = _filtro(local)
    ini = hoy.replace(day=1)
    fin_mes = (ini + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)

    def menos_anio(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:          # 29 de febrero
            return d.replace(year=d.year - 1, day=28)

    def suma(a, b):
        return dict(con.execute(f"SELECT local, SUM(neto) FROM ventas WHERE dia BETWEEN ? AND ? {filtro} "
                                f"GROUP BY local", [a.isoformat(), b.isoformat(), *args]))
    fin12 = ini - dt.timedelta(days=1)
    ini12 = menos_anio(ini)
    ult12, prev12 = suma(ini12, fin12), suma(menos_anio(ini12), menos_anio(fin12))
    actual, ant_mismos = suma(ini, hoy), suma(menos_anio(ini), menos_anio(hoy))
    ant_mes = suma(menos_anio(ini), menos_anio(fin_mes))
    filas = []
    for loc in sorted(set(ant_mes) | set(actual)):
        subida = (ult12.get(loc, 0) / prev12[loc] - 1) if prev12.get(loc) else 0
        obj = ant_mes.get(loc, 0) * (1 + subida)
        ritmo = actual.get(loc, 0) / ant_mismos[loc] if ant_mismos.get(loc) else None
        filas.append({"local": loc, "subida_media": round(subida * 100, 1), "objetivo": round(obj, 2),
                      "actual": round(actual.get(loc, 0), 2), "anterior_mes": round(ant_mes.get(loc, 0), 2),
                      "anterior_a_fecha": round(ant_mismos.get(loc, 0), 2),
                      "prevision": round(ant_mes.get(loc, 0) * ritmo, 2) if ritmo else None})
    return {"mes": ini.strftime("%Y-%m"), "dia": hoy.day, "dias_mes": fin_mes.day, "filas": filas}


def anomalias(con, desde, hasta, local):
    """Fichajes a revisar del periodo: olvido de salida, turno de más de TURNO_LARGO horas y fichaje
    en un local distinto del habitual de la persona (el suyo en los 120 días anteriores)."""
    quien = plantilla.identificador(con)
    d0 = (dt.date.fromisoformat(desde) - dt.timedelta(days=120)).isoformat()
    filas = con.execute("SELECT dia, inicio, fin, estab, empleado, horas FROM fichajes WHERE dia BETWEEN ? AND ? "
                        "ORDER BY inicio", (d0, hasta)).fetchall()
    habitual = {}
    for dia, ini, fin, estab, emp, h in filas:
        if dia < desde:
            c = habitual.setdefault(quien(emp, estab, dia), {})
            loc = plantilla.local_fichaje(estab)
            c[loc] = c.get(loc, 0) + 1
    habitual = {p: max(c, key=c.get) for p, c in habitual.items() if max(c.values()) >= MIN_HABITUAL}
    res = []
    for dia, ini, fin, estab, emp, h in filas:
        if dia < desde or emp in base.NO_PLANTILLA:
            continue
        loc = plantilla.local_fichaje(estab)
        if local and loc != local:
            continue
        f = {"inicio": ini, "fin": fin, "horas": h or 0}
        persona = quien(emp, estab, dia)
        motivos = []
        if base.es_olvido(f):
            motivos.append("olvido de salida")
        elif (h or 0) > TURNO_LARGO:
            motivos.append(f"turno de {h:.1f} h")
        if habitual.get(persona) and habitual[persona] != loc:
            motivos.append(f"suele fichar en {habitual[persona]}")
        if motivos:
            res.append({"dia": dia, "inicio": ini, "fin": fin, "local": loc, "empleado": persona.title(),
                        "horas": round(h or 0, 2), "motivos": motivos})
    return res[-300:][::-1]


def cajas(con, desde, hasta, local):
    """Por caja: ventas, efectivo y su %, Z hechos y vacíos, y días sin Z (de los que tenía que
    hacer: cajas con venta en las 2 semanas anteriores). El día de un Z es el de negocio: un Z de
    madrugada o de mañana (Torrecárdenas lo hace al día siguiente) cuenta para el día anterior."""
    filtro, args = _filtro(local)
    d0 = (dt.date.fromisoformat(desde) - dt.timedelta(days=15)).isoformat()
    d1 = (dt.date.fromisoformat(hasta) + dt.timedelta(days=1)).isoformat()
    filas = con.execute(f"SELECT local, estab, caja, fecha, venta, efectivo, calculado, retirado FROM cierres "
                        f"WHERE dia BETWEEN ? AND ? {filtro} ORDER BY fecha", [d0, d1, *args]).fetchall()
    ayer = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    dias_z, res = {}, {}
    for loc, estab, caja, fecha, venta, efectivo, calculado, retirado in filas:
        negocio = (dt.datetime.fromisoformat(fecha) - dt.timedelta(hours=12)).date().isoformat()
        k = (loc, caja)
        dias_z.setdefault(k, {}).setdefault(negocio, 0)
        dias_z[k][negocio] += venta
        if desde <= negocio <= hasta:
            r = res.setdefault(k, {"local": loc, "caja": caja, "ventas": 0, "efectivo": 0, "retirado": 0,
                                   "z": 0, "vacios": 0, "faltan": []})
            r["ventas"] += venta
            r["efectivo"] += efectivo
            r["retirado"] += retirado
            r["z"] += 1
            r["vacios"] += not (venta or calculado or retirado)
    d = dt.date.fromisoformat(desde)
    while d.isoformat() <= min(hasta, ayer):
        s = d.isoformat()
        antes = (d - dt.timedelta(days=14)).isoformat()
        for k, dias in dias_z.items():
            activa = any(antes <= x < s and v > 0 for x, v in dias.items())
            if activa and s not in dias:
                r = res.setdefault(k, {"local": k[0], "caja": k[1], "ventas": 0, "efectivo": 0, "retirado": 0,
                                       "z": 0, "vacios": 0, "faltan": []})
                r["faltan"].append(s)
        d += dt.timedelta(days=1)
    out = []
    for r in res.values():
        r["pct_efectivo"] = round(r["efectivo"] / r["ventas"] * 100, 1) if r["ventas"] else None
        out.append({k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    return sorted(out, key=lambda r: (r["local"], -r["ventas"]))


def todo(con, desde, hasta, local=None):
    return {"desde": desde, "hasta": hasta, "local": local,
            "por_hora": por_hora(con, desde, hasta, local), "mapa": mapa(con, desde, hasta, local),
            "franjas": franjas(con, desde, hasta, local), "coste": coste(con, desde, hasta, local),
            "margen": margen(con, desde, hasta, local), "objetivo": objetivo(con, local),
            "anomalias": anomalias(con, desde, hasta, local), "cajas": cajas(con, desde, hasta, local)}
