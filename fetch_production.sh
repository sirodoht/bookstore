#!/bin/bash
# Copy production data and reset admin password
# WARNING: This will OVERWRITE local db.sqlite3 and media/book_covers/

set -e  # Exit on any error

# Get the directory where this script is located
LOCAL_PATH="$(cd "$(dirname "$0")" && pwd)"

echo "Target: root@brick.01z.io:/var/www/bookstore"
echo "Local destination: $LOCAL_PATH"
echo ""

# Configuration
PROD_HOST="root@brick.01z.io"
PROD_PATH="/var/www/bookstore"

# Step 1: Copy production database
echo "Step 1: Copying production database..."
scp "$PROD_HOST:$PROD_PATH/db.sqlite3" "$LOCAL_PATH/db.sqlite3"
echo "  - db.sqlite3 copied"
echo ""

# Step 2: Copy production media files
echo "Step 2: Copying production media files..."
# Remove existing media to ensure clean copy
if [ -d "$LOCAL_PATH/media/book_covers" ]; then
    rm -rf "$LOCAL_PATH/media/book_covers"
fi
# Copy from production
scp -r "$PROD_HOST:$PROD_PATH/media/book_covers" "$LOCAL_PATH/media/"
echo "  - media/book_covers copied"
echo ""

# Step 3: Fix permissions
echo "Step 3: Fixing file permissions..."
chmod 644 "$LOCAL_PATH/db.sqlite3"
chmod -R 644 "$LOCAL_PATH/media/book_covers"/*
echo "  - Permissions updated"
echo ""

# Step 4: Reset theo user password
echo "Step 4: Resetting 'theo' user password..."
uv run manage.py shell << 'PYTHON_EOF'
from django.contrib.auth import get_user_model

User = get_user_model()

try:
    user = User.objects.get(username='theo')
    user.set_password('123')
    user.save()
    print("  - Password reset successfully for user 'theo'")
except User.DoesNotExist:
    print("  - WARNING: User 'theo' does not exist in database")
    print("  - Available users:")
    for u in User.objects.all():
        print(f"      - {u.username}")
PYTHON_EOF
echo ""

# Step 5: Verification
echo "Step 5: Verification..."
BOOK_COUNT=$(uv run manage.py shell -c "from books.models import Book; print(Book.objects.count())")
IMG_COUNT=$(ls -1 "$LOCAL_PATH/media/book_covers" | wc -l | tr -d ' ')

echo "  - Books in database: $BOOK_COUNT"
echo "  - Cover images: $IMG_COUNT"
echo ""

echo "=== Complete ==="
echo "You can now run: uv run manage.py runserver"
echo "Login with: theo / 123"
