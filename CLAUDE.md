# Informes La Dulce Alianza (VPS)

Informes automáticos de HiOffice (Hiopos) por Telegram para Carlos Cano
(Administración, c.cano@ladulcealianza.com). Idioma de trabajo: español.

Esta carpeta es `/home/lda/presencia` en el VPS `vps-64775d96` (OVH, 137.74.113.152; alias ssh
`vps-lda` en el Mac de Carlos, usuario ubuntu con sudo). Desde el 22/10/2026 el servidor es solo de
La Dulce Alianza: los advisors de inversión y el IB Gateway siguen en el VPS antiguo (`vps-1bc13e95`,
57.131.163.217), que **no se toca** desde aquí. El usuario `lda` no tiene sudo, a propósito.
Cortafuegos (ufw): solo 22, 80 y 443 abiertos. Montaje inicial: `migracion/preparar_vps.sh`.

## Qué se envía y cuándo
Todo va al canal de Telegram "La Dulce ALianza" (id en `.env`) con el bot @ladulcealianzacom_bot.

| Hora | Script | Contenido |
|---|---|---|
| 04:00 | `presencia.py` | Fichajes de ayer por establecimiento, olvidos de salida, € por hora (y semanal los lunes) |
| 15:00 / 22:10 | `ventas.py` | Ventas del día: cafeterías, tiendas+charcutería, por local, total |
| 15:05 / 22:15 | `productos.py` | Top 10 productos: empresa, por local, por establecimiento |
| 23:00 | `cierres.py` | Cierres de caja (Z): caja, nº Z, hora, persona, importe, efectivo |
| lunes 08:00 | `periodos.py --semana` | Ventas y top 10 de la semana, con importes de la anterior |
| día 1 08:00 | `periodos.py --mes` | Ídem del mes |

`bot_lda.py` (servicio permanente) responde a /ventas /productos /cierres /presencia
/semana /semanal /mensual [fecha] en privado a los ids de `TELEGRAM_USUARIOS` y en el canal.

## App (panel web con icono en el móvil)
- `historico.py` guarda en `datos/historico.db` (SQLite) ventas por establecimiento y por hora, artículos,
  fichajes y cierres, por día, reutilizando las consultas de los informes. Timer `historico.timer`:
  cada hora de 9:20 a 23:20 y a las 04:30 (refresca 3 días; fichajes 21). Carga inicial o
  rehacer un rango: `python3 historico.py --desde 2023-01-01 [--hasta …] [--solo ventas,productos]`.
  Hay ventas en HiOffice desde 2023.
- `app.py` (servicio `app.service`, 127.0.0.1:8765, publicado en https://app.ladulcealianza.com
  con Caddy en /etc/caddy/Caddyfile) sirve `app/` (PWA: index.html, manifest,
  sw.js, iconos) y `/api/datos?desde&hasta[&local]`. Si el periodo llega a hoy, lee hoy en directo
  de HiOffice (como mucho una vez por minuto) y lo guarda en el histórico.
- Comparaciones con un periodo que llega a hoy (`&corte=1`): el último día de la comparación se corta
  a la hora actual con `ventas_hora` (ventas por hora, atributo 1077 de HiOffice; la hora en curso en
  proporción a los minutos) y los artículos en la misma proporción por local. Horas y €/h solo cuentan
  días cerrados (los fichajes de hoy no están completos); en "Hoy" salen "al cierre".
- Áreas (plantilla.py): desde 2025 todos fichan en el TPV de la tienda de su local, así que el área
  de cada persona (Cafetería, Tienda —con bollería—, Charcutería, Obrador) se propone según dónde cobra
  (`ventas_empleado`, últimos 90 días de cada uno) y Carlos la corrige en la app (Personal → Área de cada
  persona; se guarda en `datos/plantilla.json`, que manda sobre la propuesta). Los empleados de venta
  «M-NOMBRE APELLIDO» / «T-…» son subcódigos de PDA de mañana/tarde, no personas: se unen con su usuario
  de presencia «NOMBRE INICIALES» (iniciales de los dos apellidos) dentro del mismo local.
  Fuera de ventas: Obrador, Oficina Central y Opera Prima (`NO_SON_LOCALES` en app.py); el Obrador sí
  sale en Personal. Sótano Paseo se suma a Cafetería Paseo y Bollería a Tienda Paseo (`ventas.FUSIONAR`).
