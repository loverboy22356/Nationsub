"""Firebase Admin bootstrap.

On Render there is no Google Cloud metadata server, so the service account has
to arrive as configuration:

  FIREBASE_SERVICE_ACCOUNT           the whole service-account JSON, either raw
                                     or base64 encoded (Render environment
                                     variable, marked as a secret)
  FIREBASE_SERVICE_ACCOUNT_FILE      path to the JSON file (a Render secret
                                     file is mounted at /etc/secrets/<name>)
  GOOGLE_APPLICATION_CREDENTIALS     same idea, honoured by the Google SDK

Firebase is used for exactly two things: verifying the ID tokens the Flutter
app sends, and reading/writing the Firestore wallet.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import threading

DEFAULT_PROJECT = "nationsub-84906"

_lock = threading.Lock()


class FirebaseUnavailable(RuntimeError):
    """Raised when this process has no Firebase credentials at all."""


def _service_account() -> dict | None:
    raw = (os.environ.get("FIREBASE_SERVICE_ACCOUNT") or "").strip()
    file_hint = (os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE") or "").strip()

    if file_hint:
        with open(file_hint, "r", encoding="utf-8") as handle:
            return json.load(handle)

    if not raw:
        return None

    if raw.startswith("{"):
        return json.loads(raw)

    # Render secrets are sometimes pasted base64 encoded.
    try:
        decoded = base64.b64decode(raw, validate=False).decode("utf-8")
        if decoded.strip().startswith("{"):
            return json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        pass

    if os.path.exists(raw):
        with open(raw, "r", encoding="utf-8") as handle:
            return json.load(handle)

    raise FirebaseUnavailable(
        "FIREBASE_SERVICE_ACCOUNT is not valid JSON, base64 JSON or a file path."
    )


def project_id() -> str:
    return (
        os.environ.get("FIREBASE_PROJECT_ID")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
        or DEFAULT_PROJECT
    )


def ensure_app():
    """Returns the initialised firebase_admin app, or raises FirebaseUnavailable."""
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    with _lock:
        try:
            return firebase_admin.get_app()
        except ValueError:
            pass

        account = _service_account()
        if account is not None:
            credential = credentials.Certificate(account)
            project = account.get("project_id") or project_id()
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            credential = credentials.ApplicationDefault()
            project = project_id()
        else:
            raise FirebaseUnavailable(
                "Firebase credentials are missing on this server. Set "
                "FIREBASE_SERVICE_ACCOUNT to the service-account JSON for "
                f"{project_id()}."
            )

        return firebase_admin.initialize_app(credential, {"projectId": project})


def has_credentials() -> bool:
    return bool(
        (os.environ.get("FIREBASE_SERVICE_ACCOUNT") or "").strip()
        or (os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE") or "").strip()
        or (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    )
