"""Compatibility entry point for Render's default Python start command.

If the service's Start Command is left at Render's default,

    gunicorn your_application.wsgi

gunicorn imports this module and looks for a WSGI callable named ``wsgi``.
The API itself is ASGI (FastAPI), so this file wraps it with a2wsgi and exposes
that callable. Deployments that set a proper start command instead use one of:

    uvicorn main:app --host 0.0.0.0 --port $PORT      (Root Directory empty)
    uvicorn app.main:app --host 0.0.0.0 --port $PORT  (Root Directory: backend)

and never import this file.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "backend"))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app as _asgi_app  # noqa: E402

wsgi = ASGIMiddleware(_asgi_app)
