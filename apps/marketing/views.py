from __future__ import annotations

import json

from django import forms
from django.db.models import Count, Q, Sum
from django.shortcuts import render

from apps.core.forms import DateInput, TenantModelForm
from apps.core.money import ZERO
from apps.core.periods import resolve_period
from apps.core.permissions import company_view
from apps.core.views import TenantCreateView, TenantDeleteView, TenantUpdateView, get_tenant_object
from apps.sales.models import SalesOrder

from .models import Campaign, average_ticket, cac, conversion_rate, cpc, roas


class CampaignForm(TenantModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "channel", "start_date", "end_date", "investment", "clicks", "visitors", "orders",
                  "new_customers", "attributed_revenue", "notes"]
        widgets = {"start_date": DateInput(), "end_date": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}


@company_view("marketing", feature="marketing")
def campaign_list(request):
    company = request.company
    period = resolve_period(request.GET, default="year")
    qs = (Campaign.objects.for_company(company)
          .filter(start_date__lte=period.end, end_date__gte=period.start)
          .annotate(linked_orders=Count("linked_sales", filter=Q(linked_sales__status__in=SalesOrder.REVENUE_STATUSES)),
                    linked_revenue=Sum("linked_sales__total", filter=Q(linked_sales__status__in=SalesOrder.REVENUE_STATUSES))))
    channel = request.GET.get("canal", "")
    if channel in dict(Campaign.CHANNELS):
        qs = qs.filter(channel=channel)
    campaigns = list(qs)
    totals = {
        "investment": sum((c.investment for c in campaigns), ZERO),
        "clicks": sum(c.clicks for c in campaigns), "visitors": sum(c.visitors for c in campaigns),
        "orders": sum(c.orders for c in campaigns), "new_customers": sum(c.new_customers for c in campaigns),
        "revenue": sum((c.attributed_revenue for c in campaigns), ZERO),
    }
    totals.update(conversion=conversion_rate(totals["orders"], totals["visitors"]),
                  cpc=cpc(totals["investment"], totals["clicks"]), cac=cac(totals["investment"], totals["new_customers"]),
                  roas=roas(totals["revenue"], totals["investment"]),
                  ticket=average_ticket(totals["revenue"], totals["orders"]))
    chart = json.dumps({
        "labels": [c.name[:24] for c in campaigns],
        "investment": [str(c.investment) for c in campaigns],
        "revenue": [str(c.attributed_revenue) for c in campaigns],
        "roas": [None if c.roas is None else str(round(c.roas, 2)) for c in campaigns],
    })
    return render(request, "marketing/list.html", {"campaigns": campaigns, "totals": totals, "chart": chart,
                                                   "period": period, "channels": Campaign.CHANNELS,
                                                   "channel": channel})


@company_view("marketing", feature="marketing")
def campaign_detail(request, pk):
    campaign = get_tenant_object(Campaign, request.company, pk=pk)
    orders = campaign.linked_sales.filter(status__in=SalesOrder.REVENUE_STATUSES)
    linked = orders.aggregate(n=Count("id"), revenue=Sum("total"))
    return render(request, "marketing/detail.html", {"campaign": campaign, "linked": linked,
                                                     "orders": orders.select_related("customer")[:30]})


class CampaignCreate(TenantCreateView):
    model = Campaign
    form_class = CampaignForm
    module = "marketing"
    feature = "marketing"
    success_url_name = "marketing:list"
    title = "Nova campanha"
    audit_action = "campaign"


class CampaignUpdate(TenantUpdateView):
    model = Campaign
    form_class = CampaignForm
    module = "marketing"
    feature = "marketing"
    success_url_name = "marketing:list"
    title = "Editar campanha"
    audit_action = "campaign"


class CampaignDelete(TenantDeleteView):
    model = Campaign
    module = "marketing"
    success_url_name = "marketing:list"
    audit_action = "campaign"
