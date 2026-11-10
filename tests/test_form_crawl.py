"""
Varredura de formulários: todo formulário POST visível é enviado como o navegador faria — primeiro com os
valores exibidos e depois preenchido com dados plausíveis. Nenhum envio pode resultar em erro 500.
"""

from __future__ import annotations

import os
from decimal import Decimal as D
from html.parser import HTMLParser
from unittest import mock
from urllib.parse import urljoin, urlparse

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import User
from apps.companies.models import Membership
from apps.finance.models import MonthlyGoal
from apps.marketing.models import Campaign
from apps.pricing.models import PricingSimulation
from apps.purchasing.models import PurchaseOrder, PurchaseOrderItem
from apps.research import trends
from apps.research.models import OpportunitySimulation
from apps.sales.models import SalesOrder
from apps.sales.services import save_order

from .factories import add_member, make_company, make_customer, make_product, make_supplier, make_user

DESTRUCTIVE = ("excluir", "delete", "remover", "cancelar", "anonim", "sair", "logout", "revogar", "bloquear",
               "desativar", "suspender", "toggle", "arquivar")


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.forms, self._form, self._select, self._textarea = [], [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "form":
            self._form = {"method": (a.get("method") or "get").lower(), "action": a.get("action") or "",
                          "fields": [], "buttons": []}
        elif self._form is None:
            return
        elif tag == "input":
            kind = (a.get("type") or "text").lower()
            if kind in ("submit", "button", "image"):
                if a.get("name"):
                    self._form["buttons"].append((a["name"], a.get("value", "")))
                return
            if kind == "file":
                return
            self._form["fields"].append({"name": a.get("name"), "type": kind, "value": a.get("value", ""),
                                         "checked": "checked" in a, "inputmode": a.get("inputmode", ""),
                                         "required": "required" in a})
        elif tag == "button" and a.get("name"):
            self._form["buttons"].append((a["name"], a.get("value", "")))
        elif tag == "select":
            self._select = {"name": a.get("name"), "type": "select", "options": [], "selected": None,
                            "multiple": "multiple" in a}
            self._form["fields"].append(self._select)
        elif tag == "option" and self._select is not None:
            self._select["options"].append(a.get("value", ""))
            if "selected" in a:
                self._select["selected"] = a.get("value", "")
        elif tag == "textarea":
            self._textarea = {"name": a.get("name"), "type": "textarea", "value": ""}
            self._form["fields"].append(self._textarea)

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea["value"] += data

    def handle_endtag(self, tag):
        if tag == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None


def _plausible(field) -> str:
    name, kind = (field.get("name") or "").lower(), field["type"]
    if kind == "email" or "email" in name:
        return f"teste{abs(hash(name)) % 10000}@exemplo.test"
    if kind == "date" or name.endswith("date") or "data" in name:
        return timezone.localdate().isoformat()
    if kind == "password" or "password" in name:
        return "Senha-Forte#2026x"
    if "cnpj" in name:
        return ""
    if "cpf" in name or "phone" in name or "telefone" in name:
        return ""
    if kind == "number" or field.get("inputmode") in ("decimal", "numeric") or any(
            k in name for k in ("qty", "quantity", "price", "cost", "pct", "amount", "value", "stock")):
        return "5"
    if "code" in name or "sku" in name:
        return f"T{abs(hash(name)) % 100000}"
    return "Teste automatizado"


def form_data(form, fill: bool) -> dict:
    data: dict = {}
    for field in form["fields"]:
        name = field.get("name")
        if not name:
            continue
        kind = field["type"]
        if kind == "select":
            options = [o for o in field["options"] if o != ""]
            value = field["selected"] if field["selected"] not in (None, "") else (
                options[0] if fill and options else (field["selected"] or ""))
            data.setdefault(name, []).append(value)
        elif kind in ("checkbox", "radio"):
            if field["checked"]:
                data.setdefault(name, []).append(field["value"] or "on")
            elif fill and kind == "radio" and name not in data:
                data[name] = [field["value"]]
        elif kind == "hidden":
            data.setdefault(name, []).append(field["value"])
        else:
            value = field["value"]
            if fill and not value:
                value = _plausible(field)
            data.setdefault(name, []).append(value)
    return {k: (v if len(v) > 1 else v[0]) for k, v in data.items()}


@override_settings(SERPAPI_API_KEY="")
class FormSubmissionCrawlTests(TestCase):
    def setUp(self):
        self.admin = make_user()
        self.company = make_company(self.admin)
        supplier = make_supplier(self.company)
        product = make_product(self.company, stock_qty=20, supplier=supplier, min_stock=D("2"))
        make_product(self.company, stock_qty=5, name="Outro produto")
        customer = make_customer(self.company)
        campaign = Campaign.objects.create(company=self.company, name="C", channel="email", investment=D("100"),
                                           attributed_revenue=D("500"), start_date=timezone.localdate(),
                                           end_date=timezone.localdate())
        save_order(SalesOrder(company=self.company, order_date=timezone.localdate(), status=SalesOrder.Status.PAID,
                              customer=customer, campaign=campaign),
                   [{"product": product, "quantity": 1, "unit_price": D("30")}])
        save_order(SalesOrder(company=self.company, order_date=timezone.localdate()),
                   [{"product": product, "quantity": 2, "unit_price": D("30")}])
        po = PurchaseOrder.objects.create(company=self.company, supplier=supplier, number=1,
                                          order_date=timezone.localdate())
        PurchaseOrderItem.objects.create(order=po, product=product, quantity=3, unit_cost=2)
        PricingSimulation.objects.create(company=self.company, name="Sim", product=product)
        OpportunitySimulation.objects.create(company=self.company, name="Oportunidade", origin="national",
                                             inputs={"name": "Oportunidade", "origin": "national",
                                                     "unit_cost_brl": "10", "quantity": 10,
                                                     "target_margin_pct": "30"})
        MonthlyGoal.objects.create(company=self.company, month=timezone.localdate().replace(day=1),
                                   revenue_target=D("100"))
        # Lançamentos em aberto: exercitam os formulários de pagar/receber/cancelar.
        from apps.finance.models import FinancialCategory, FinancialEntry

        for kind, nature in (("payable", "fixed"), ("receivable", "revenue")):
            category = FinancialCategory.objects.filter(company=self.company, nature=nature).first()
            FinancialEntry.objects.create(company=self.company, type=kind, category=category,
                                          description=f"Em aberto {kind}", amount=D("80"),
                                          competence_date=timezone.localdate(), due_date=timezone.localdate())
        from apps.companies.models import Coupon

        Coupon.objects.create(code="CRAWL50", discount_percent=50)

    def sweep(self, user, start="/painel/", limit=700, per_path=3):
        self.client.force_login(user)
        self.client.raise_request_exception = False
        seen, queue, forms, errors, per = set(), [start], [], [], {}
        while queue and len(seen) < limit:
            url = queue.pop(0)
            parsed = urlparse(url)
            if parsed.netloc and parsed.netloc != "testserver":
                continue
            url = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            if url in seen or url.startswith("#") or any(s in url for s in ("/media/", "javascript:", "mailto:")):
                continue
            if per.get(parsed.path, 0) >= per_path:  # variações de filtro/página da mesma tela
                continue
            per[parsed.path] = per.get(parsed.path, 0) + 1
            seen.add(url)
            response = self.client.get(url)
            if response.status_code >= 500:
                errors.append(("GET", url, response.status_code))
                continue
            if response.status_code in (301, 302):
                queue.append(response["Location"])
                continue
            if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
                continue
            parser = _FormParser()
            parser.feed(response.content.decode())
            queue.extend(urljoin(url, h) for h in parser.links)
            for form in parser.forms:
                action = urljoin(url, form["action"] or url)
                if form["method"] == "get":
                    queue.append(action + "?" + "&".join(
                        f"{k}={v}" for k, v in form_data(form, fill=False).items() if isinstance(v, str) and v))
                else:
                    forms.append((url, action, form))
        # Envia primeiro os formulários não destrutivos; os de exclusão/cancelamento por último.
        forms.sort(key=lambda f: any(w in f[1] for w in DESTRUCTIVE))
        submitted = set()
        for page, action, form in forms:
            buttons = form["buttons"] or [None]
            for button in buttons:
                for fill in (False, True):
                    data = form_data(form, fill)
                    if button:
                        data[button[0]] = button[1]
                    key = (action, tuple(sorted((k, str(v)) for k, v in data.items() if k != "csrfmiddlewaretoken")))
                    if key in submitted or "/sair" in action or "logout" in action:
                        continue
                    submitted.add(key)
                    response = self.client.post(urlparse(action).path + (
                        "?" + urlparse(action).query if urlparse(action).query else ""), data)
                    if response.status_code >= 500:
                        errors.append(("POST", action, response.status_code, page,
                                       {k: v for k, v in data.items() if k != "csrfmiddlewaretoken"}))
        if os.environ.get("CRAWL_DEBUG"):
            print(f"\n{user}: {len(seen)} páginas, {len(submitted)} envios")
            for a in sorted({a for a, _ in submitted}):
                print("   ", a.replace("http://testserver", ""))
        return seen, submitted, errors

    def test_admin_all_forms(self):
        with mock.patch.object(trends, "trending_now", return_value=[]):
            seen, submitted, errors = self.sweep(self.admin)
        self.assertGreater(len(submitted), 40)
        self.assertEqual(errors, [])

    def test_operator_all_forms(self):
        user = add_member(self.company, role=Membership.Role.OPERATOR)
        seen, submitted, errors = self.sweep(user)
        self.assertEqual(errors, [])

    def test_superadmin_all_forms(self):
        root = User.objects.create_superuser(email="root@exemplo.test", password="x-Senha#2026", full_name="Root")
        seen, submitted, errors = self.sweep(root, start="/plataforma/")
        self.assertEqual(errors, [])

    def test_new_user_onboarding_forms(self):
        user = make_user()
        seen, submitted, errors = self.sweep(user, start="/empresa/cadastro/")
        self.assertEqual(errors, [])
