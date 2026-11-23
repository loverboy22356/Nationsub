"""Customer prices.

The app never adds a service charge itself: this service adds it when it serves
a catalogue or charges a purchase, so the price the customer sees is the
provider price plus this app's fee.

    price = provider_price + fee_for(service)

Change a number here, redeploy on Render, and the app follows.
"""

from __future__ import annotations

import math
import re

FEES = {
    "cable": 100,
    "airtime": 30,
    "electricity": 100,
    "exam-pin": 200,
    "betpay": 20,
    "sendmoney": 100,
    "airtime-pin": 100,
    "airtime-cash": 0,
    # CAC is not connected to a live provider, so it carries no charge yet.
    "cac": 0,
}

# Data is charged by bundle size, not price: bundles from 20MB to 200MB carry
# ₦50, everything else carries ₦100.
DATA_SMALL_BUNDLE = {"from_mb": 20, "to_mb": 200, "fee": 50}
DATA_FEE = 100

# Kept out of the wallet balance: a ₦1,000 top-up credits ₦950.
FUNDING_FEE = 50

_SIZE = re.compile(r"([\d.]+)\s*(KB|MB|GB|TB)", re.IGNORECASE)


def money(value) -> float:
    """Rounds to kobo the way the Node backend did (Math.round, half up)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return math.floor(number * 100 + 0.5) / 100


def size_in_mb(label) -> float | None:
    """Reads a plan label such as "200MB", "1GB" or "10.8GB"."""
    match = _SIZE.search(str(label or ""))
    if not match:
        return None
    size = float(match.group(1))
    if size <= 0:
        return None
    unit = match.group(2).upper()
    if unit == "KB":
        return size / 1024
    if unit == "GB":
        return size * 1024
    if unit == "TB":
        return size * 1024 * 1024
    return size


def data_fee(size_mb) -> int:
    try:
        mb = float(size_mb)
    except (TypeError, ValueError):
        return DATA_FEE
    if mb <= 0:
        return DATA_FEE
    if DATA_SMALL_BUNDLE["from_mb"] <= mb <= DATA_SMALL_BUNDLE["to_mb"]:
        return DATA_SMALL_BUNDLE["fee"]
    return DATA_FEE


def fee_for(service, provider_price=0, size_mb=None) -> float:
    service_id = str(service or "").lower()
    if service_id == "data":
        return data_fee(size_mb)
    fee = FEES.get(service_id)
    return fee if isinstance(fee, (int, float)) and fee > 0 else 0


def with_fee(service, provider_price, size_mb=None) -> dict | None:
    """Returns provider price, fee and customer price, or None when unusable."""
    try:
        base = float(provider_price)
    except (TypeError, ValueError):
        return None
    if math.isnan(base) or math.isinf(base) or base <= 0:
        return None
    fee = fee_for(service, base, size_mb)
    return {
        "providerPrice": money(base),
        "fee": fee,
        "price": money(base + fee),
    }
