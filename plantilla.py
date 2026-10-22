"""
Área de cada trabajador (Cafetería, Tienda, Charcutería, Obrador) para repartir las horas fichadas.

Desde 2025 todos fichan en el TPV de la tienda de su local, así que el fichaje no dice el área.
Se deduce de dónde cobra cada uno (ventas_empleado) y Carlos la confirma o corrige en la app;
lo que él elige se guarda en datos/plantilla.json y manda sobre la propuesta.

Nombres: en presencia cada persona es «NOMBRE INICIALES» (iniciales de los dos apellidos, p. ej.
«ANTONIO NM»). Para la PDA tiene subcódigos «M-NOMBRE APELLIDO» (mañana) y «T-NOMBRE APELLIDO»
(tarde): «T-ANTONIO NIETO» es «ANTONIO NM». Se unen dentro del mismo local si los nombres coinciden
y la inicial del primer apellido es la del apellido de la PDA.
"""
import datetime as dt
import json
import re
from pathlib import Path

import presencia as base
import ventas

FICHERO = Path(__file__).resolve().parent / "datos" / "plantilla.json"
AREAS = ["Cafetería", "Tienda", "Charcutería", "Obrador", "Otra"]
SIN_AREA = "Sin asignar"
MIN_TICKETS = 10        # por debajo, no se propone área
DIAS_PROPUESTA = 90     # ventas de los últimos días de cada persona que se usan para proponer


def area_venta(estab):
    """Área de una caja de venta (ya fusionadas Bollería → Tienda y Sótano → Cafetería)."""
    e = ventas.FUSIONAR.get(estab.strip(), estab.strip()).lower()
    if e.startswith("cafeteria"):
        return "Cafetería"
    if e.startswith("charcuteria"):
        return "Charcutería"
    if e.startswith("tienda"):
        return "Tienda"
    return None


def area_fichaje(estab):
    """Área que ya dice el TPV donde se fichó (2023-2024 se fichaba en cada área), o None si es
    el TPV de la tienda, donde ficha todo el local."""
    e = estab.strip().lower()
    if e == "obrador":
        return "Obrador"
    if e.startswith("cafeteria") or "sotano" in e:
        return "Cafetería"
    if e.startswith("charcuteria"):
        return "Charcutería"
    if "bolleria" in e:
        return "Tienda"
    return None


def local_fichaje(estab):
    return base.LOCAL_PRESENCIA.get(estab, base.NOMBRE_ESTAB.get(estab, estab.strip()))


ABREVIATURAS = {"FCO": "FRANCISCO", "FCO.": "FRANCISCO", "Mª": "MARIA", "Mª.": "MARIA", "JOSÉ": "JOSE"}
PDA = re.compile(r"^[MT]-")
PARTICULAS = {"DE", "DEL", "LA", "LAS", "LOS", "Y"}


def normal(nombre):
    return " ".join(ABREVIATURAS.get(t, t) for t in nombre.upper().split())


def encaja(pda, real):
    """Grado en que `real` («NOMBRE INICIALES») es la persona del nombre de venta `pda` (sin M-/T-):
    3 apellido(s) iguales, 2 trae ya las iniciales, 1 iniciales de los apellidos, 0 no encaja.
    Admite «NOMBRE APELLIDO», «NOMBRE APELLIDO APELLIDO», «NOMBRE INICIALES» y solo «NOMBRE» (1)."""
    p = [t for t in pda.split() if t not in PARTICULAS]
    r = [t for t in real.split() if t not in PARTICULAS]
    if len(r) < 2:
        return 0
    nombres_r, iniciales = r[:-1], r[-1]
    mejor = 0
    for k in range(1, len(p) + 1):
        nombres, resto = p[:k], p[k:]
        if nombres_r[:k] != nombres:
            continue
        if not resto:
            mejor = max(mejor, 1)                                  # solo el nombre
        elif resto == r[k:]:
            mejor = max(mejor, 3)                                  # mismos apellidos
        elif len(resto) == 1 and resto[0] == iniciales:
            mejor = max(mejor, 2)                                  # ya trae las iniciales
        elif iniciales.startswith("".join(t[0] for t in resto)):
            mejor = max(mejor, 1)                                  # apellido(s) enteros
    return mejor


