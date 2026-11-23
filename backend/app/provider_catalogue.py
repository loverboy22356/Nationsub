"""Live VTU Africa data-plan catalogue.

VTU exposes no JSON plan-listing endpoint: every
`index.php?serviceName=...&service=...` lookup answers code 102 ("No active
variations found") for data, airtime and exam PINs, while cable TV works. What
it does publish is its own price table on the API documentation page, served as
plain HTML:

    https://www.vtuafrica.com.ng/api/data.php

That page carries one table per service (MTNSME, MTNGIFT, MTNAWOOF, ...) with
the columns Value, Service, DataPlan, Description, Validity, Portal Owners,
Resellers, Agents, Free Users, Status. This module reads it, keeps the active
plans at the owner tier (the tier our API key is on) and caches the result.

`catalogue.py` (the bundled snapshot) is only a fallback for the case where the
page cannot be reached, so a provider outage never empties the app's catalogue.
"""

from __future__ import annotations

import os
import re
import threading
import time

import httpx

from . import pricing

TABLE_URL = os.environ.get("VTU_PLAN_TABLE_URL") or "https://www.vtuafrica.com.ng/api/data.php"
CACHE_SECONDS = int(os.environ.get("VTU_PLAN_CACHE_SECONDS") or 600)
FETCH_TIMEOUT = 25.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

NETWORK_ORDER = ["mtn", "airtel", "glo", "9mobile"]
CATEGORY_RANK = {"SME": 0, "Gifting": 1, "Awoof": 2, "Corporate": 3, "Other": 4}

_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")

_lock = threading.Lock()
_cache: dict = {"plans": None, "source": "", "expires": 0.0}


def _cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", _TAG.sub("", cell)).strip() for cell in _CELL.findall(row)
    ]


def _price(text: str) -> float | None:
    """'₦330' -> 330.0; disabled plans are listed as '₦-100'."""
    raw = str(text or "")
    if "-" in raw:
        return None
    digits = re.sub(r"[^0-9.]", "", raw)
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def _network(service: str) -> str | None:
    name = str(service or "").upper()
    if name.startswith("9MOBILE"):
        return "9mobile"
    if name.startswith("MTN"):
        return "mtn"
    if name.startswith("AIRTEL"):
        return "airtel"
    if name.startswith("GLO"):
        return "glo"
    return None


def _category(service: str) -> str:
    name = str(service or "").upper()
    if name.endswith("SME"):
        return "SME"
    if name.endswith("GIFT"):
        return "Gifting"
    if name.endswith("AWOOF"):
        return "Awoof"
    if name.endswith("CG"):
        return "Corporate"
    return "Other"


def parse(html: str) -> dict[str, list[dict]]:
    """Turns VTU's price tables into {network: [plan, ...]}."""
    plans: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()

    for table in _TABLE.findall(html):
        rows = _ROW.findall(table)
        if not rows:
            continue
        header = [cell.lower() for cell in _cells(rows[0])]
        if "dataplan" not in header or "service" not in header:
            continue
        column = {name: index for index, name in enumerate(header)}
        for row in rows[1:]:
            cells = _cells(row)
            if len(cells) < len(header):
                continue
            service = cells[column["service"]]
            code = cells[column["dataplan"]]
            network = _network(service)
            if not code or not network:
                continue
            label = cells[column["value"]] if "value" in column else ""
            if not label:
                continue
            status = cells[column["status"]].lower() if "status" in column else "active"
            if status and status != "active":
                continue
            price = _price(cells[column["portal owners"]]) if "portal owners" in column else None
            if price is None:
                continue

            key = (service.lower(), code.lower())
            if key in seen:
                continue
            seen.add(key)
            plans.setdefault(network, []).append(
                {
                    "code": code,
                    "label": label,
                    "price": pricing.money(price),
                    "validity": cells[column["validity"]] if "validity" in column else "",
                    "category": _category(service),
                    "network": network,
                    "service": service,
                }
            )

    for network_plans in plans.values():
        network_plans.sort(key=lambda plan: CATEGORY_RANK.get(plan["category"], 9))
    return plans


def _fetch_live() -> dict[str, list[dict]] | None:
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT) as client:
            response = client.get(TABLE_URL, headers={"User-Agent": USER_AGENT})
        if not response.is_success:
            print(f"VTU plan table returned HTTP {response.status_code}")
            return None
        parsed = parse(response.text)
    except httpx.HTTPError as error:
        print(f"VTU plan table could not be fetched: {error}")
        return None
    if not parsed:
        print("VTU plan table had no active plans")
        return None
    return parsed


def _snapshot() -> dict[str, list[dict]]:
    from .catalogue import CATALOGUE

    return {
        network: [dict(plan) for plan in entry["plans"]]
        for network, entry in CATALOGUE.items()
    }


def plans() -> tuple[dict[str, list[dict]], str]:
    """Returns ({network: [plan]}, source) where source is provider or snapshot."""
    cached = _cache["plans"]
    if cached is not None and time.monotonic() < _cache["expires"]:
        return cached, _cache["source"]

    live = _fetch_live()
    if live:
        with _lock:
            _cache.update(
                plans=live, source="provider", expires=time.monotonic() + CACHE_SECONDS
            )
        return live, "provider"

    if cached is not None:
        # Keep serving the last good catalogue rather than an empty one.
        return cached, _cache["source"]

    snapshot = _snapshot()
    with _lock:
        _cache.update(
            plans=snapshot, source="snapshot", expires=time.monotonic() + CACHE_SECONDS
        )
    return snapshot, "snapshot"


def plans_for(network: str) -> list[dict] | None:
    catalogue, _ = plans()
    return catalogue.get(str(network or "").lower())


def plan(service: str, code: str) -> dict | None:
    """Finds one plan by the gateway's service name and plan code."""
    wanted_service = str(service or "").strip().lower()
    wanted_code = str(code or "").strip().lower()
    if not wanted_code:
        return None

    catalogue, _ = plans()
    for network_plans in catalogue.values():
        for entry in network_plans:
            if (
                str(entry.get("service", "")).lower() == wanted_service
                and str(entry.get("code", "")).lower() == wanted_code
            ):
                return entry

    # Last resort: the bundled snapshot, in case the live table dropped a plan
    # the provider still accepts.
    for network_plans in _snapshot().values():
        for entry in network_plans:
            if (
                str(entry.get("service", "")).lower() == wanted_service
                and str(entry.get("code", "")).lower() == wanted_code
            ):
                return entry
    return None


def refresh() -> tuple[dict[str, list[dict]], str]:
    _cache["expires"] = 0.0
    return plans()