- Excel de explotación (`explotacion.py`, tabla `explotacion`): cuenta de explotación por trimestre,
  local y área (CONFITERIA = Tienda; CENTRO = Paseo). Se sube desde la app (Análisis → Cuenta de
  explotación) o con `python3 explotacion.py fichero.xlsx`; cada año que trae se reemplaza entero.
  Las ventas del Excel van sin IVA: la app guarda también la base imponible (`ventas.base`).
- Indicadores (`analisis.py`, `/api/analisis`): ventas y personas por hora (tramos fichados), mapa de
  calor, ticket medio por franja (desayuno <12, mediodía 12-16, tarde ≥16), coste de personal
  (horas × €/h del trimestre = sueldos+gratificaciones+SS ÷ horas fichadas; % sobre base sin IVA),
  margen bruto como la hoja «Ratios» (resta también «Otras Secciones»), objetivo del mes
  (mismo mes del año anterior × (1 + subida media de los últimos 12 meses)), fichajes a revisar
  (olvidos, turnos > 12 h, local distinto del habitual) y control de cajas (Z que faltan, vacíos,
  % efectivo; el Z de madrugada o mañana cuenta para el día anterior).
- Familias de artículo: atributo 1465 de HiOffice → tabla `familias` (artículo → familia).
- `avisos.py` (avisos.timer, cada hora 10:40-21:40): si un local va ≥25 % por debajo del mismo día
  de la semana pasada a la misma hora, avisa en privado a TELEGRAM_USUARIOS (no al canal).
- Acceso: código de 6 cifras que el bot manda por Telegram a `TELEGRAM_USUARIOS`; la sesión
  (cookie de un año) se guarda en `datos/sesiones.json`.
- Tras cambiar `app.py`: `systemctl --user restart app.service`. Si se cambia algo de `app/`,
  subir la versión de `CACHE` en `app/sw.js`.

## Cómo se gestiona (systemd de usuario, sin sudo)
```bash
systemctl --user list-timers
systemctl --user restart bot-lda.service
systemctl --user start ventas.service          # lanzar un informe ahora
journalctl --user -u ventas.service -n 50
python3 ventas.py 2026-10-20 --no-enviar       # probar sin enviar
```
Unidades en `~/.config/systemd/user/`. Tras editar una unidad: `systemctl --user daemon-reload`.
Tras cambiar `bot_lda.py`: reiniciar `bot-lda.service`.

## Reglas
- **No leer, mostrar ni editar `.env`** (credenciales de HiOffice y token del bot). Los scripts lo leen solos.
- Probar siempre con `--no-enviar` antes de mandar algo al canal; el canal lo ve más gente.
- Tras cambiar código: `git add -A && git commit -m "…" && git push`.
- No tocar nada fuera de `/home/lda`.

## HiOffice por API (dulcealianza01.hiopos.com)
- Login: `presencia.login()` (AES con clave fija del front; reintenta si da 500). Hay que llamar a
  `report/getUser` antes de consultar.
- Datos: `POST /ErpCloud/report/query` con dashboard/block/datasource y columnas con `internalId`
  (ver constantes en cada script). Si se añaden campos extra a las columnas, el servidor deja de agrupar.
- Presencia solo devuelve fichajes **cerrados**; un olvido de salida aparece al volver a fichar
  (tramo que cruza de día o > 14 h). Se avisa una vez (`avisados.json`).
- Ventas por establecimiento: 12 TPV. **Bollería Paseo se suma a Tienda Paseo** (misma caja,
  `ventas.FUSIONAR`). Un "local" agrupa cafetería + tienda (+ charcutería en Paseo): `ventas.local()`.
- Cierres: nadie declara efectivo (declarado = 0), el descuadre no sirve. Torrecárdenas hace el Z
  a la mañana siguiente.
- Excluidos de presencia: SUPERVISOR, TECNICO SEISOFT, JAVIER CS. El Obrador no vende (fuera del €/h).
