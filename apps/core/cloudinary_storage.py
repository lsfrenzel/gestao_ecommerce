"""
Armazenamento de mídia (imagens de produtos) no Cloudinary — persistente entre deploys.

Ativado automaticamente quando `CLOUDINARY_URL` (ou CLOUDINARY_CLOUD_NAME + CLOUDINARY_API_KEY +
CLOUDINARY_API_SECRET) está definida. Ver `config/settings.py` e README.

- O nome salvo no banco é o caminho relativo (ex.: `produtos/2026/11/camiseta-a1b2c3d4.png`); no Cloudinary o
  arquivo fica em `<CLOUDINARY_FOLDER>/<caminho sem extensão>`.
- As URLs são HTTPS e usam otimização automática (formato e qualidade: f_auto, q_auto).
- Nomes recebem um sufixo aleatório, então não é preciso consultar a API (limitada por hora) para checar
  se o arquivo já existe.
"""

from __future__ import annotations

import os
import secrets

from django.conf import settings
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


def configure() -> None:
    import cloudinary

    if settings.CLOUDINARY_URL:
        os.environ["CLOUDINARY_URL"] = settings.CLOUDINARY_URL
        cloudinary.reset_config()
    else:
        cloudinary.config(cloud_name=settings.CLOUDINARY_CLOUD_NAME, api_key=settings.CLOUDINARY_API_KEY,
                          api_secret=settings.CLOUDINARY_API_SECRET)
    cloudinary.config(secure=True)


@deconstructible
class CloudinaryMediaStorage(Storage):
    def __init__(self, folder: str | None = None):
        self.folder = (folder if folder is not None else settings.CLOUDINARY_FOLDER).strip("/")
        configure()

    # --- nomes ------------------------------------------------------------------------------
    def public_id(self, name: str) -> str:
        base, _ext = os.path.splitext(name.replace("\\", "/").lstrip("/"))
        return f"{self.folder}/{base}" if self.folder else base

    def get_available_name(self, name, max_length=None):
        name = name.replace("\\", "/")
        directory, filename = os.path.split(name)
        base, ext = os.path.splitext(filename)
        suffix = secrets.token_hex(4)
        limit = (max_length or 255) - len(directory) - len(ext) - len(suffix) - 3
        base = base[:max(8, limit)]
        return f"{directory}/{base}-{suffix}{ext}" if directory else f"{base}-{suffix}{ext}"

    def exists(self, name):
        return False  # nomes únicos por sufixo aleatório

    # --- operações --------------------------------------------------------------------------
    def _save(self, name, content):
        import cloudinary.uploader

        if hasattr(content, "seek"):
            content.seek(0)
        cloudinary.uploader.upload(content, public_id=self.public_id(name), resource_type="image",
                                   overwrite=False, unique_filename=False, use_filename=False)
        return name

    def _open(self, name, mode="rb"):  # pragma: no cover - leitura direta não é usada pelo sistema
        import urllib.request

        from django.core.files.base import ContentFile

        with urllib.request.urlopen(self.url(name), timeout=15) as response:  # noqa: S310 — URL do próprio Cloudinary
            return ContentFile(response.read(), name=name)

    def delete(self, name):
        import cloudinary.uploader

        if name:
            cloudinary.uploader.destroy(self.public_id(name), resource_type="image", invalidate=True)

    def url(self, name):
        import cloudinary.utils

        if not name:
            return ""
        url, _options = cloudinary.utils.cloudinary_url(self.public_id(name), resource_type="image", secure=True,
                                                        fetch_format="auto", quality="auto")
        return url

    def size(self, name):  # pragma: no cover
        return 0
