# Imagem de produção (Railway usa este Dockerfile via railway.json)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Arquivos estáticos gerados no build (sem banco nem segredos).
RUN DJANGO_BUILD=1 python manage.py collectstatic --noinput

RUN useradd --create-home --uid 1000 app && chown -R app:app /app && chmod +x scripts/start.sh
USER app

EXPOSE 8000
CMD ["sh", "scripts/start.sh"]
