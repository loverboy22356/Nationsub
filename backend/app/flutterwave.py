"""Small Flutterwave v3 client.

Funding: a dynamic virtual account is created per top-up, tied to that amount
and reference; the webhook credits the wallet when the money arrives.

Payouts: /transfers pays the recipient from the owner's Flutterwave balance
while the customer's wallet is charged by this service first.

Docs: https://developer.flutterwave.com/docs
"""

from __future__ import annotations

import hmac
import os
import urllib.parse

import httpx

REQUEST_TIMEOUT = 30.0


class FlutterwaveError(Exception):
    """A provider (or configuration) failure the routes turn into JSON."""

    def __init__(self, message: str, code: str = "provider"):
        super().__init__(message)
        self.code = code


def _api_base() -> str:
    return os.environ.get("FLW_API_BASE") or "https://api.flutterwave.com/v3"


def _secret_key() -> str:
    return os.environ.get("FLW_SECRET_KEY") or ""


def is_configured() -> bool:
    return bool(_secret_key())


def _request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    if not is_configured():
        raise FlutterwaveError("FLW_SECRET_KEY is not configured on the backend.", "unconfigured")
    url = f"{_api_base()}{path}"
    headers = {
        "Authorization": f"Bearer {_secret_key()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.request(method, url, json=body, headers=headers)
    except httpx.HTTPError as error:
        raise FlutterwaveError(f"Flutterwave could not be reached: {error}") from error

    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text}
    return response.status_code, payload if isinstance(payload, dict) else {"data": payload}


def _failure_message(status: int, payload: dict) -> str:
    data = payload.get("data")
    detail = None
    if isinstance(data, str) and data:
        detail = data
    elif isinstance(data, dict) and (data.get("message") or data.get("narration")):
        detail = data.get("message") or data.get("narration")
    elif payload.get("message"):
        detail = payload.get("message")
    else:
        detail = f"Flutterwave returned HTTP {status}."
    return detail if isinstance(detail, str) else str(detail)


def create_virtual_account(
    email: str, amount, tx_ref: str, phone: str = "", name: str = ""
) -> dict:
    """Creates a one-amount virtual account for a single top-up."""
    body = {
        "email": email,
        "amount": amount,
        "currency": "NGN",
        "tx_ref": tx_ref,
        "is_permanent": False,
    }
    if phone:
        body["phonenumber"] = phone
    if name:
        body["narration"] = name

    status, payload = _request("POST", "/virtual-account-numbers", body)
    data = payload.get("data") or {}
    if payload.get("status") != "success" or not (isinstance(data, dict) and data.get("account_number")):
        raise FlutterwaveError(_failure_message(status, payload))
    return {
        "accountNumber": str(data.get("account_number")),
        "bankName": str(data.get("bank_name") or data.get("account_bank") or "Flutterwave MFB"),
        "accountName": str(data.get("narration") or name or ""),
        "flwRef": str(data.get("flw_ref") or ""),
        "orderRef": str(data.get("order_ref") or ""),
    }


def _split_name(name: str) -> tuple[str, str]:
    parts = [part for part in str(name or "").strip().split() if part]
    if not parts:
        return "Nation", "Sub"
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def create_permanent_account(
    email: str, name: str, bvn: str, tx_ref: str, phone: str = ""
) -> dict:
    """Issues the customer's own permanent NGN transfer account.

    Flutterwave calls this a *static* virtual account: it never expires, and it
    is registered to a real person, which is why a sender's bank resolves the
    account to the customer's own name instead of the business name. That
    registration is also why Flutterwave requires the customer's BVN (or NIN) --
    there is no way to open a named account without it.

    No `amount` is sent: an amount would turn it into a single-use dynamic
    account. The BVN is forwarded to Flutterwave and never stored here.
    """
    first, last = _split_name(name)
    body = {
        "email": email,
        "currency": "NGN",
        "is_permanent": True,
        "tx_ref": tx_ref,
        "firstname": first,
        "lastname": last,
        "narration": (str(name or "").strip() or f"{first} {last}".strip())[:100],
        "bvn": str(bvn).strip(),
    }
    if phone:
        body["phonenumber"] = phone

    status, payload = _request("POST", "/virtual-account-numbers", body)
    data = payload.get("data") or {}
    if payload.get("status") != "success" or not (
        isinstance(data, dict) and data.get("account_number")
    ):
        raise FlutterwaveError(_failure_message(status, payload))
    return {
        "accountNumber": str(data.get("account_number")),
        "bankName": str(data.get("bank_name") or "Flutterwave MFB"),
        "accountName": str(
            data.get("narration") or str(name or "").strip() or f"{first} {last}".strip()
        ),
        "flwRef": str(data.get("flw_ref") or ""),
        "orderRef": str(data.get("order_ref") or ""),
    }


