"""Gunicorn settings for Render's default start command.

When a Render Python service has no start command of its own, Render runs:

    gunicorn your_application.wsgi

Gunicorn loads ``gunicorn.conf.py`` from the working directory automatically, so
this file is what makes that default command serve this API correctly. The
callable it serves lives in ``your_application/wsgi.py``.

What matters here:

* **worker_class** — gunicorn's default sync worker speaks WSGI only. This API is
  ASGI (FastAPI), and bridging the two with a2wsgi left requests hanging: the
  worker accepted connections and never replied, so Render's port scan reported
  "No open HTTP ports detected". The uvicorn worker runs the ASGI app directly.
* **timeout** — gunicorn kills a worker after 30s by default. The VTU proxy
  allows 45s and a cold data-plan read can take ~20s, so the stock timeout would
  cut live purchases off mid-flight and answer them with a 502.
* **bind** — gunicorn already binds ``0.0.0.0:$PORT`` when Render sets PORT, but
  it is pinned here so the port is never a surprise, and so a laptop run without
  PORT still works on 8000.
"""

import os

try:  # maintained home of the worker class (uvicorn-worker package)
    import uvicorn_worker  # noqa: F401

    worker_class = "uvicorn_worker.UvicornWorker"
except ImportError:  # the copy uvicorn itself still ships
    worker_class = "uvicorn.workers.UvicornWorker"

def _port() -> int:
    """Render's PORT, falling back to 8000. Port 0 would leave the OS to pick
    a random port that Render's scanner cannot find."""
    try:
        value = int(os.environ.get("PORT") or 0)
    except ValueError:
        value = 0
    return value if 0 < value < 65536 else 8000


bind = f"0.0.0.0:{_port()}"

# Render sets WEB_CONCURRENCY from the instance's CPU count. Firebase Admin and
# FastAPI are imported per worker, so stay small on the free instance.
workers = int(os.environ.get("WEB_CONCURRENCY") or 2)

# Long enough for the 45s VTU proxy and slow provider catalogue reads.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
