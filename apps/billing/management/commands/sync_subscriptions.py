"""
Reconciliação periódica com o Mercado Pago (recomendado: 1x ao dia via Railway Cron).

- Atualiza a situação das assinaturas pendentes/ativas/pausadas consultando a API.
- Cupons de 50% com prazo vencido: valor da assinatura volta ao preço cheio.
"""

from django.core.management.base import BaseCommand

from apps.billing import gateway
from apps.billing.models import Subscription
from apps.billing.services import apply_coupon_expirations, sync_subscription


class Command(BaseCommand):
    help = "Sincroniza as assinaturas com o Mercado Pago e aplica o fim dos descontos de cupom."

    def handle(self, *args, **options):
        if not gateway.is_configured():
            self.stdout.write("sync_subscriptions: MERCADOPAGO_ACCESS_TOKEN não configurado; nada a fazer.")
            return
        ok = errors = 0
        for sub in Subscription.objects.filter(mp_preapproval_id__isnull=False,
                                               status__in=["pending", "authorized", "paused"]):
            try:
                sync_subscription(sub)
                ok += 1
            except gateway.GatewayError as exc:
                errors += 1
                self.stderr.write(f"assinatura {sub.pk}: {exc}")
        updated = apply_coupon_expirations()
        self.stdout.write(self.style.SUCCESS(
            f"sync_subscriptions: {ok} sincronizada(s), {errors} erro(s), {updated} valor(es) reajustado(s)."))
