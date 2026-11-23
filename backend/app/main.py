"""Nation Sub backend — Python service for Render.

Same HTTP contract the Flutter app already calls:

    GET  /health                 liveness probe
    GET  /plans?network=mtn      data catalogue with the app's service charge
    GET  /cable-plans?provider=  cable packages with the app's service charge
    ALL  /vtu/<service>          authenticated proxy to VTU Africa
    GET  /wallet                 signed-in user's balance and recent entries
    POST /wallet/topup           Flutterwave virtual account for one top-up
    GET  /transfer/quote         what a bank transfer costs
    GET  /transfer/verify        resolve a recipient account
    POST /transfer               pay a bank account through Flutterwave
    POST /webhook/flutterwave    Flutterwave payment/payout notification

Money rules live on this side of the wire: the app never chooses a price and
never sees VTU_API_KEY or FLW_SECRET_KEY.

Start locally:  uvicorn app.main:app --reload --port 8000
On Render:      uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import math
import os
import re
import time

from fastapi import Body, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import (
    firebase,
    flutterwave,
    pricing,
    provider_catalogue,
    provider_services,
    purchases,
    vtu,
    wallet,
)
from .firebase import FirebaseUnavailable

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("nationsub")

# Only these VTU services may be reached through the proxy.
ALLOWED_SERVICES = {
    "airtime",
    "data",
    "betpay",
    "merchant-verify",
    "electric",
    "paytv",
    "exam-pin",
    "airtime-pin",
    "airtime-cash",
    "balance",
}

app = FastAPI(title="Nation Sub API", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-store")
    return response


# ------------------------------------------------------------------- helpers


def send_json(status: int, payload) -> JSONResponse:
    return JSONResponse(status_code=status, content=jsonable_encoder(payload))


def _round_int(value):
    """Math.round(Number(value)) — half away from zero, None when not a number."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return int(math.floor(number + 0.5))


