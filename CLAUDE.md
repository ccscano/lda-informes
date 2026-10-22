# Informes La Dulce Alianza (VPS)

Informes automáticos de HiOffice (Hiopos) por Telegram para Carlos Cano
(Administración, c.cano@ladulcealianza.com). Idioma de trabajo: español.

Esta carpeta es `/home/lda/presencia` en el VPS `vps-1bc13e95` (OVH). El usuario `lda`
no tiene sudo, a propósito: en este servidor también corren los advisors de inversión y el
IB Gateway (usuario `advisor`), que **no se tocan** desde aquí.

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
