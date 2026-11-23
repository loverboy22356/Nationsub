"""Target of Render's default start command.

Render starts Python web services with

    gunicorn your_application.wsgi

which gunicorn reads as "import the module your_application.wsgi and serve its
`application` callable". The API is ASGI (FastAPI), so `application` here is the
FastAPI app itself and gunicorn is told to use its uvicorn worker
(`worker_class` in the repository's gunicorn.conf.py). That worker speaks ASGI
natively, which is what uvicorn does on a laptop.

An earlier version wrapped the app in a2wsgi to fake a WSGI callable. Under
gunicorn's sync worker that wrapper accepted connections and then never
answered, which is what Render reported as "No open HTTP ports detected".

Services that set their own start command never touch this module:

    uvicorn main:app --host 0.0.0.0 --port $PORT      (root directory empty)
    uvicorn app.main:app --host 0.0.0.0 --port $PORT  (root directory: backend)
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from app.main import app as application  # noqa: E402,F401  (ASGI app for gunicorn)
