"""Obriga a troca da senha provisória antes de usar o sistema."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse

ALLOWED_PREFIXES = ("/static/", "/media/", "/healthz")


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
            if request.path not in allowed and not request.path.startswith(ALLOWED_PREFIXES):
                if request.method == "GET":
                    messages.info(request, "Por segurança, defina uma nova senha para continuar. "
                                           "A senha atual é provisória.")
                return redirect("accounts:password_change")
        return self.get_response(request)