def resolver(nombre_venta, dia, candidatos):
    """Trabajador de presencia de un nombre de venta, o None si no es claro. `candidatos` es
    {nombre: días con fichaje} del local. Gana el que mejor encaja; si empatan, quien fichó ese día
    y, si no, quien fichó en los 60 días de alrededor (cuando se ficha con la PDA, el usuario
    real no tiene fichaje ese día)."""
    n = normal(nombre_venta)
    if n in candidatos:
        return n
    grados = {c: encaja(PDA.sub("", n), c) for c in candidatos}
    tope = max(grados.values(), default=0)
    encajan = [c for c, g in grados.items() if g and g == tope]
    if len(encajan) > 1:
        ese_dia = [c for c in encajan if dia in candidatos[c]]
        if ese_dia:
            encajan = ese_dia
        else:
            d = dt.date.fromisoformat(dia)
            cerca = lambda c: any(abs((dt.date.fromisoformat(x) - d).days) <= 60 for x in candidatos[c])
            encajan = [c for c in encajan if cerca(c)]
    return encajan[0] if len(encajan) == 1 else None


def leer_manual():
    try:
        return json.loads(FICHERO.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def guardar_manual(empleado, area):
    m = leer_manual()
    if area:
        m[empleado] = area
    else:
        m.pop(empleado, None)
    FICHERO.write_text(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True))
    return m


# Propuestas y candidatos se recalculan solo cuando cambian las tablas (cada carga del histórico)
_memoria = {}


def _firma(con, tabla):
    return con.execute(f"SELECT COUNT(*), MAX(dia) FROM {tabla}").fetchone()


def _recordado(clave, firma, calcular):
    if _memoria.get(clave, (None,))[0] != firma:
        _memoria[clave] = (firma, calcular())
    return _memoria[clave][1]


def candidatos_por_local(con):
    """local → {nombre de presencia: días que fichó}. Los subcódigos M-/T- no son personas."""
    return _recordado("candidatos", _firma(con, "fichajes"), lambda: _candidatos(con))


def _candidatos(con):
    candidatos = {}
    for estab, emp, dia in con.execute("SELECT DISTINCT estab, empleado, dia FROM fichajes"):
        nombre = normal(emp)
        if not PDA.match(nombre):
            candidatos.setdefault(local_fichaje(estab), {}).setdefault(nombre, set()).add(dia)
    return candidatos


def identificador(con):
    """Función (empleado, estab, día) → nombre de la persona real. A veces se ficha con el
    subcódigo de PDA (M-/T-) en vez del usuario de presencia: se pasa a su trabajador."""
    candidatos = candidatos_por_local(con)
    cache = {}

    def quien(empleado, estab, dia):
        n = normal(empleado)
        if not PDA.match(n):
            return n
        k = (n, local_fichaje(estab), dia)
        if k not in cache:
            cache[k] = resolver(n, dia, candidatos.get(k[1], {})) or n
        return cache[k]
    return quien


def propuestas(con):
    """{empleado: {local, propuesta, tickets, reparto: {área: tickets}}} y nombres de venta sin unir."""
    firma = (_firma(con, "ventas_empleado"), _firma(con, "fichajes"))
    return _recordado("propuestas", firma, lambda: _propuestas(con))


def _propuestas(con):
    candidatos = candidatos_por_local(con)
    ultimo = dict(con.execute("SELECT empleado, MAX(dia) FROM ventas_empleado GROUP BY empleado"))
    res, sin_unir = {}, {}
    filas = con.execute("SELECT dia, estab, local, empleado, docs FROM ventas_empleado")
    for dia, estab, local, emp, docs in filas:
        area = area_venta(estab)
        if not area:
            continue
        # Solo los últimos DIAS_PROPUESTA días de cada nombre de venta: el área actual de la persona
        fin = dt.date.fromisoformat(ultimo[emp])
        if dt.date.fromisoformat(dia) < fin - dt.timedelta(days=DIAS_PROPUESTA):
            continue
        quien = resolver(emp, dia, candidatos.get(local, {}))
        if not quien:
            sin_unir[emp] = sin_unir.get(emp, 0) + docs
            continue
        p = res.setdefault(quien, {"local": local, "reparto": {}})
        p["reparto"][area] = p["reparto"].get(area, 0) + docs
    for p in res.values():
        p["tickets"] = sum(p["reparto"].values())
        mejor = max(p["reparto"], key=p["reparto"].get)
        p["propuesta"] = mejor if p["tickets"] >= MIN_TICKETS else None
    return res, sin_unir


