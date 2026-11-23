"""VTU Africa gateway helpers: proxy, cable catalogue and float check."""

from __future__ import annotations

import math
import os
import urllib.parse

import httpx

from . import pricing

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

PROXY_TIMEOUT = 45.0
CATALOGUE_TIMEOUT = 20.0


def _base() -> str:
    return os.environ.get("VTU_API_BASE") or "https://vtuafrica.com.ng/portal/api/"


def _catalogue_url() -> str:
    return (
        os.environ.get("VTU_CATALOGUE_URL")
        or "https://vtuafrica.com.ng/portal/api/index.php"
    )


def _key() -> str:
    return os.environ.get("VTU_API_KEY") or ""


def _provider_error(message: str, status: int = 502) -> tuple[int, dict]:
    return status, {
        "code": status,
        "description": {"Status": "Failed", "message": message},
    }


def proxy_to_vtu(service: str, params: dict) -> tuple[int, object]:
    """Forwards one call to VTU with the server's key, never the client's."""
    key = _key()
    if not key:
        return _provider_error(
            "The backend has no VTU_API_KEY configured. Set it in the Render "
            "dashboard under Environment, then redeploy.",
            503,
        )

    query = {name: str(value) for name, value in params.items() if name != "apikey"}
    query["apikey"] = key
    url = f"{_base()}{service}/?{urllib.parse.urlencode(query)}"

    try:
        with httpx.Client(timeout=PROXY_TIMEOUT) as client:
            response = client.get(
                url,
                headers={"Accept": "application/json, text/plain, */*", "User-Agent": USER_AGENT},
            )
    except httpx.HTTPError as error:
        return _provider_error(f"The provider could not be reached: {error}")

    text = response.text
    try:
        return 200, response.json()
    except ValueError:
        return 200, text.strip()


def fetch_cable_plans(provider: str) -> dict:
    """The provider's own package list for cable purchases."""
    key = _key()
    if not key:
        return {"ok": False, "message": "VTU_API_KEY is not configured on the backend."}
    query = urllib.parse.urlencode(
        {"serviceName": "CableTV", "service": provider, "apikey": key}
    )
    try:
        with httpx.Client(timeout=CATALOGUE_TIMEOUT) as client:
            response = client.get(
                f"{_catalogue_url()}?{query}",
                headers={"Accept": "application/json, text/plain, */*", "User-Agent": USER_AGENT},
            )
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        print(f"VTU cable catalogue failed for {provider}: {error}")
        return {"ok": False, "message": "VTU cable packages could not be fetched right now."}

    description = body.get("description") if isinstance(body, dict) else None
    variations = description.get("variations") if isinstance(description, dict) else None
    if (
        not response.is_success
        or str((body or {}).get("code")) != "101"
        or not isinstance(variations, list)
    ):
        message = (
            description.get("message")
            if isinstance(description, dict)
            else None
        )
        return {
            "ok": True,
            "plans": [],
            "message": message or f"No active {provider} packages are available from VTU.",
        }

    plans = []
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        code = str(variation.get("variation") or "").strip()
        label = str(variation.get("name") or "").strip()
        try:
            amount = float(variation.get("amount"))
        except (TypeError, ValueError):
            continue
        if code and label and math.isfinite(amount) and amount > 0:
            plans.append({"code": code, "label": label, "price": amount})
    return {"ok": True, "plans": plans}


def provider_float() -> float | None:
    """The owner's VTU Africa float, or None when the provider reports none."""
    _, body = proxy_to_vtu("balance", {})

    def numeric(value):
        text = str(value if value is not None else "").strip()
        if not any(character.isdigit() for character in text):
            return None
        cleaned = "".join(
            character for character in text if character.isdigit() or character == "."
        )
        try:
            return float(cleaned)
        except ValueError:
            return None

    if isinstance(body, dict):
        direct = numeric(body.get("description"))
        if direct is not None:
            return direct
        description = body.get("description")
        if isinstance(description, dict):
            for value in description.values():
                parsed = numeric(value)
                if parsed is not None:
                    return parsed
        return None
    return numeric(body)


def with_customer_prices(service: str, plans: list[dict]) -> list[dict]:
    """Adds this app's service charge to fetched catalogue prices."""
    priced_plans = []
    for plan in plans:
        size_mb = pricing.size_in_mb(plan.get("label")) if service == "data" else None
        priced = pricing.with_fee(service, plan.get("price"), size_mb)
        priced_plans.append({**plan, **priced} if priced else plan)
    return priced_plans
