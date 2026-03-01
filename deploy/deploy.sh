#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <hostname>"
    echo "Example: $0 root@yourdomain.com"
    exit 1
fi

HOST="$1"

echo "=== Running uv sync ==="
ssh $HOST 'su - deploy -c "/home/deploy/.local/bin/uv run sync"'

echo "=== Running collectstatic ==="
ssh $HOST 'su - deploy -c "/home/deploy/.local/bin/uv run /var/www/bookstore/manage.py collectstatic --no-input"'

echo "=== Running migrations ==="
ssh $HOST 'su - deploy -c "/home/deploy/.local/bin/uv run /var/www/bookstore/manage.py migrate --no-input"'

echo "=== Restarting bookstore service ==="
ssh $HOST 'systemctl restart bookstore'

echo "=== Restarting caddy ==="
ssh $HOST 'systemctl restart caddy'

echo ""
echo "=== Deployment complete! ==="