def _amount(value) -> str:
    """Formats naira for messages: 1000.0 -> "1,000", 1000.5 -> "1,000.50"."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.2f}"


def require_user(authorization: str | None) -> dict:
    if os.environ.get("NATIONSB_ALLOW_ANON") == "1":
        return {"uid": "local-dev"}

    header = authorization or ""
    token = header[7:].strip() if header.startswith("Bearer ") else ""
    if not token:
        return {"error": "missing-token"}
    from firebase_admin import auth as firebase_auth

    # Initialising is kept separate from verifying: a missing or unreadable
    # service-account file is a server setup problem (503), not a bad token
    # (401), and reporting it as a bad token sent everyone looking in the app.
    try:
        firebase.ensure_app()
    except FirebaseUnavailable:
        return {"error": "no-credentials"}
    except Exception as error:
        logger.warning("Firebase credentials unusable: %s", type(error).__name__)
        return {"error": "credentials-unusable"}

    try:
        return firebase_auth.verify_id_token(token, check_revoked=False)
    except Exception as error:  # expired, revoked, wrong project, ...
        logger.warning("Firebase ID token verification failed: %s", type(error).__name__)
        return {"error": "invalid-token"}


def auth_failure(user: dict, action: str) -> JSONResponse:
    error = user.get("error")
    if error in ("no-credentials", "credentials-unusable"):
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "The server cannot load its Firebase Admin credentials, so it "
                    "cannot check sign-ins. Upload the service-account JSON as a "
                    "Render Secret File named firebase-service-account.json, or set "
                    "FIREBASE_SERVICE_ACCOUNT to the JSON itself."
                ),
            },
        )
    if error == "missing-token":
        return send_json(401, {"ok": False, "message": f"No Firebase ID token was sent. {action}"})
    return send_json(
        401,
        {"ok": False, "message": "Firebase rejected this ID token. Sign out, sign in again, and retry."},
    )


def _provider_auth_failure(user: dict) -> JSONResponse:
    """The 401 shape the VTU proxy has always returned."""
    if user.get("error") == "no-credentials":
        return send_json(
            503,
            {
                "code": 503,
                "description": {
                    "Status": "Failed",
                    "message": (
                        "This server has no Firebase Admin credentials, so sign-ins cannot "
                        "be checked. Set FIREBASE_SERVICE_ACCOUNT on Render and redeploy."
                    ),
                },
            },
        )
    return send_json(
        401,
        {
            "code": 401,
            "description": {
                "Status": "Failed",
                "message": (
                    "No Firebase ID token was sent. Sign in before requesting this service."
                    if user.get("error") == "missing-token"
                    else "Firebase rejected this ID token. Sign out, sign in again, and retry."
                ),
            },
        },
    )


def credit_for_topup(topup: dict, paid_amount) -> float:
    """What lands in the wallet for a paid top-up: ₦1,000 credits ₦950."""
    try:
        recorded = float(topup.get("creditAmount"))
    except (TypeError, ValueError, AttributeError):
        recorded = 0
    if math.isfinite(recorded) and recorded > 0:
        return pricing.money(recorded)
    if not topup or topup.get("fee") is None:
        # Top-ups issued before the funding charge existed credit in full.
        return pricing.money(paid_amount)
    return max(0.0, float(paid_amount) - pricing.FUNDING_FEE)


def reconcile_topups(uid: str) -> dict:
    """Credits any top-up Flutterwave says was actually paid."""
    if not flutterwave.is_configured():
        return {"checked": 0, "credited": 0}
    try:
        pending = wallet.pending_topups(uid, 5)
    except Exception as error:
        logger.warning("wallet reconcile: cannot list top-ups: %s", error)
        return {"checked": 0, "credited": 0}

    credited = 0
    for topup in pending:
        tx_ref = topup.get("txRef") or ""
        try:
            payment = flutterwave.verify_by_reference(tx_ref)
            if not payment or payment.get("status") != "successful":
                continue
            if str(payment.get("currency") or "").upper() != "NGN":
                continue
            amount = float(payment.get("amount") or 0)
            if amount <= 0:
                continue
            credit_amount = credit_for_topup(topup, amount)
            if credit_amount <= 0:
                continue
            result = wallet.credit(
                uid,
                credit_amount,
                reference=f"flw-{payment.get('id') or tx_ref}",
                description="Wallet top-up",
                tx_ref=tx_ref,
                flw_ref=payment.get("flwRef") or "",
            )
            wallet.mark_topup_paid(
                tx_ref,
                {"amount": amount, "creditAmount": credit_amount, "transactionId": payment.get("id")},
            )
            if result.get("credited"):
                credited += 1
            logger.info(
                "wallet reconcile: %s paid, credited=%s, balance=%s",
                tx_ref,
                result.get("credited"),
                result.get("balance"),
            )
        except Exception as error:
            logger.warning("wallet reconcile failed for %s: %s", tx_ref, error)
    return {"checked": len(pending), "credited": credited}


# -------------------------------------------------------------------- routes


@app.get("/")
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "nationsub-api",
        "runtime": "python",
        "provider": "configured" if os.environ.get("VTU_API_KEY") else "missing-key",
        "firebase": "configured" if firebase.has_credentials() else "missing-credentials",
        "firebaseCheck": firebase.probe(),
        "wallet": "file" if wallet.file_store_enabled() else "firestore",
    }


@app.get("/wallet")
def read_wallet(authorization: str | None = Header(default=None)):
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in before requesting your wallet.")

    try:
        synced = reconcile_topups(user["uid"])
        current = wallet.balance(user["uid"])
        entries: list = []
        try:
            entries = wallet.recent_entries(user["uid"], 10)
        except Exception as error:
            logger.warning("wallet history read failed: %s", error)
        return send_json(
            200,
            {
                "ok": True,
                "balance": current,
                "currency": wallet.CURRENCY,
                "synced": synced,
                "transactions": entries,
            },
        )
    except FirebaseUnavailable:
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "This server has no Firebase Admin credentials, so the wallet "
                    "cannot be read. Set FIREBASE_SERVICE_ACCOUNT on Render and redeploy."
                ),
            },
        )
    except Exception as error:
        text = str(error)
        permission_denied = "permission" in text.lower()
        logger.warning("wallet read failed: %s", text)
        return send_json(
            403 if permission_denied else 502,
            {
                "ok": False,
                "message": (
                    "Firestore denied this wallet read."
                    if permission_denied
                    else f"The wallet could not be loaded from Firestore: {text}"
                ),
            },
        )


@app.post("/wallet/topup")
def create_topup(
    payload: dict | None = Body(default=None),
    authorization: str | None = Header(default=None),
):
    # VTU Africa has no funding API, so funding is handled here: Flutterwave
    # issues a virtual account for this exact amount and the webhook credits
    # the wallet once the transfer lands.
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in before requesting a top-up.")

    body = payload or {}
    amount = _round_int(body.get("amount"))
    if amount is None or amount < 100:
        return send_json(400, {"ok": False, "message": "The smallest top-up is ₦100."})

    credit_amount = pricing.money(amount - pricing.FUNDING_FEE)
    if amount > 500000:
        return send_json(
            400, {"ok": False, "message": "A single top-up cannot be more than ₦500,000."}
        )
    if not flutterwave.is_configured():
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "Card and bank-transfer funding is not configured: the backend "
                    "needs FLW_SECRET_KEY and FLW_SECRET_HASH."
                ),
            },
        )

    email = user.get("email") or os.environ.get("NATIONSB_DEV_EMAIL") or "funding@nationsub.app"
    display_name = str(body.get("name") or user.get("name") or "Nation Sub wallet")
    phone = str(body.get("phone") or "")
    tx_ref = f"nationsub-{str(user['uid'])[:8]}-{int(time.time() * 1000)}"

    try:
        account = flutterwave.create_virtual_account(
            email=email, amount=amount, tx_ref=tx_ref, phone=phone, name=display_name
        )
        wallet.save_topup(
            tx_ref,
            {
                "uid": user["uid"],
                "amount": amount,
                "creditAmount": credit_amount,
                "fee": pricing.FUNDING_FEE,
                "email": email,
                "accountNumber": account["accountNumber"],
                "bankName": account["bankName"],
                "flwRef": account["flwRef"],
            },
        )
        return send_json(
            200,
            {
                "ok": True,
                "amount": amount,
                "fee": pricing.FUNDING_FEE,
                "creditAmount": credit_amount,
                "accountNumber": account["accountNumber"],
                "bankName": account["bankName"],
                "accountName": account.get("accountName") or display_name,
                "txRef": tx_ref,
                "balance": wallet.balance(user["uid"]),
                "note": (
                    f"Transfer exactly ₦{_amount(amount)} to this account. "
                    f"₦{_amount(credit_amount)} will be added to your wallet "
                    f"(₦{int(pricing.FUNDING_FEE):,} funding charge). "
                    "It is issued for this top-up only."
                ),
            },
        )
    except flutterwave.FlutterwaveError as error:
        status = 503 if error.code == "unconfigured" else 502
        return send_json(status, {"ok": False, "message": str(error)})
    except FirebaseUnavailable:
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "This server has no Firebase Admin credentials, so the top-up "
                    "could not be recorded. Set FIREBASE_SERVICE_ACCOUNT on Render."
                ),
            },
        )
    except Exception as error:
        logger.warning("top-up failed: %s", error)
        return send_json(502, {"ok": False, "message": str(error)})


PERMANENT_PREFIX = "nationsub-acct-"


def permanent_tx_ref(uid: str) -> str:
    """Reference carried by every transfer into a customer's own account."""
    return f"{PERMANENT_PREFIX}{uid}"


