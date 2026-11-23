"""Wallet ledger.

In Firebase the balance and its entries live in Firestore:

    wallets/{uid}                       { balance, currency, updatedAt }
    wallets/{uid}/transactions/{ref}    { amount, source, txRef, createdAt }
    topups/{txRef}                      { uid, amount, accountNumber, ... }

Every credit and debit is keyed by a reference inside a transaction, so a
webhook that arrives twice can never credit twice and a purchase can never
overdraw a wallet.

A JSON ledger is used instead when NATIONSB_WALLET_FILE is set (local runs and
the automated tests); the deployed service always uses Firestore.
"""

from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime, timezone

CURRENCY = "NGN"

_LEDGER_LOCK = threading.RLock()


def money(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return math.floor(number * 100 + 0.5) / 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------- JSON ledger


def file_store_enabled() -> bool:
    return bool((os.environ.get("NATIONSB_WALLET_FILE") or "").strip())


def _ledger_path() -> str:
    return (
        os.environ.get("NATIONSB_WALLET_FILE")
        or os.path.join(os.path.dirname(__file__), ".local-wallet.json")
    )


def _read_ledger() -> dict:
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"wallets": {}, "entries": {}, "topups": {}}
    return {
        "wallets": parsed.get("wallets") or {},
        "entries": parsed.get("entries") or {},
        "topups": parsed.get("topups") or {},
        "accounts": parsed.get("accounts") or {},
    }


def _write_ledger(data: dict) -> None:
    path = _ledger_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)


# ------------------------------------------------------------------ Firestore


def _store():
    from firebase_admin import firestore as fb_firestore

    from .firebase import ensure_app

    ensure_app()
    return fb_firestore.client()


def _timestamp():
    from google.cloud import firestore

    return firestore.SERVER_TIMESTAMP


def _ts(value) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------- API


def save_topup(tx_ref: str, record: dict) -> None:
    """Records the virtual account issued for a top-up so the webhook can find
    the user it belongs to from the reference alone."""
    payload = {**record, "txRef": tx_ref, "currency": CURRENCY, "status": "pending"}
    if file_store_enabled():
        with _LEDGER_LOCK:
            data = _read_ledger()
            data["topups"][tx_ref] = {**payload, "createdAt": _now()}
            _write_ledger(data)
        return
    _store().collection("topups").document(tx_ref).set(
        {**payload, "createdAt": _timestamp()}
    )


def get_topup(tx_ref: str) -> dict | None:
    if not tx_ref:
        return None
    if file_store_enabled():
        return _read_ledger()["topups"].get(tx_ref)
    snapshot = _store().collection("topups").document(tx_ref).get()
    if not snapshot.exists:
        return None
    return {"txRef": tx_ref, **(snapshot.to_dict() or {})}


def save_account(uid: str, record: dict) -> None:
    """Stores the customer's own permanent transfer account (wallets read it)."""
    payload = {**record, "uid": uid, "currency": CURRENCY}
    if file_store_enabled():
        with _LEDGER_LOCK:
            data = _read_ledger()
            accounts = data.get("accounts") or {}
            accounts[uid] = {**payload, "createdAt": _now()}
            data["accounts"] = accounts
            _write_ledger(data)
        return
    _store().collection("accounts").document(uid).set(
        {**payload, "createdAt": _timestamp()}
    )


def get_account(uid: str) -> dict | None:
    """The permanent account issued for [uid], or None if there is none yet."""
    if not uid:
        return None
    if file_store_enabled():
        return ((
            _read_ledger().get("accounts") or {}
        ).get(uid))
    snapshot = _store().collection("accounts").document(uid).get()
    if not snapshot.exists:
        return None
    return {"uid": uid, **(snapshot.to_dict() or {})}


def balance(uid: str) -> float:
    if file_store_enabled():
        return money((_read_ledger()["wallets"].get(uid) or {}).get("balance") or 0)
    snapshot = _store().collection("wallets").document(uid).get()
    if not snapshot.exists:
        return 0.0
    return money((snapshot.to_dict() or {}).get("balance") or 0)


def credit(
    uid: str,
    amount,
    reference: str | None = None,
    description: str = "Wallet top-up",
    source: str = "flutterwave",
    tx_ref: str = "",
    flw_ref: str = "",
) -> dict:
    """Adds money once per reference; a repeat returns the existing balance."""
    amount = money(amount)
    if not uid:
        raise ValueError("A wallet credit needs a user.")
    if amount <= 0:
        raise ValueError("A wallet credit needs a positive amount.")
    reference = str(reference or f"credit-{int(datetime.now().timestamp() * 1000)}")
    entry = {
        "amount": amount,
        "currency": CURRENCY,
        "source": source,
        "description": description,
        "txRef": str(tx_ref or ""),
        "flwRef": str(flw_ref or ""),
        "status": "success",
    }

    if file_store_enabled():
        with _LEDGER_LOCK:
            data = _read_ledger()
            key = f"{uid}/{reference}"
            current = money((data["wallets"].get(uid) or {}).get("balance") or 0)
            if key in data["entries"]:
                return {"credited": False, "balance": current}
            nxt = money(current + amount)
            data["wallets"][uid] = {
                "balance": nxt,
                "currency": CURRENCY,
                "updatedAt": _now(),
            }
            data["entries"][key] = {**entry, "uid": uid, "createdAt": _now()}
            _write_ledger(data)
            return {"credited": True, "balance": nxt}

    from google.cloud import firestore

    db = _store()
    wallet_ref = db.collection("wallets").document(uid)
    entry_ref = wallet_ref.collection("transactions").document(reference)

    @firestore.transactional
    def run(transaction):
        existing = entry_ref.get(transaction=transaction)
        snapshot = wallet_ref.get(transaction=transaction)
        current = money((snapshot.to_dict() or {}).get("balance") or 0) if snapshot.exists else 0.0
        if existing.exists:
            return {"credited": False, "balance": current}
        nxt = money(current + amount)
        transaction.set(
            wallet_ref,
            {"balance": nxt, "currency": CURRENCY, "updatedAt": _timestamp()},
            merge=True,
        )
        transaction.set(entry_ref, {**entry, "createdAt": _timestamp()})
        return {"credited": True, "balance": nxt}

    return run(db.transaction())


