"""
Configurações do FrenzelTech Ecommerce.

Toda configuração sensível ou dependente de ambiente vem de variáveis de ambiente.
Veja `.env.example` e `README.md`.

Modos:
- Produção (padrão): DJANGO_DEBUG=0 → exige DJANGO_SECRET_KEY e PostgreSQL (DATABASE_URL).
- Desenvolvimento: DJANGO_DEBUG=1 → aceita SQLite *somente* se DATABASE_URL não estiver definida.
- Testes: DJANGO_TESTING=1 (definido automaticamente por `manage.py test`).
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Carrega um `.env` simples (KEY=VALUE) sem sobrescrever variáveis já definidas."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


DEBUG = env_bool("DJANGO_DEBUG", False)
TESTING = env_bool("DJANGO_TESTING", False)
BUILDING = env_bool("DJANGO_BUILD", False)  # etapa de build (collectstatic) sem banco/segredos
PRODUCTION = not (DEBUG or TESTING or BUILDING)

if PRODUCTION:
    _missing = []
    if not env("DJANGO_SECRET_KEY"):
        _missing.append('DJANGO_SECRET_KEY (gere com: python -c "import secrets; print(secrets.token_urlsafe(64))")')
    if not (env("DATABASE_URL") or env("PGHOST")):
        _missing.append("DATABASE_URL (no Railway: ${{Postgres.DATABASE_URL}})")
    if _missing:
        raise ImproperlyConfigured(
            "Variáveis de ambiente obrigatórias ausentes em produção (DJANGO_DEBUG=0): "
            + "; ".join(_missing) + ". Veja .env.example."
        )

SECRET_KEY = env("DJANGO_SECRET_KEY") or "dev-only-insecure-key-never-use-in-production"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if env("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(env("RAILWAY_PUBLIC_DOMAIN"))
    CSRF_TRUSTED_ORIGINS.append(f"https://{env('RAILWAY_PUBLIC_DOMAIN')}")
ALLOWED_HOSTS.append("healthcheck.railway.app")  # health check interno do Railway

_default_site = f"https://{env('RAILWAY_PUBLIC_DOMAIN')}" if env("RAILWAY_PUBLIC_DOMAIN") else "http://localhost:8000"
SITE_URL = (env("SITE_URL") or _default_site).rstrip("/")
PLATFORM_NAME = env("PLATFORM_NAME", "FrenzelTech Ecommerce")

# ---------------------------------------------------------------------------
# Assinaturas — Mercado Pago (Mercado Livre). Credenciais em Suas integrações → Credenciais.
# ---------------------------------------------------------------------------
def _credential(*names: str) -> str:
    """Primeiro valor preenchido entre os nomes aceitos, sem aspas, espaços ou prefixo “Bearer ” colados por engano."""
    for name in names:
        value = env(name).strip().strip('"').strip("'").strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        if value:
            return value
    return ""


MERCADOPAGO_ACCESS_TOKEN = _credential("MERCADOPAGO_ACCESS_TOKEN", "MERCADO_PAGO_ACCESS_TOKEN", "MP_ACCESS_TOKEN")
MERCADOPAGO_PUBLIC_KEY = _credential("MERCADOPAGO_PUBLIC_KEY", "MERCADO_PAGO_PUBLIC_KEY", "MP_PUBLIC_KEY")
# Assinatura secreta do webhook (Suas integrações → Webhooks). Recomendado em produção.
MERCADOPAGO_WEBHOOK_SECRET = _credential("MERCADOPAGO_WEBHOOK_SECRET", "MERCADO_PAGO_WEBHOOK_SECRET",
                                         "MP_WEBHOOK_SECRET")
FREE_TRIAL_DAYS = env_int("FREE_TRIAL_DAYS", 14)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "storages",
    # Módulos do sistema
    "apps.core",
    "apps.accounts",
    "apps.companies",
    "apps.platform",
    "apps.billing",
    "apps.products",
    "apps.pricing",
    "apps.inventory",
    "apps.purchasing",
    "apps.customers",
    "apps.sales",
    "apps.finance",
    "apps.marketing",
    "apps.reports",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.companies.middleware.CurrentCompanyMiddleware",
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.platform",
                "apps.companies.context_processors.company",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Banco de dados — PostgreSQL via DATABASE_URL (ou variáveis PG* do Railway).
# ---------------------------------------------------------------------------
DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL and env("PGHOST"):
    DATABASE_URL = (
        f"postgres://{env('PGUSER')}:{env('PGPASSWORD')}@{env('PGHOST')}:"
        f"{env('PGPORT', '5432')}/{env('PGDATABASE')}"
    )

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL, conn_max_age=env_int("DB_CONN_MAX_AGE", 60), conn_health_checks=True
        )
    }
elif DEBUG or TESTING or BUILDING:
    # Somente desenvolvimento/testes locais sem PostgreSQL. Produção exige PostgreSQL (verificado acima).
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
else:  # pragma: no cover
    raise ImproperlyConfigured("DATABASE_URL (PostgreSQL) é obrigatória em produção.")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 3  # 3 dias (convites usam o mesmo fluxo)

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = False

# ---------------------------------------------------------------------------
# Estáticos e mídia. Imagens enviadas vão para um armazenamento persistente:
# Cloudinary (prioridade, se configurado) ou bucket S3-compatible. O disco do container é efêmero.
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT")) if env("MEDIA_ROOT") else BASE_DIR / "media"
# Sem S3 em produção, só sirva mídia do disco se houver um Railway Volume montado em MEDIA_ROOT.
SERVE_MEDIA_FROM_DISK = env_bool("SERVE_MEDIA_FROM_DISK", False)

# Cloudinary: cole a "API environment variable" do painel em CLOUDINARY_URL
# (cloudinary://API_KEY:API_SECRET@CLOUD_NAME) ou use as três variáveis separadas.
CLOUDINARY_URL = env("CLOUDINARY_URL").strip()
CLOUDINARY_CLOUD_NAME = env("CLOUDINARY_CLOUD_NAME").strip()
CLOUDINARY_API_KEY = env("CLOUDINARY_API_KEY").strip()
CLOUDINARY_API_SECRET = env("CLOUDINARY_API_SECRET").strip()
CLOUDINARY_FOLDER = env("CLOUDINARY_FOLDER", "frenzeltech-ecommerce").strip()
if CLOUDINARY_URL and not CLOUDINARY_URL.startswith("cloudinary://"):
    raise ImproperlyConfigured("CLOUDINARY_URL deve começar com cloudinary:// (copie a “API environment variable” "
                               "do painel do Cloudinary, sem o prefixo CLOUDINARY_URL=).")
_partial_cloudinary = [CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET]
if not CLOUDINARY_URL and any(_partial_cloudinary) and not all(_partial_cloudinary):
    raise ImproperlyConfigured("Cloudinary incompleto: informe CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY e "
                               "CLOUDINARY_API_SECRET (ou apenas CLOUDINARY_URL).")
USE_CLOUDINARY = bool(CLOUDINARY_URL or all(_partial_cloudinary))

S3_BUCKET_NAME = env("S3_BUCKET_NAME")
USE_S3 = bool(S3_BUCKET_NAME) and not USE_CLOUDINARY
_static_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if (DEBUG or TESTING) else "whitenoise.storage.CompressedManifestStaticFilesStorage"
)
if USE_CLOUDINARY:
    STORAGES = {
        "default": {"BACKEND": "apps.core.cloudinary_storage.CloudinaryMediaStorage"},
        "staticfiles": {"BACKEND": _static_backend},
    }
elif USE_S3:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": S3_BUCKET_NAME,
                "endpoint_url": env("S3_ENDPOINT_URL") or None,
                "access_key": env("S3_ACCESS_KEY_ID"),
                "secret_key": env("S3_SECRET_ACCESS_KEY"),
                "region_name": env("S3_REGION") or None,
                "signature_version": "s3v4",
                "addressing_style": env("S3_ADDRESSING_STYLE", "virtual"),
                "file_overwrite": False,
                "location": "media",
                "default_acl": "private",
                "querystring_auth": True,  # URLs assinadas: imagens das empresas não ficam públicas
                "querystring_expire": env_int("S3_URL_EXPIRE", 60 * 60),
                "custom_domain": None,
            },
        },
        "staticfiles": {"BACKEND": _static_backend},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": _static_backend},
    }

if PRODUCTION and not (USE_CLOUDINARY or USE_S3 or SERVE_MEDIA_FROM_DISK):
    import sys

    print("AVISO: nenhum armazenamento persistente de imagens configurado (CLOUDINARY_URL ou S3_*). "
          "Imagens enviadas serão perdidas a cada deploy.", file=sys.stderr)

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
PRODUCT_IMAGE_MAX_MB = env_int("PRODUCT_IMAGE_MAX_MB", 5)
CSV_IMPORT_MAX_MB = env_int("CSV_IMPORT_MAX_MB", 5)
CSV_IMPORT_MAX_ROWS = env_int("CSV_IMPORT_MAX_ROWS", 5000)

# ---------------------------------------------------------------------------
# Cache (rate limiting). Redis opcional — obrigatório se houver mais de uma réplica.
# ---------------------------------------------------------------------------
if env("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": env("REDIS_URL")}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "saas"}}

# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 12)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

if not DEBUG and not TESTING:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)

LOGIN_RATE_LIMIT_ATTEMPTS = env_int("LOGIN_RATE_LIMIT_ATTEMPTS", 5)
LOGIN_RATE_LIMIT_WINDOW = env_int("LOGIN_RATE_LIMIT_WINDOW", 15 * 60)
PASSWORD_RESET_RATE_LIMIT = env_int("PASSWORD_RESET_RATE_LIMIT", 5)
PASSWORD_RESET_RATE_WINDOW = env_int("PASSWORD_RESET_RATE_WINDOW", 60 * 60)

# ---------------------------------------------------------------------------
# E-mail
# ---------------------------------------------------------------------------
if env("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("EMAIL_HOST")
    EMAIL_PORT = env_int("EMAIL_PORT", 587)
    EMAIL_HOST_USER = env("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
    EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
    EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
    EMAIL_TIMEOUT = 15
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "nao-responda@localhost")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# ---------------------------------------------------------------------------
# Logs (JSON em produção, sem dados sensíveis)
# ---------------------------------------------------------------------------
LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "apps.core.logging.RedactSensitiveFilter"}},
    "formatters": {
        "json": {"()": "apps.core.logging.JsonFormatter"},
        "plain": {"format": "%(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain" if DEBUG else "json", "filters": ["redact"]}
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.db.backends": {"level": "WARNING"},
    },
}

if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    LOGGING["root"]["level"] = "CRITICAL"
    LOGGING["loggers"]["django"]["level"] = "CRITICAL"