def _account_json(record: dict) -> dict:
    return {
        "accountNumber": str(record.get("accountNumber") or ""),
        "bankName": str(record.get("bankName") or ""),
        "accountName": str(record.get("accountName") or ""),
        "txRef": str(record.get("txRef") or ""),
    }


@app.get("/wallet/account")
def read_account(authorization: str | None = Header(default=None)):
    """The customer's own permanent transfer account, if one has been issued."""
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in before requesting your account number.")

    try:
        record = wallet.get_account(user["uid"])
    except FirebaseUnavailable:
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "This server has no Firebase Admin credentials, so it cannot "
                    "look up your account number."
                ),
            },
        )
    except Exception as error:
        logger.warning("account read failed: %s", error)
        return send_json(
            502,
            {"ok": False, "message": f"Your account number could not be loaded: {error}"},
        )

    return send_json(200, {"ok": True, "account": _account_json(record) if record else None})


@app.post("/wallet/account")
def create_account(
    payload: dict | None = Body(default=None),
    authorization: str | None = Header(default=None),
):
    """Issues the customer's own permanent account through Flutterwave.

    A static NGN account is registered to a real person, so Flutterwave requires
    the customer's BVN (or NIN). That is why the app asks once, and why the name
    a sender sees when their bank resolves the account is the customer's own
    name. The BVN is forwarded to Flutterwave and never stored by this service.
    """
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in before creating your account number.")

    uid = user["uid"]
    try:
        existing = wallet.get_account(uid)
    except Exception:
        existing = None
    if existing and existing.get("accountNumber"):
        return send_json(
            200, {"ok": True, "account": _account_json(existing), "created": False}
        )

    body = payload or {}
    bvn = re.sub(r"\D", "", str(body.get("bvn") or ""))
    if len(bvn) != 11:
        return send_json(400, {"ok": False, "message": "Enter your 11-digit BVN."})

    name = str(body.get("name") or user.get("name") or "").strip()
    if len(name) < 3:
        return send_json(
            400,
            {"ok": False, "message": "Enter the full name on your bank account."},
        )

    if not flutterwave.is_configured():
        return send_json(
            503,
            {
                "ok": False,
                "message": "Account numbers need FLW_SECRET_KEY on the backend.",
            },
        )

    email = user.get("email") or os.environ.get("NATIONSB_DEV_EMAIL") or "funding@nationsub.app"
    tx_ref = permanent_tx_ref(uid)

    try:
        account = flutterwave.create_permanent_account(
            email=email,
            name=name,
            bvn=bvn,
            tx_ref=tx_ref,
            phone=str(body.get("phone") or ""),
        )
        record = {**account, "name": name, "txRef": tx_ref, "email": email}
        wallet.save_account(uid, record)
        return send_json(
            200,
            {
                "ok": True,
                "created": True,
                "account": _account_json(record),
                "note": (
                    "Transfers to this account number credit your wallet "
                    f"automatically (₦{int(pricing.FUNDING_FEE):,} funding charge)."
                ),
            },
        )
    except flutterwave.FlutterwaveError as error:
        status = 503 if error.code == "unconfigured" else 502
        return send_json(status, {"ok": False, "message": str(error)})
    except FirebaseUnavailable:
        return send_json(
            503,
            {
                "ok": False,
                "message": (
                    "The account was created but could not be saved: this server "
                    "has no Firebase Admin credentials."
                ),
            },
        )
    except Exception as error:
        logger.warning("account creation failed: %s", error)
        return send_json(502, {"ok": False, "message": str(error)})