def debit(
    uid: str,
    amount,
    reference: str | None = None,
    description: str = "Purchase",
    source: str = "purchase",
    tx_ref: str = "",
) -> dict:
    """Takes a purchase out of a wallet once per reference.

    Returns {"debited": True} on success, {"duplicate": True} when the
    reference was already used, {"insufficient": True} when the balance cannot
    cover it. Nothing changes in the last two cases.
    """
    amount = money(amount)
    if not uid:
        raise ValueError("A wallet debit needs a user.")
    if amount <= 0:
        raise ValueError("A wallet debit needs a positive amount.")
    reference = str(reference or f"debit-{int(datetime.now().timestamp() * 1000)}")
    entry = {
        "amount": -amount,
        "currency": CURRENCY,
        "source": source,
        "description": description,
        "txRef": str(tx_ref or ""),
        "status": "success",
    }

    if file_store_enabled():
        with _LEDGER_LOCK:
            data = _read_ledger()
            key = f"{uid}/{reference}"
            current = money((data["wallets"].get(uid) or {}).get("balance") or 0)
            if key in data["entries"]:
                return {"debited": False, "duplicate": True, "balance": current}
            if current < amount:
                return {"debited": False, "insufficient": True, "balance": current}
            nxt = money(current - amount)
            data["wallets"][uid] = {
                "balance": nxt,
                "currency": CURRENCY,
                "updatedAt": _now(),
            }
            data["entries"][key] = {**entry, "uid": uid, "createdAt": _now()}
            _write_ledger(data)
            return {"debited": True, "balance": nxt}

    from google.cloud import firestore

    db = _store()
    wallet_ref = db.collection("wallets").document(uid)
    entry_ref = wallet_ref.collection("transactions").document(reference)

    @firestore.transactional
    def run(transaction):
        existing = entry_ref.get(transaction=transaction)
        snapshot = wallet_ref.get(transaction=transaction)
        current = money((snapshot.to_dict() or {}).get("balance") or 0) if snapshot.exists else 0.0
        if existing.exists:
            return {"debited": False, "duplicate": True, "balance": current}
        if current < amount:
            return {"debited": False, "insufficient": True, "balance": current}
        nxt = money(current - amount)
        transaction.set(
            wallet_ref,
            {"balance": nxt, "currency": CURRENCY, "updatedAt": _timestamp()},
            merge=True,
        )
        transaction.set(entry_ref, {**entry, "createdAt": _timestamp()})
        return {"debited": True, "balance": nxt}

    return run(db.transaction())


def pending_topups(uid: str, limit: int = 5) -> list[dict]:
    """Top-ups that have not been credited yet, newest first."""
    if file_store_enabled():
        rows = [
            row
            for row in _read_ledger()["topups"].values()
            if row.get("uid") == uid and row.get("status") != "paid"
        ]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        return rows[:limit]

    docs = _store().collection("topups").where("uid", "==", uid).limit(50).stream()
    rows = []
    for doc in docs:
        row = {"txRef": doc.id, **(doc.to_dict() or {})}
        if row.get("status") != "paid":
            rows.append(row)
    rows.sort(key=lambda row: _ts(row.get("createdAt")), reverse=True)
    return rows[:limit]


def mark_topup_paid(tx_ref: str, patch: dict | None = None) -> None:
    patch = patch or {}
    if file_store_enabled():
        with _LEDGER_LOCK:
            data = _read_ledger()
            topup = data["topups"].get(tx_ref)
            if not topup:
                return
            data["topups"][tx_ref] = {
                **topup,
                **patch,
                "status": "paid",
                "paidAt": _now(),
            }
            _write_ledger(data)
        return
    _store().collection("topups").document(tx_ref).set(
        {**patch, "status": "paid", "paidAt": _timestamp()}, merge=True
    )


def recent_entries(uid: str, limit: int = 10) -> list[dict]:
    if file_store_enabled():
        rows = [
            value
            for key, value in _read_ledger()["entries"].items()
            if key.startswith(f"{uid}/")
        ]
        rows.sort(key=lambda row: str(row.get("createdAt") or ""), reverse=True)
        return rows[:limit]

    from google.cloud import firestore

    docs = (
        _store()
        .collection("wallets")
        .document(uid)
        .collection("transactions")
        .order_by("createdAt", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [doc.to_dict() or {} for doc in docs]
