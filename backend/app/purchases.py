"""What a customer is charged for one purchase.

A purchase spends the customer's own wallet (money collected through
Flutterwave top-ups) and never the owner's VTU float, so this service works the
price out itself instead of trusting the app: the provider amount comes from
the catalogue or the provider's own package list, and the service charge comes
from pricing.py.

    customer_charge = provider_amount + fee
"""

from __future__ import annotations

import math

from . import pricing
from .catalogue import CATALOGUE, plans_for

# Gateway service -> the fee key used by pricing.py
FEE_KEY = {
    "airtime": "airtime",
    "data": "data",
    "paytv": "cable",
    "electric": "electricity",
    "betpay": "betpay",
    "sendmoney": "sendmoney",
    "airtime-pin": "airtime-pin",
}

# service|plan-code -> catalogue plan, built once per process.
_PLAN_INDEX: dict[str, dict] = {}
for _network in CATALOGUE:
    for _plan in plans_for(_network) or []:
        _PLAN_INDEX[f"{_plan.get('service')}|{_plan.get('code')}"] = _plan


def catalogue_plan(service, code) -> dict | None:
    return _PLAN_INDEX.get(f"{str(service or '').strip()}|{str(code or '').strip()}")


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or number <= 0:
        return None
    return number


def describe(service, params=None, cable_plans=None) -> dict:
    """Returns {"skip": True}, {"error": ...} or the priced purchase.

    Services such as airtime-to-cash and balance checks do not spend the
    wallet, so they are skipped rather than priced.
    """
    params = params or {}
    fee_key = FEE_KEY.get(str(service or "").lower())
    if not fee_key:
        return {"skip": True}

    provider_amount = None
    label = ""
    size_mb = None

    if fee_key == "data":
        plan = catalogue_plan(params.get("service"), params.get("DataPlan"))
        if not plan:
            return {"error": "That data plan is not available right now."}
        provider_amount = _positive(plan.get("price"))
        label = f"{plan.get('label')} data"
        size_mb = pricing.size_in_mb(plan.get("label"))
    elif fee_key == "cable":
        variation = str(params.get("variation") or "").strip()
        matching = [
            entry for entry in (cable_plans or []) if str(entry.get("code")) == variation
        ]
        if not matching:
            return {"error": "That cable package is not available right now."}
        plan = matching[0]
        provider_amount = _positive(
            plan.get("providerPrice") if plan.get("providerPrice") is not None else plan.get("price")
        )
        label = f"{plan.get('label') or 'Cable'} subscription"
    elif fee_key == "airtime-pin":
        # VTU documents ₦100 and ₦200 denominations as variations 1 and 2.
        provider_amount = 200 if str(params.get("variation")) == "2" else 100
        label = f"₦{int(provider_amount)} recharge PIN"
    elif fee_key == "sendmoney":
        provider_amount = _positive(params.get("amount"))
        label = "Bank transfer"
    elif fee_key == "electricity":
        provider_amount = _positive(params.get("amount"))
        label = "Electricity token"
    elif fee_key == "betpay":
        provider_amount = _positive(params.get("amount"))
        label = "Bet account funding"
    else:
        provider_amount = _positive(params.get("amount"))
        label = "Airtime"

    if not provider_amount:
        return {"error": "The amount for this purchase is missing or invalid."}

    fee = pricing.fee_for(fee_key, provider_amount, size_mb)
    return {
        "ok": True,
        "providerAmount": pricing.money(provider_amount),
        "fee": fee,
        "customerCharge": pricing.money(provider_amount + fee),
        "label": label,
    }
