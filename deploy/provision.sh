#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <hostname>"
    echo "Example: $0 root@yourdomain.com"
    exit 1
fi

HOST="$1"

echo "=== Installing Caddy ==="
ssh $HOST 'apt install -y debian-keyring debian-archive-keyring apt-transport-https curl'
ssh $HOST "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
ssh $HOST "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list"
ssh $HOST 'chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg'
ssh $HOST 'chmod o+r /etc/apt/sources.list.d/caddy-stable.list'
ssh $HOST 'apt update && apt install -y caddy'

echo "=== Creating www directory ==="
ssh $HOST 'mkdir -p /var/www && chmod 0755 /var/www'

echo "=== Creating deploy user ==="
ssh $HOST 'useradd -m -s /bin/bash -N -G sudo,www-data deploy && usermod -L deploy'

echo "=== Generating SSH key for deploy user ==="
ssh $HOST 'mkdir -p /home/deploy/.ssh && su - deploy -c "ssh-keygen -t ed25519 -f /home/deploy/.ssh/id_ed25519 -N \"\"" && chown -R deploy:deploy /home/deploy/.ssh'

echo "=== Setting www ownership ==="
ssh $HOST 'chown -R deploy:www-data /var/www'

echo "=== Installing uv ==="
ssh $HOST 'curl -LsSf https://astral.sh/uv/0.10.7/install.sh | sh'
ssh $HOST 'su - deploy -c "curl -LsSf https://astral.sh/uv/0.10.7/install.sh | sh"'

echo "=== Installing systemd service ==="
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scp "$SCRIPT_DIR/bookstore.service" $HOST:/etc/systemd/system/bookstore.service
scp "$SCRIPT_DIR/Caddyfile" $HOST:/etc/caddy/Caddyfile
ssh $HOST 'mkdir -p /run && chown deploy:www-data /run'
ssh $HOST 'systemctl daemon-reload'
ssh $HOST 'systemctl enable bookstore.service'

echo ""
echo "=== Provisioning complete! ==="
echo "Service 'bookstore' installed and enabled (not started yet)."
echo "Deploy your code to /var/www/bookstore and run:"
echo "  systemctl start bookstore"
