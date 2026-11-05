"""Cria os planos SaaS iniciais se nenhum plano existir (idempotente). Nenhuma cobrança é gerada."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.companies.models import PLAN_FEATURES, Plan

ALL = [key for key, _ in PLAN_FEATURES]

PLANS = [
    {"name": "Essencial", "description": "Para quem está começando: produtos, precificação, estoque e vendas.",
     "monthly_price": Decimal("49.90"), "max_users": 2, "max_products": 200, "trial_days": 14,
     "features": ["pricing", "breakeven", "inventory", "csv_import"]},
    {"name": "Profissional", "description": "Gestão completa com compras, financeiro, marketing e relatórios em PDF.",
     "monthly_price": Decimal("129.90"), "max_users": 5, "max_products": 2000, "trial_days": 14, "is_default": True,
     "features": [f for f in ALL if f != "multi_warehouse"]},
    {"name": "Avançado", "description": "Operações maiores: múltiplos depósitos e limites ampliados.",
     "monthly_price": Decimal("299.90"), "max_users": None, "max_products": None, "trial_days": 14,
     "features": ALL},
]


class Command(BaseCommand):
    help = "Cria os planos iniciais (somente se ainda não houver planos)."

    def handle(self, *args, **options):
        if Plan.objects.exists():
            self.stdout.write("setup_plans: planos já existem; nada a fazer.")
            return
        for data in PLANS:
            Plan.objects.create(**data)
        self.stdout.write(self.style.SUCCESS(f"setup_plans: {len(PLANS)} planos criados."))
