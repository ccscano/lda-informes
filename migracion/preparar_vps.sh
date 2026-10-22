#!/usr/bin/env bash
# Prepara un VPS Ubuntu nuevo para La Dulce Alianza. Se ejecuta UNA vez, como root o con sudo,
# en el servidor nuevo:  sudo bash preparar_vps.sh
#  - usuario lda sin sudo, con linger (servicios de usuario aunque no haya sesión abierta)
#  - cortafuegos: solo SSH (22), HTTP (80) y HTTPS (443)
#  - Caddy como proxy HTTPS de la app (127.0.0.1:8765)
#  - paquetes que usan los scripts (python3-cryptography, sqlite3, git)
set -euo pipefail

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-cryptography sqlite3 git caddy ufw >/dev/null

# Usuario de la aplicación, sin sudo a propósito
if ! id lda >/dev/null 2>&1; then
    useradd -m -s /bin/bash lda
fi
loginctl enable-linger lda
timedatectl set-timezone Europe/Madrid

# Cortafuegos: lo que no esté aquí queda cerrado desde internet
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Caddy: HTTPS automático para la app
cat > /etc/caddy/Caddyfile <<'EOF'
# App La Dulce Alianza (usuario lda, app.py en 127.0.0.1:8765)
app.ladulcealianza.com {
	encode gzip
	header {
		Strict-Transport-Security "max-age=31536000"
		-Server
	}
	reverse_proxy 127.0.0.1:8765
}
EOF
systemctl enable --now caddy
systemctl reload caddy

echo "VPS preparado. Puertos abiertos:"
ufw status | sed -n '1,12p'