def areas_por_tpv(con):
    """{persona: área} de quien alguna vez fichó en un TPV de área (cafetería, charcutería, obrador…):
    la más frecuente. Vale también para sus fichajes en el TPV de la tienda."""
    def calcular():
        quien = identificador(con)
        cuenta = {}
        for estab, emp, dia in con.execute("SELECT estab, empleado, dia FROM fichajes"):
            a = area_fichaje(estab)
            if a:
                c = cuenta.setdefault(quien(emp, estab, dia), {})
                c[a] = c.get(a, 0) + 1
        return {p: max(c, key=c.get) for p, c in cuenta.items()}
    return _recordado("tpv", _firma(con, "fichajes"), calcular)


def asignador(con):
    """Función (empleado, estab del fichaje, día) → área: TPV del área de ese fichaje > elegida a
    mano > área de la persona por otros fichajes en TPV de área > propuesta por ventas.
    El empleado se pasa antes a la persona real (fichajes hechos con el subcódigo de PDA)."""
    manual = leer_manual()
    prop, _ = propuestas(con)
    tpv = areas_por_tpv(con)
    quien = identificador(con)

    def area(empleado, estab, dia=None):
        nombre = quien(empleado, estab, dia) if dia else normal(empleado)
        return (area_fichaje(estab) or manual.get(nombre) or tpv.get(nombre)
                or (prop.get(nombre) or {}).get("propuesta") or SIN_AREA)
    return area


def horas_fichajes(filas):
    """[(fichaje, horas, estimado)]: un olvido de salida se cuenta hasta la última salida normal
    de ese local ese día (o 8 h), como en el € por hora de Telegram."""
    ultima = {}
    for f in filas:
        if f["fin"] and not base.es_olvido(f):
            k = (f["dia"], local_fichaje(f["estab"]))
            ultima[k] = max(ultima.get(k, ""), f["fin"])
    res = []
    for f in filas:
        if base.es_olvido(f):
            fin = ultima.get((f["dia"], local_fichaje(f["estab"])))
            if fin and fin > f["inicio"]:
                h = (dt.datetime.fromisoformat(fin) - dt.datetime.fromisoformat(f["inicio"])).total_seconds() / 3600
            else:
                h = 8.0
            res.append((f, min(h, base.MAX_HORAS), True))
        else:
            res.append((f, f["horas"], False))
    return res


def resumen(con, desde, hasta):
    """Para la pantalla de plantilla: cada persona que ha fichado entre `desde` y `hasta` con su
    local, área efectiva y de dónde sale (manual, TPV del área o propuesta por ventas)."""
    manual = leer_manual()
    prop, sin_unir = propuestas(con)
    quien = identificador(con)
    gente = {}
    for estab, emp, dia, horas in con.execute(
            "SELECT estab, empleado, dia, horas FROM fichajes WHERE dia BETWEEN ? AND ?", (desde, hasta)):
        if emp in base.NO_PLANTILLA or local_fichaje(estab) in ("Oficina Central", "Opera Prima"):
            continue
        nombre = quien(emp, estab, dia)
        g = gente.setdefault(nombre, {"empleado": nombre, "locales": {}, "tpv": None, "horas": 0.0})
        loc = local_fichaje(estab)
        g["locales"][loc] = g["locales"].get(loc, 0) + 1
        g["horas"] += min(horas or 0, base.MAX_HORAS)
        g["tpv"] = g["tpv"] or area_fichaje(estab)
    res = []
    for nombre, g in gente.items():
        p = prop.get(nombre) or {}
        origen, area = ("manual", manual[nombre]) if nombre in manual else \
            ("tpv", g["tpv"]) if g["tpv"] else ("propuesta", p.get("propuesta")) if p.get("propuesta") else (None, SIN_AREA)
        res.append({"empleado": nombre, "local": max(g["locales"], key=g["locales"].get), "area": area,
                    "origen": origen, "propuesta": p.get("propuesta"), "tickets": p.get("tickets", 0),
                    "reparto": p.get("reparto", {}), "manual": manual.get(nombre), "horas": round(g["horas"], 1)})
    res.sort(key=lambda r: (r["local"], r["area"] != SIN_AREA, r["empleado"]))
    return {"personas": res, "areas": AREAS, "sin_unir": sorted(sin_unir.items(), key=lambda x: -x[1])[:15]}
