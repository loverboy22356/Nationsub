"""Target of Render's default start command.

Render starts Python web services with

    gunicorn your_application.wsgi

which gunicorn reads as "import the module your_application.wsgi and use its
`application` callable". The API is ASGI (FastAPI), so `application` here is a
WSGI wrapper around it, provided by a2wsgi.

Services that set their own start command never touch this module:

    uvicorn main:app --host 0.0.0.0 --port $PORT      (root directory empty)
    uvicorn app.main:app --host 0.0.0.0 --port $PORT  (root directory: backend)
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from a2wsgi import ASGIMiddleware  # noqa: E402

from app.main import app as _asgi_app  # noqa: E402

application = ASGIMiddleware(_asgi_app)