@app.get("/transfer/quote")
def transfer_quote(
    amount: str | None = None,
    authorization: str | None = Header(default=None),
):
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in again to continue.")

    purchase = purchases.describe("sendmoney", {"amount": _round_int(amount)})
    if not purchase.get("ok"):
        return send_json(
            400,
            {
                "ok": False,
                "message": purchase.get("error")
                or "The amount for this transfer is missing or invalid.",
            },
        )
    return send_json(
        200,
        {
            "ok": True,
            "amount": purchase["providerAmount"],
            "fee": purchase["fee"],
            "customerCharge": purchase["customerCharge"],
        },
    )


@app.get("/transfer/verify")
def transfer_verify(
    accountNo: str | None = None,
    bankcode: str | None = None,
    authorization: str | None = Header(default=None),
):
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in again to continue.")

    account_no = re.sub(r"\D", "", str(accountNo or ""))
    bank_code = str(bankcode or "").strip()
    if len(account_no) != 10 or not bank_code:
        return send_json(
            400,
            {"ok": False, "message": "Enter a valid 10-digit account number and choose a bank."},
        )
    try:
        resolved = flutterwave.resolve_account(account_no, bank_code)
        return send_json(200, {"ok": True, "accountName": resolved["accountName"]})
    except flutterwave.FlutterwaveError as error:
        return send_json(503 if error.code == "unconfigured" else 502, {"ok": False, "message": str(error)})


