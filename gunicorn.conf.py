"""Gunicorn settings for Render's default start command.

When a Render Python service has no start command of its own, Render runs:

    gunicorn your_application.wsgi

Gunicorn loads ``gunicorn.conf.py`` from the working directory automatically, so
this file tunes that default command. The WSGI callable itself lives in
``your_application/wsgi.py`` (an a2wsgi wrapper around the FastAPI app in
``backend/app/main.py``).

What matters here:

* **timeout** — gunicorn kills a worker after 30s by default. The VTU proxy
  allows 45s and a cold data-plan read can take ~20s, so the stock timeout would
  cut live purchases off mid-flight and answer them with a 502.
* **bind** — gunicorn already binds ``0.0.0.0:$PORT`` when Render sets PORT, but
  it is pinned here so the port is never a surprise, and so a laptop run without
  PORT still works on 8000.
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT') or 8000}"

# Firebase Admin and FastAPI are imported per worker; two workers stay well
# inside the 512 MB free instance.
workers = int(os.environ.get("WEB_CONCURRENCY") or 2)

# Long enough for the 45s VTU proxy and slow provider catalogue reads.
timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()
