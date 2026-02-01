#!/bin/bash
set -e

APP_DIR="/opt/clawedin"
APP_USER="clawedin"
SERVICE_NAME="clawedin"
GIT_SSH_KEY="/home/clawedin/.ssh/clawedinserver"

echo "➡️  Switching to app directory..."
cd "$APP_DIR"

echo "⬇️  Pulling latest changes..."
if [ -f "$GIT_SSH_KEY" ]; then
  sudo -u "$APP_USER" env GIT_SSH_COMMAND="ssh -i $GIT_SSH_KEY -o IdentitiesOnly=yes" git pull
else
  sudo -u "$APP_USER" git pull
fi

echo "📦 Installing dependencies..."
sudo -u "$APP_USER" bash -c "
  source $APP_DIR/.venv/bin/activate
  pip install -r requirements.txt
"

echo "🗄️  Applying migrations..."
sudo -u "$APP_USER" bash -c "
  source $APP_DIR/.venv/bin/activate
  python manage.py migrate
"

echo "🎨 Collecting static files..."
sudo -u "$APP_USER" bash -c "
  source $APP_DIR/.venv/bin/activate
  python manage.py collectstatic --noinput
"

echo "🔁 Restarting service..."
sudo systemctl restart "$SERVICE_NAME"

echo "✅ Deployment completed successfully!"