@app.post("/transfer")
def create_transfer(
    payload: dict | None = Body(default=None),
    authorization: str | None = Header(default=None),
):
    user = require_user(authorization)
    if user.get("error"):
        return auth_failure(user, "Sign in again to continue.")

    body = payload or {}
    account_no = re.sub(r"\D", "", str(body.get("accountNo") or ""))
    bank_code = str(body.get("bankcode") or "").strip()
    amount = _round_int(body.get("amount"))

    if len(account_no) != 10:
        return send_json(400, {"ok": False, "message": "Enter a valid 10-digit account number."})
    if not bank_code:
        return send_json(400, {"ok": False, "message": "Choose the recipient bank."})

    purchase = purchases.describe("sendmoney", {"amount": amount})
    if not purchase.get("ok"):
        return send_json(
            400,
            {
                "ok": False,
                "message": purchase.get("error")
                or "The amount for this transfer is missing or invalid.",
            },
        )
    if not flutterwave.is_configured():
        return send_json(
            503,
            {"ok": False, "message": "Bank transfer is not configured: the backend needs FLW_SECRET_KEY."},
        )

    # The uid stays in the reference so a later payout failure reported by the
    # webhook can find the wallet that has to be refunded.
    client_ref = re.sub(r"[^A-Za-z0-9]", "", str(body.get("ref") or int(time.time() * 1000)))
    reference = f"trf-{user['uid']}-{client_ref or int(time.time() * 1000)}"

    try:
        debit = wallet.debit(
            user["uid"],
            purchase["customerCharge"],
            reference=reference,
            description=purchase["label"],
            tx_ref=client_ref,
        )
    except FirebaseUnavailable:
        return send_json(
            503,
            {
                "ok": False,
                "message": "This server has no Firebase Admin credentials, so the wallet could not be charged.",
            },
        )

    if debit.get("duplicate"):
        return send_json(409, {"ok": False, "message": "This transfer was already submitted."})
    if debit.get("insufficient"):
        return send_json(
            402,
            {
                "ok": False,
                "message": (
                    f"Your wallet has ₦{_amount(debit.get('balance', 0))} but this transfer costs "
                    f"₦{_amount(purchase['customerCharge'])} "
                    f"(₦{_amount(purchase['providerAmount'])} + ₦{_amount(purchase['fee'])} service charge). "
                    "Fund your wallet and try again."
                ),
            },
        )

    try:
        transfer = flutterwave.create_transfer(
            bank_code=bank_code,
            account_number=account_no,
            amount=purchase["providerAmount"],
            reference=reference,
            narration=str(body.get("sender") or "Nation Sub")[:100],
        )
        bank_suffix = f" ({transfer['bankName']})" if transfer["bankName"] else ""
        return send_json(
            200,
            {
                "ok": True,
                "id": transfer["id"],
                "status": transfer["status"],
                "reference": transfer["reference"],
                "accountName": transfer["accountName"],
                "bankName": transfer["bankName"],
                "amount": purchase["providerAmount"],
                "fee": purchase["fee"],
                "customerCharge": purchase["customerCharge"],
                "balance": wallet.balance(user["uid"]),
                "message": (
                    f"₦{_amount(purchase['providerAmount'])} is on its way to "
                    f"{transfer['accountName'] or 'the recipient'}"
                    f"{bank_suffix}. Reference {transfer['reference']}."
                ),
            },
        )
    except Exception as error:
        # The payout was refused, so the customer keeps their money.
        try:
            wallet.credit(
                user["uid"],
                purchase["customerCharge"],
                reference=f"{reference}-refund",
                description="Refund: bank transfer",
                source="refund",
                tx_ref=client_ref,
            )
        except Exception as refund_error:
            logger.warning("transfer refund FAILED for %s: %s", reference, refund_error)
        status = 503 if getattr(error, "code", None) == "unconfigured" else 502
        return send_json(
            status,
            {"ok": False, "message": f"{error} Nothing was taken from your wallet."},
        )


