"""Render entry point.

Render services whose Root Directory is the repository root start the app with

    uvicorn main:app --host 0.0.0.0 --port $PORT

The FastAPI app itself lives in backend/app/main.py; this file only puts that
directory on the import path so `app` resolves. Services configured with
Root Directory = backend can ignore this file and use `app.main:app`.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "backend"))

from app.main import app  # noqa: E402,F401  (re-exported for uvicorn)
