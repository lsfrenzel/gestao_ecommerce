"""Cabeçalhos de segurança adicionais."""

from __future__ import annotations

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    # form-action também vale para o redirecionamento após o envio: libera o checkout do Mercado Pago.
    "form-action 'self' https://www.mercadopago.com.br https://*.mercadopago.com.br https://*.mercadopago.com "
    "https://*.mercadolivre.com https://*.mercadolibre.com"
)


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.user.is_authenticated if hasattr(request, "user") else False:
            response.headers.setdefault("Cache-Control", "private, no-store")
        return response