@app.post("/webhook/flutterwave")
def flutterwave_webhook(
    payload: dict | None = Body(default=None),
    verif_hash: str | None = Header(default=None, alias="verif-hash"),
):
    # Public endpoint: authenticated by the verif-hash Flutterwave sends, not by
    # a Firebase token.
    if not flutterwave.verify_webhook_hash(verif_hash):
        return send_json(401, {"status": "rejected", "message": "Invalid verif-hash."})

    body = payload or {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}

    # Payout events from /transfers. A transfer the bank rejects has to give
    # the customer their wallet money back: it was charged when submitted.
    if str(body.get("event") or "") == "transfer.completed":
        reference = str(data.get("reference") or "")
        state = str(data.get("status") or "").upper()
        if state == "SUCCESSFUL":
            return send_json(200, {"status": "ok"})
        match = re.fullmatch(r"trf-([A-Za-z0-9]+)-([A-Za-z0-9]+)", reference)
        if not match:
            logger.warning("flutterwave webhook: unknown transfer %s", reference)
            return send_json(200, {"status": "ignored-reference"})
        purchase = purchases.describe("sendmoney", {"amount": _round_int(data.get("amount"))})
        if not purchase.get("ok"):
            return send_json(200, {"status": "ignored-amount"})
        refund = wallet.credit(
            match.group(1),
            purchase["customerCharge"],
            reference=f"{reference}-refund",
            description="Refund: bank transfer",
            source="refund",
            tx_ref=match.group(2),
        )
        return send_json(
            200,
            {"status": "refunded", "credited": refund.get("credited"), "balance": refund.get("balance")},
        )

    tx_ref = str(data.get("tx_ref") or "")
    if not tx_ref or str(data.get("status") or "") != "successful":
        return send_json(200, {"status": "ignored"})

    topup = wallet.get_topup(tx_ref)
    if not topup and tx_ref.startswith(PERMANENT_PREFIX):
        # Money sent to a customer's own permanent account. There is no top-up
        # record per transfer, so the wallet is found from the reference.
        try:
            account = wallet.get_account(tx_ref[len(PERMANENT_PREFIX):])
        except Exception as error:
            logger.warning("flutterwave webhook: account lookup failed: %s", error)
            account = None
        if not account or not account.get("uid"):
            logger.warning("flutterwave webhook: unknown account reference %s", tx_ref)
            return send_json(200, {"status": "unknown-reference"})
        topup = {
            "uid": account.get("uid"),
            "fee": pricing.FUNDING_FEE,
            "accountNumber": account.get("accountNumber"),
            "permanent": True,
        }
    if not topup:
        logger.warning("flutterwave webhook: unknown reference %s", tx_ref)
        return send_json(200, {"status": "unknown-reference"})
    if str(data.get("currency") or "NGN").upper() != "NGN":
        return send_json(200, {"status": "ignored-currency"})

    # Confirm with the provider so a forged body cannot mint wallet credit.
    if data.get("id"):
        verified = flutterwave.verify_transaction(data.get("id"))
        if verified and verified.get("status") != "successful":
            logger.warning(
                "flutterwave webhook: transaction %s is %s; not crediting %s",
                data.get("id"),
                verified.get("status"),
                tx_ref,
            )
            return send_json(200, {"status": "not-successful"})

    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return send_json(200, {"status": "ignored-amount"})

    credit_amount = credit_for_topup(topup, amount)
    if credit_amount <= 0:
        return send_json(200, {"status": "ignored-amount"})

    reference = f"flw-{data.get('id') or data.get('flw_ref') or ''}"
    if reference == "flw-":
        # A permanent account is credited many times under one tx_ref, so a
        # transfer without a provider id still needs its own ledger key.
        reference = f"{tx_ref}-{int(time.time() * 1000)}"

    result = wallet.credit(
        topup.get("uid"),
        credit_amount,
        reference=reference,
        description="Wallet top-up",
        tx_ref=tx_ref,
        flw_ref=str(data.get("flw_ref") or topup.get("flwRef") or ""),
    )
    return send_json(
        200,
        {"status": "ok", "credited": result.get("credited"), "balance": result.get("balance")},
    )


