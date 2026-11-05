from django.conf import settings

from .periods import PERIOD_CHOICES


def platform(request):
    return {"PLATFORM_NAME": settings.PLATFORM_NAME, "period_choices": PERIOD_CHOICES}