def verify_transaction(transaction_id) -> dict | None:
    """Server-side re-check so a forged webhook body cannot mint wallet credit."""
    if not is_configured():
        return None
    status, payload = _request(
        "GET", f"/transactions/{urllib.parse.quote(str(transaction_id))}/verify"
    )
    if payload.get("status") != "success":
        return None
    data = payload.get("data") or {}
    return {
        "status": str(data.get("status") or "").lower(),
        "amount": float(data.get("amount") or 0),
        "currency": str(data.get("currency") or ""),
        "txRef": str(data.get("tx_ref") or ""),
    }


def verify_by_reference(tx_ref: str) -> dict | None:
    """Looks a payment up by our own reference.

    This lets a wallet credit itself without waiting for a webhook.
    """
    if not is_configured() or not tx_ref:
        return None
    query = urllib.parse.quote(str(tx_ref), safe="")
    _, payload = _request("GET", f"/transactions/verify_by_reference?tx_ref={query}")
    if payload.get("status") != "success":
        return None
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    return {
        "id": str(data.get("id") or ""),
        "status": str(data.get("status") or "").lower(),
        "amount": float(data.get("amount") or 0),
        "currency": str(data.get("currency") or ""),
        "flwRef": str(data.get("flw_ref") or ""),
        "paymentType": str(data.get("payment_type") or ""),
    }


def resolve_account(account_number: str, bank_code: str) -> dict:
    """Confirms the account holder before money is sent."""
    status, payload = _request(
        "POST",
        "/accounts/resolve",
        {"account_number": str(account_number), "account_bank": str(bank_code)},
    )
    data = payload.get("data") or {}
    if payload.get("status") != "success" or not (isinstance(data, dict) and data.get("account_name")):
        raise FlutterwaveError(_failure_message(status, payload))
    return {
        "accountName": str(data.get("account_name")),
        "accountNumber": str(data.get("account_number") or account_number),
    }


def create_transfer(
    bank_code: str,
    account_number: str,
    amount,
    reference: str,
    narration: str = "Nation Sub transfer",
    callback_url: str | None = None,
) -> dict:
    """Pays a Nigerian bank account out of the owner's Flutterwave balance."""
    body = {
        "account_bank": str(bank_code),
        "account_number": str(account_number),
        "amount": float(amount),
        "currency": "NGN",
        "debit_currency": "NGN",
        "narration": (narration or "Nation Sub transfer")[:100],
        "reference": str(reference),
    }
    if callback_url:
        body["callback_url"] = callback_url

    status, payload = _request("POST", "/transfers", body)
    data = payload.get("data") or {}
    if payload.get("status") != "success" or not (isinstance(data, dict) and data.get("id")):
        raise FlutterwaveError(_failure_message(status, payload))
    return {
        "id": str(data.get("id")),
        "status": str(data.get("status") or "NEW"),
        "reference": str(data.get("reference") or reference),
        "accountName": str(data.get("full_name") or ""),
        "bankName": str(data.get("bank_name") or ""),
        "fee": float(data.get("fee") or 0),
    }


def list_banks(country: str = "NG") -> list[dict]:
    """The bank codes Flutterwave resolves and pays against."""
    if not is_configured():
        return []
    _, payload = _request("GET", f"/banks/{country}")
    data = payload.get("data") or []
    banks = []
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if code and name:
            banks.append({"code": code, "name": name})
    return banks


def verify_webhook_hash(header) -> bool:
    """Flutterwave sends the configured secret hash in `verif-hash`."""
    expected = os.environ.get("FLW_SECRET_HASH") or ""
    if not expected or not header:
        return False
    return hmac.compare_digest(str(header), expected)
