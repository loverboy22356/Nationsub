# Nation Sub backend (Python / Render)

FastAPI service that owns the money logic for the app: plan pricing, the
wallet, purchases, Flutterwave funding and bank transfers. The Flutter app
never picks a price and never sees `VTU_API_KEY` or `FLW_SECRET_KEY`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | liveness probe used by Render |
| GET | `/plans?network=mtn` | data catalogue with the service charge |
| GET | `/cable-plans?provider=dstv` | cable packages with the service charge |
| ANY | `/vtu/<service>` | authenticated proxy to VTU Africa |
| GET | `/wallet` | balance and recent entries |
| POST | `/wallet/topup` | Flutterwave virtual account for a top-up |
| GET | `/transfer/quote?amount=` | transfer price |
| GET | `/transfer/verify?accountNo=&bankcode=` | resolve a recipient |
| POST | `/transfer` | pay a bank account through Flutterwave |
| POST | `/webhook/flutterwave` | funding + payout notifications |

Fees live in `app/pricing.py`, plan prices in `app/catalogue_data.py`.
Regenerate the catalogue after changing the Firebase function's catalogue:

```bash
node tools/sync_catalogue.js
```

## Deploy on Render

### Option A — blueprint

1. Push this repository to GitHub.
2. Render → **New** → **Blueprint** → pick the repository. `render.yaml` at the
   repository root creates the `nationsub-api` service with root directory
   `backend`.
3. Render asks for the `sync: false` variables. Fill them in (see below).

### Option B — manual

- **New** → **Web Service** → the repository.
- Language: **Python 3**, root directory: `backend`.
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`

### Environment variables

| Key | Value |
| --- | --- |
| `VTU_API_KEY` | the VTU Africa portal key (stays server-side) |
| `FLW_SECRET_KEY` | Flutterwave secret key |
| `FLW_SECRET_HASH` | Flutterwave webhook secret hash |
| `FIREBASE_SERVICE_ACCOUNT` | the Firebase service-account JSON on one line (Render secret) |
| `FIREBASE_PROJECT_ID` | `nationsub-84906` |
| `PYTHON_VERSION` | `3.12.6` |

`FIREBASE_SERVICE_ACCOUNT` is used to verify the app's Firebase ID tokens and
to read/write the Firestore wallet. Alternatively upload the JSON as a Render
**Secret File** and set `FIREBASE_SERVICE_ACCOUNT_FILE=/etc/secrets/<name>`.

Create the key in Firebase → Project settings → Service accounts →
*Generate new private key*. Treat it as a password: anyone holding it can read
and write the whole project.

### After the first deploy

- Copy the service URL, e.g. `https://nationsub-api.onrender.com`, and open
  `/health` — it should answer `{"ok":true,...}`.
- Flutterwave dashboard → Settings → Webhooks →
  `https://nationsub-api.onrender.com/webhook/flutterwave`, using the same
  secret hash as `FLW_SECRET_HASH`.
- Point the Flutter app at it:
  `flutter run -d windows --dart-define=NATIONSB_API=https://nationsub-api.onrender.com`

Free instances sleep after 15 minutes idle, so the first request after a pause
takes ~30–60 seconds. A paid instance avoids that.

## Local run

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # Windows
.venv/bin/pip install -r requirements.txt          # macOS / Linux
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

For a run without Firebase credentials, use the JSON ledger and skip sign-in
checks (development only):

```bash
NATIONSB_WALLET_FILE=./.local-wallet.json NATIONSB_ALLOW_ANON=1 \
  .venv/Scripts/python -m uvicorn app.main:app --port 8000
```
