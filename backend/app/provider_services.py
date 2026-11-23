"""Provider lists served from the providers themselves, never from us.

- **Electricity billers** — VTU publishes the discos it sells for, with the
  service code and minimum amount, in the table on its developer page
  (``api/electricity.php``). That table is the source; there is no JSON
  endpoint for it (``index.php?serviceName=Electricity`` answers HTTP 500).
- **Cable TV providers** — the providers VTU's CableTV variation endpoint
  actually returns packages for. The candidate names come from VTU's own
  cable documentation; anything it answers ``code 102`` for is dropped, so a
  provider with no live packages never reaches the app.
- **Banks** — Flutterwave's live ``/banks/NG`` list, which is what transfers
  are resolved and paid against, so the codes always match.

Everything is cached briefly so a screen opening does not hit the providers
every time.
"""

from __future__ import annotations

import os
import re
import threading
import time

import httpx

from . import flutterwave, vtu

ELECTRICITY_DOC_URL = (
    os.environ.get("VTU_ELECTRICITY_DOC_URL")
    or "https://www.vtuafrica.com.ng/api/electricity.php"
)
CABLE_DOC_URL = (
    os.environ.get("VTU_CABLE_DOC_URL")
    or "https://www.vtuafrica.com.ng/api/cable-tv.php"
)
CACHE_SECONDS = int(os.environ.get("VTU_PROVIDER_CACHE_SECONDS") or 1800)
FETCH_TIMEOUT = 25.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Display names only: the ids and their availability come from the provider.
CABLE_LABELS = {
    "dstv": "DStv",
    "gotv": "GOtv",
    "startimes": "StarTimes",
    "showmax": "Showmax",
}

_TABLE = re.compile(r"<table.*?</table>", re.S | re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")

_lock = threading.Lock()
_cache: dict[str, dict] = {}


def _cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", _TAG.sub("", cell)).strip() for cell in _CELL.findall(row)
    ]


def _cached(key: str):
    entry = _cache.get(key)
    if entry and time.monotonic() < entry["expires"]:
        return entry["value"], entry["source"]
    return None, ""


def _remember(key: str, value, source: str):
    with _lock:
        _cache[key] = {
            "value": value,
            "source": source,
            "expires": time.monotonic() + CACHE_SECONDS,
        }
    return value, source


def _get(url: str) -> str | None:
    try:
        with httpx.Client(timeout=FETCH_TIMEOUT) as client:
            response = client.get(url, headers={"User-Agent": USER_AGENT})
        if not response.is_success:
            print(f"{url} returned HTTP {response.status_code}")
            return None
        return response.text
    except httpx.HTTPError as error:
        print(f"{url} could not be fetched: {error}")
        return None


def _amount(text: str) -> float | None:
    digits = re.sub(r"[^0-9.]", "", str(text or ""))
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return value if value > 0 else None


def parse_electricity(html: str) -> list[dict]:
    """Reads VTU's biller table: Company | Service Code | Minimum Amount."""
    providers = []
    seen = set()
    for table in _TABLE.findall(html):
        rows = _ROW.findall(table)
        if not rows:
            continue
        header = [cell.lower() for cell in _cells(rows[0])]
        if "service code" not in header:
            continue
        column = {name: index for index, name in enumerate(header)}
        for row in rows[1:]:
            cells = _cells(row)
            if len(cells) < len(header):
                continue
            code = cells[column["service code"]]
            if not code or code.lower() in seen:
                continue
            seen.add(code.lower())
            providers.append(
                {
                    "id": code,
                    "label": cells[column["company"]] if "company" in column else code,
                    "minAmount": _amount(cells[column["minimum amount"]])
                    if "minimum amount" in column
                    else None,
                }
            )
    return providers


def electricity_providers() -> tuple[list[dict], str]:
    cached, source = _cached("electricity")
    if cached is not None:
        return cached, source

    html = _get(ELECTRICITY_DOC_URL)
    if html:
        providers = parse_electricity(html)
        if providers:
            return _remember("electricity", providers, "provider")
    return [], "unavailable"


def cable_candidates() -> list[str]:
    """The service names VTU documents for cable TV."""
    html = _get(CABLE_DOC_URL) or ""
    found = []
    for name in re.findall(r"\b(dstv|gotv|startimes|showmax)\b", html, re.I):
        name = name.lower()
        if name not in found:
            found.append(name)
    return found or list(CABLE_LABELS)


def cable_providers() -> tuple[list[dict], str]:
    """Only the providers VTU returns live packages for."""
    cached, source = _cached("cable")
    if cached is not None:
        return cached, source

    providers = []
    for provider in cable_candidates():
        catalogue = vtu.fetch_cable_plans(provider)
        plans = catalogue.get("plans") or []
        if not plans:
            continue
        providers.append(
            {
                "id": provider,
                "label": CABLE_LABELS.get(provider, provider.upper()),
                "packages": len(plans),
            }
        )
    if providers:
        return _remember("cable", providers, "provider")
    return [], "unavailable"


def banks() -> tuple[list[dict], str]:
    cached, source = _cached("banks")
    if cached is not None:
        return cached, source

    if not flutterwave.is_configured():
        return [], "unavailable"
    try:
        rows = flutterwave.list_banks()
    except Exception as error:
        print(f"Flutterwave bank list failed: {error}")
        return [], "unavailable"
    if rows:
        return _remember("banks", rows, "flutterwave")
    return [], "unavailable"
