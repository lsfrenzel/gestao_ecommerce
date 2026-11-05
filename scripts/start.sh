#!/bin/sh
# Inicialização do container: migrações → planos iniciais → SUPER ADMIN (opcional) → Gunicorn.
set -e

echo "==> Aplicando migrações"
python manage.py migrate --noinput

echo "==> Planos iniciais (somente se não houver planos)"
python manage.py setup_plans

echo "==> SUPER ADMIN inicial (somente se as variáveis estiverem definidas)"
python manage.py ensure_superadmin

echo "==> Iniciando Gunicorn"
exec gunicorn config.wsgi:application --config gunicorn.conf.py