@app.get("/providers")
def providers(service: str | None = None):
    """Electricity billers, cable providers and banks, straight from source.

    The app asks here instead of carrying its own copy of these lists, so a
    provider the gateway stops selling disappears from the app by itself.
    """
    kind = (service or "").strip().lower()

    if kind in ("electricity", "electric", "bill", "bills"):
        rows, source = provider_services.electricity_providers()
        if not rows:
            return send_json(
                502,
                {"ok": False, "message": "VTU's biller list could not be read right now."},
            )
        return send_json(
            200,
            {"ok": True, "service": "electricity", "source": source, "providers": rows},
        )

    if kind in ("cable", "tv"):
        rows, source = provider_services.cable_providers()
        if not rows:
            return send_json(
                502,
                {"ok": False, "message": "No cable provider is returning packages right now."},
            )
        return send_json(
            200, {"ok": True, "service": "cable", "source": source, "providers": rows}
        )

    if kind in ("network", "networks"):
        _, source = provider_catalogue.plans()
        rows = provider_catalogue.networks()
        if not rows:
            return send_json(
                502, {"ok": False, "message": "The data catalogue is not available right now."}
            )
        return send_json(
            200, {"ok": True, "service": "network", "source": source, "providers": rows}
        )

    if kind in ("bank", "banks"):
        rows, source = provider_services.banks()
        if not rows:
            return send_json(
                502, {"ok": False, "message": "The bank list could not be loaded right now."}
            )
        return send_json(
            200, {"ok": True, "service": "bank", "source": source, "banks": rows}
        )

    return send_json(
        400, {"ok": False, "message": "Unknown provider list. Use electricity, cable or bank."}
    )


@app.get("/cable-plans")
def cable_plans(provider: str | None = None):
    provider_id = (provider or "").lower()
    available, _ = provider_services.cable_providers()
    if provider_id not in {row["id"] for row in available}:
        return send_json(400, {"ok": False, "message": "Unknown cable provider."})

    catalogue = vtu.fetch_cable_plans(provider_id)
    if not catalogue.get("ok"):
        return send_json(502, {"ok": False, "message": catalogue.get("message")})

    plans = vtu.with_customer_prices("cable", catalogue.get("plans") or [])
    response = {
        "ok": True,
        "source": "provider",
        "provider": provider_id,
        "plans": plans,
    }
    if not plans:
        response["message"] = catalogue.get("message")
    return send_json(200, response)


@app.get("/plans")
def plans(network: str | None = None):
    # Read from VTU's published plan table (cached for a few minutes), not from
    # a baked-in list: the snapshot is only used when the table is unreachable.
    catalogue, source = provider_catalogue.plans()

    if network:
        network_id = network.lower()
        network_plans = catalogue.get(network_id)
        if network_plans is None:
            return send_json(404, {"ok": False, "message": f'Unknown network "{network_id}".'})
        return send_json(
            200,
            {
                "ok": True,
                "network": network_id,
                "source": source,
                "plans": vtu.with_customer_prices("data", network_plans),
            },
        )

    return send_json(
        200,
        {
            "ok": True,
            "source": source,
            "networks": [
                {
                    "id": network_id,
                    "label": provider_catalogue.NETWORK_LABELS.get(network_id)
                    or network_id.upper(),
                    "plans": vtu.with_customer_prices("data", network_plans),
                }
                for network_id, network_plans in catalogue.items()
            ],
        },
    )


def _handle_vtu(service: str, params: dict, body: dict, authorization: str | None) -> JSONResponse:
    if service not in ALLOWED_SERVICES:
        return send_json(404, {"ok": False, "message": "Unknown service."})

    user = require_user(authorization)
    if user.get("error"):
        return _provider_auth_failure(user)

    merged = dict(params)
    for name, value in body.items():
        if value is not None:
            merged[name] = str(value)

    # The price is worked out here, never taken from the app, and the money
    # comes out of the customer's wallet instead of the owner's VTU float.
    cable_plan_list: list = []
    if service == "paytv":
        catalogue = vtu.fetch_cable_plans(str(merged.get("service") or "").lower())
        cable_plan_list = catalogue.get("plans") or []

    # Data bundles are priced from the provider's own plan table, so a plan the
    # app names is checked against what VTU actually sells before any charge.
    data_plan = None
    if service == "data":
        data_plan = provider_catalogue.plan(merged.get("service"), merged.get("DataPlan"))

    purchase = purchases.describe(service, merged, cable_plan_list, data_plan)
    if purchase.get("error"):
        return send_json(
            400,
            {"code": 400, "description": {"Status": "Failed", "message": purchase["error"]}},
        )

    charged = None
    if purchase.get("ok"):
        try:
            float_balance = vtu.provider_float()
        except Exception:
            float_balance = None
        if float_balance is not None and float_balance < purchase["providerAmount"]:
            return send_json(
                503,
                {
                    "code": 503,
                    "description": {
                        "Status": "Failed",
                        "message": (
                            "The provider wallet is too low for this purchase. Top up the "
                            "VTU Africa balance and try again. Nothing was charged."
                        ),
                    },
                },
            )

        client_ref = merged.get("ref") or f"{user['uid']}-{int(time.time() * 1000)}"
        reference = f"buy-{client_ref}"
        try:
            debit = wallet.debit(
                user["uid"],
                purchase["customerCharge"],
                reference=reference,
                description=purchase["label"],
                tx_ref=str(merged.get("ref") or ""),
            )
        except FirebaseUnavailable:
            return send_json(
                503,
                {
                    "code": 503,
                    "description": {
                        "Status": "Failed",
                        "message": (
                            "This server has no Firebase Admin credentials, so the wallet "
                            "could not be charged. Set FIREBASE_SERVICE_ACCOUNT on Render."
                        ),
                    },
                },
            )
        if debit.get("insufficient"):
            return send_json(
                402,
                {
                    "code": 402,
                    "description": {
                        "Status": "Failed",
                        "message": (
                            f"Your wallet has ₦{_amount(debit.get('balance', 0))} but this purchase costs "
                            f"₦{_amount(purchase['customerCharge'])} "
                            f"(₦{_amount(purchase['providerAmount'])} + ₦{_amount(purchase['fee'])} service charge). "
                            "Fund your wallet and try again."
                        ),
                    },
                },
            )
        if not debit.get("debited"):
            return send_json(
                502,
                {
                    "code": 502,
                    "description": {
                        "Status": "Failed",
                        "message": (
                            "This purchase reference was already used, so nothing new was charged."
                            if debit.get("duplicate")
                            else "The wallet could not be charged, so nothing was sent."
                        ),
                    },
                },
            )
        charged = {"reference": reference, "purchase": purchase}

    status, result = vtu.proxy_to_vtu(service, merged)
    succeeded = status == 200 and isinstance(result, dict) and str(result.get("code")) == "101"
    if charged and not succeeded:
        # The provider refused or could not be reached, so the customer keeps
        # their money: the charge is credited straight back.
        try:
            wallet.credit(
                user["uid"],
                charged["purchase"]["customerCharge"],
                reference=f"{charged['reference']}-refund",
                description=f"Refund: {charged['purchase']['label']}",
                source="refund",
                tx_ref=str(merged.get("ref") or ""),
            )
            logger.warning("wallet refund issued for %s", charged["reference"])
        except Exception as error:
            logger.warning("wallet refund FAILED for %s: %s", charged["reference"], error)
    return send_json(status, result)


@app.api_route("/vtu/{service}", methods=["GET", "POST"])
@app.api_route("/vtu/{service}/", methods=["GET", "POST"])
async def vtu_proxy(service: str, request: Request):
    body: dict = {}
    if request.method == "POST":
        try:
            parsed = await request.json()
            body = parsed if isinstance(parsed, dict) else {}
        except Exception:
            body = {}
    params = {name: str(value) for name, value in request.query_params.items()}
    return await run_in_threadpool(
        _handle_vtu, service, params, body, request.headers.get("authorization")
    )
