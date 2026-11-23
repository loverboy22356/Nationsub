'use strict';

// Nation Sub backend.
//
// The Flutter app never sees VTU_API_KEY and never chooses a VTU price: it asks
// this API for the plan catalogue and for purchases, and the key stays in Firebase.
//
//   GET  /plans?network=mtn   -> catalogue served by this backend
//   ALL  /vtu/<service>       -> authenticated proxy to the VTU Africa gateway
//   GET  /wallet              -> signed-in user's wallet balance
//   POST /wallet/topup        -> Flutterwave virtual account for one top-up
//   POST /webhook/flutterwave -> Flutterwave payment notification (verif-hash)
//   GET  /health              -> liveness probe
//
// Deployment: firebase deploy --only functions, with VTU_API_KEY, FLW_SECRET_KEY
// and FLW_SECRET_HASH set as secrets.

const fs = require('fs');
const path = require('path');

const { plansFor, CATALOGUE } = require('./catalogue');
const pricing = require('./pricing');
const purchases = require('./purchases');
const wallet = require('./wallet');
const flutterwave = require('./flutterwave');

const VTU_BASE = process.env.VTU_API_BASE || 'https://vtuafrica.com.ng/portal/api/';
const VTU_CATALOGUE_URL =
  process.env.VTU_CATALOGUE_URL || 'https://vtuafrica.com.ng/portal/api/index.php';
const CABLE_PROVIDERS = new Set(['dstv', 'gotv', 'startimes', 'showmax']);
const USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36';

// Only these VTU services may be reached through the proxy.
const ALLOWED_SERVICES = new Set([
  'airtime',
  'data',
  'betpay',
  'merchant-verify',
  'electric',
  'paytv',
  'exam-pin',
  'airtime-pin',
  'airtime-cash',
  'balance',
]);

let adminApp = null;

/// Local development has no Google application credentials, so Firestore calls
/// fail even though ID-token verification still works. Dropping a service
/// account key at functions/serviceAccountKey.json (git-ignored) makes the
/// local backend read the same Firestore data as the deployed function.
function localServiceAccount() {
  const file = path.join(__dirname, 'serviceAccountKey.json');
  try {
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    console.warn(`serviceAccountKey.json could not be read: ${error.message}`);
    return null;
  }
}

function admin() {
  if (adminApp) return adminApp;
  // Required lazily so the plan catalogue works even where Admin SDK
  // credentials are absent (for example a plain `node dev-server.js` run).
  const firebaseAdmin = require('firebase-admin');
  if (firebaseAdmin.apps.length) {
    adminApp = firebaseAdmin.app();
    return adminApp;
  }
  const serviceAccount = localServiceAccount();
  adminApp = firebaseAdmin.initializeApp({
    projectId:
      serviceAccount?.project_id ||
      process.env.GCLOUD_PROJECT ||
      process.env.GCP_PROJECT ||
      'nationsub-84906',
    ...(serviceAccount ? { credential: firebaseAdmin.credential.cert(serviceAccount) } : {}),
  });
  return adminApp;
}

async function requireUser(req) {
  if (process.env.NATIONSB_ALLOW_ANON === '1') return { uid: 'local-dev' };
  const header = req.headers.authorization || req.headers.Authorization || '';
  const token = String(header).startsWith('Bearer ')
    ? String(header).slice(7).trim()
    : '';
  if (!token) return { error: 'missing-token' };
  try {
    const user = await admin().auth().verifyIdToken(token);
    return process.env.NATIONSB_LOCAL_FIRESTORE_REST === '1'
      ? { ...user, idToken: token }
      : user;
  } catch (error) {
    console.warn(`Firebase ID token verification failed: ${error.code || error.name}`);
    return { error: 'invalid-token' };
  }
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(body);
}

async function readBody(req) {
  if (req.body && typeof req.body === 'object') return req.body;
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (_) {
    return {};
  }
}

async function proxyToVtu(service, searchParams) {
  const key = process.env.VTU_API_KEY;
  if (!key) {
    return {
      status: 503,
      body: {
        code: 503,
        description: {
          Status: 'Failed',
          message:
            'The backend has no VTU_API_KEY configured. Set it with firebase functions:secrets:set VTU_API_KEY.',
        },
      },
    };
  }

  const query = new URLSearchParams();
  for (const [name, value] of searchParams.entries()) {
    if (name === 'apikey') continue; // never forwarded from the client
    query.set(name, value);
  }
  query.set('apikey', key);

  const url = `${VTU_BASE}${service}/?${query.toString()}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 45000);
  try {
    const response = await fetch(url, {
      headers: {
        Accept: 'application/json, text/plain, */*',
        'User-Agent': USER_AGENT,
      },
      signal: controller.signal,
    });
    const text = await response.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (_) {
      parsed = text.trim();
    }
    return { status: 200, body: parsed };
  } catch (error) {
    return {
      status: 502,
      body: {
        code: 502,
        description: {
          Status: 'Failed',
          message: `The provider could not be reached: ${error.message}`,
        },
      },
    };
  } finally {
    clearTimeout(timer);
  }
}

/// The provider's own package list, used both to show cable prices and to work
/// out what a cable purchase really costs before a wallet is charged.
async function fetchCablePlans(provider) {
  const key = process.env.VTU_API_KEY;
  if (!key) {
    return { ok: false, message: 'VTU_API_KEY is not configured on the backend.' };
  }
  const query = new URLSearchParams({
    serviceName: 'CableTV',
    service: provider,
    apikey: key,
  });
  try {
    const response = await fetch(`${VTU_CATALOGUE_URL}?${query}`, {
      headers: { Accept: 'application/json, text/plain, */*', 'User-Agent': USER_AGENT },
      signal: AbortSignal.timeout(20000),
    });
    const body = await response.json();
    const description = body && body.description;
    if (!response.ok || String(body?.code) !== '101' || !Array.isArray(description?.variations)) {
      return {
        ok: true,
        plans: [],
        message: description?.message || `No active ${provider} packages are available from VTU.`,
      };
    }
    const plans = description.variations
      .map((variation) => ({
        code: String(variation.variation || '').trim(),
        label: String(variation.name || '').trim(),
        price: Number(variation.amount),
      }))
      .filter((plan) => plan.code && plan.label && Number.isFinite(plan.price) && plan.price > 0);
    return { ok: true, plans };
  } catch (error) {
    console.warn(`VTU cable catalogue failed for ${provider}: ${error.message}`);
    return { ok: false, message: 'VTU cable packages could not be fetched right now.' };
  }
}

/// The owner's VTU Africa float. Checked before every purchase so a sale is
/// never attempted against an empty provider wallet. Null when the provider
/// does not report a usable number.
async function providerFloat() {
  const result = await proxyToVtu('balance', new URLSearchParams());
  const numeric = (value) => {
    const text = String(value ?? '').trim();
    if (!/\d/.test(text)) return null;
    const parsed = Number(text.replace(/[^0-9.]/g, ''));
    return Number.isFinite(parsed) ? parsed : null;
  };
  const body = result.body;
  if (body && typeof body === 'object') {
    const direct = numeric(body.description);
    if (direct !== null) return direct;
    if (body.description && typeof body.description === 'object') {
      for (const value of Object.values(body.description)) {
        const parsed = numeric(value);
        if (parsed !== null) return parsed;
      }
    }
    return null;
  }
  return numeric(body);
}

/// What actually lands in the wallet for a paid top-up: the funding charge is
/// kept by the app, so a ₦1,000 transfer credits ₦950.
function creditForTopup(topup, paidAmount) {
  const recorded = Number(topup && topup.creditAmount);
  if (Number.isFinite(recorded) && recorded > 0) return Math.round(recorded * 100) / 100;
  // Top-ups issued before the funding charge existed credit in full.
  if (!topup || topup.fee === undefined) return Math.round(Number(paidAmount) * 100) / 100;
  return Math.max(0, Number(paidAmount) - pricing.FUNDING_FEE);
}

/// Adds this app's service charge to fetched catalogue prices. Data bundles are
/// charged by size, so their label is passed through.
function withCustomerPrices(service, plans) {
  return plans.map((plan) => {
    const sizeMb = service === 'data' ? pricing.sizeInMb(plan.label) : null;
    const priced = pricing.withFee(service, plan.price, { sizeMb });
    return priced ? { ...plan, ...priced } : plan;
  });
}

/// Asks Flutterwave about this user's unpaid top-ups and credits the wallet for
/// any that were actually paid. This is what makes funding work on a machine
/// with no public webhook URL; in production the webhook usually gets there
/// first and this check simply finds nothing left to do.
async function reconcileTopups(uid) {
  if (!flutterwave.isConfigured()) return { checked: 0, credited: 0 };
  let pending = [];
  try {
    pending = await wallet.pendingTopups(uid, 5);
  } catch (error) {
    console.warn(`wallet reconcile: cannot list top-ups: ${error.message}`);
    return { checked: 0, credited: 0 };
  }
  let credited = 0;
  for (const topup of pending) {
    try {
      const payment = await flutterwave.verifyByReference(topup.txRef);
      if (!payment || payment.status !== 'successful') continue;
      if (String(payment.currency).toUpperCase() !== 'NGN') continue;
      const amount = Number(payment.amount);
      if (!Number.isFinite(amount) || amount <= 0) continue;
      const creditAmount = creditForTopup(topup, amount);
      if (!(creditAmount > 0)) continue;
      const result = await wallet.credit(uid, {
        amount: creditAmount,
        reference: `flw-${payment.id || topup.txRef}`,
        description: 'Wallet top-up',
        txRef: topup.txRef,
        flwRef: payment.flwRef,
      });
      await wallet.markTopupPaid(topup.txRef, {
        amount,
        creditAmount,
        transactionId: payment.id,
      });
      if (result.credited) credited += 1;
      console.log(
        `wallet reconcile: ${topup.txRef} paid, credited=${result.credited}, balance=${result.balance}`,
      );
    } catch (error) {
      console.warn(
        `wallet reconcile failed for ${topup.txRef}: ${error.message}`,
      );
    }
  }
  return { checked: pending.length, credited };
}

/// Framework-independent handler, used by both firebase-functions and the
/// local dev server.
async function handler(req, res) {
  const url = new URL(req.url || '/', 'http://localhost');
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (process.env.NATIONSB_LOG_REQUESTS === '1') {
    console.log(`${req.method} ${path}${url.search}`);
  }

  if (path === '/health' || path === '/') {
    return sendJson(res, 200, {
      ok: true,
      service: 'nationsub-api',
      provider: process.env.VTU_API_KEY ? 'configured' : 'missing-key',
    });
  }

  if (path === '/wallet') {
    if (req.method !== 'GET') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, {
        ok: false,
        message: user?.error === 'missing-token'
          ? 'No Firebase ID token was sent. Sign in before requesting your wallet.'
          : 'Firebase rejected this ID token. Sign out, sign in again, and retry.',
      });
    }
    try {
      const synced = await reconcileTopups(user.uid);
      const balance = await wallet.balance(user.uid, user.idToken);
      let entries = [];
      try {
        entries = await wallet.recentEntries(user.uid, 10, user.idToken);
      } catch (error) {
        console.warn(`wallet history read failed: ${error.message}`);
      }
      return sendJson(res, 200, {
        ok: true,
        balance,
        currency: wallet.CURRENCY,
        synced,
        transactions: entries,
      });
    } catch (error) {
      const missingCredentials = /default credentials/i.test(error.message || '');
      const permissionDenied = error.status === 403 || /permission denied/i.test(error.message || '');
      console.warn(`wallet read failed: ${error.message}`);
      return sendJson(res, missingCredentials ? 503 : permissionDenied ? 403 : 502, {
        ok: false,
        message: missingCredentials
          ? 'The local backend cannot reach Firestore. Add functions/serviceAccountKey.json or run against the deployed function.'
          : permissionDenied
          ? 'Firestore Security Rules denied this wallet read. Add functions/serviceAccountKey.json for the local backend, or configure an owner-only read rule for wallets/{uid} and its transactions.'
          : `The wallet could not be loaded from Firestore: ${error.message}`,
      });
    }
  }

  if (path === '/wallet/topup') {
    // VTU Africa has no funding API and the portal's transfer account expires
    // after an hour, so funding is handled here instead: Flutterwave issues a
    // virtual account for this exact amount and the webhook credits the wallet.
    if (req.method !== 'POST') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, {
        ok: false,
        message: user?.error === 'missing-token'
          ? 'No Firebase ID token was sent. Sign in before requesting a top-up.'
          : 'Firebase rejected this ID token. Sign out, sign in again, and retry.',
      });
    }
    const body = await readBody(req);
    const amount = Math.round(Number(body.amount));
    if (!Number.isFinite(amount) || amount < 100) {
      return sendJson(res, 400, {
        ok: false,
        message: 'The smallest top-up is ₦100.',
      });
    }
    const creditAmount = Math.round((amount - pricing.FUNDING_FEE) * 100) / 100;
    if (amount > 500000) {
      return sendJson(res, 400, {
        ok: false,
        message: 'A single top-up cannot be more than ₦500,000.',
      });
    }
    if (!flutterwave.isConfigured()) {
      return sendJson(res, 503, {
        ok: false,
        message:
          'Card and bank-transfer funding is not configured: the backend needs FLW_SECRET_KEY and FLW_SECRET_HASH.',
      });
    }


    const email =
      user.email || process.env.NATIONSB_DEV_EMAIL || 'funding@nationsub.app';
    const displayName = String(body.name || user.name || 'Nation Sub wallet');
    const phone = body.phone ? String(body.phone) : '';
    const txRef = `nationsub-${String(user.uid).slice(0, 8)}-${Date.now()}`;

    try {
      const account = await flutterwave.createVirtualAccount({
        email,
        amount,
        txRef,
        phone,
        name: displayName,
      });
      await wallet.saveTopup(txRef, {
        uid: user.uid,
        amount,
        creditAmount,
        fee: pricing.FUNDING_FEE,
        email,
        accountNumber: account.accountNumber,
        bankName: account.bankName,
        flwRef: account.flwRef,
      });
      return sendJson(res, 200, {
        ok: true,
        amount,
        fee: pricing.FUNDING_FEE,
        creditAmount,
        accountNumber: account.accountNumber,
        bankName: account.bankName,
        accountName: account.accountName || displayName,
        txRef,
        balance: await wallet.balance(user.uid),
        note:
          `Transfer exactly ₦${amount.toLocaleString('en-NG')} to this account. ` +
          `₦${creditAmount.toLocaleString('en-NG')} will be added to your wallet ` +
          `(₦${pricing.FUNDING_FEE.toLocaleString('en-NG')} funding charge). ` +
          'It is issued for this top-up only.',
      });
    } catch (error) {
      const status = error.code === 'unconfigured' ? 503 : 502;
      return sendJson(res, status, { ok: false, message: error.message });
    }
  }

  // Bank transfer: Flutterwave pays the recipient out of the owner's
  // Flutterwave balance, while the customer's own wallet is charged here first.
  if (path === '/transfer/quote') {
    if (req.method !== 'GET') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, { ok: false, message: 'Sign in again to continue.' });
    }
    const amount = Math.round(Number(url.searchParams.get('amount')));
    const purchase = purchases.describe('sendmoney', { amount });
    if (!purchase.ok) {
      return sendJson(res, 400, {
        ok: false,
        message:
          purchase.error || 'The amount for this transfer is missing or invalid.',
      });
    }
    return sendJson(res, 200, {
      ok: true,
      amount: purchase.providerAmount,
      fee: purchase.fee,
      customerCharge: purchase.customerCharge,
    });
  }

  if (path === '/transfer/verify') {
    if (req.method !== 'GET') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, { ok: false, message: 'Sign in again to continue.' });
    }
    const accountNo = String(url.searchParams.get('accountNo') || '').replace(/\D/g, '');
    const bankCode = String(url.searchParams.get('bankcode') || '').trim();
    if (accountNo.length !== 10 || !bankCode) {
      return sendJson(res, 400, {
        ok: false,
        message: 'Enter a valid 10-digit account number and choose a bank.',
      });
    }
    try {
      const resolved = await flutterwave.resolveAccount({
        accountNumber: accountNo,
        bankCode,
      });
      return sendJson(res, 200, { ok: true, accountName: resolved.accountName });
    } catch (error) {
      const status = error.code === 'unconfigured' ? 503 : 502;
      return sendJson(res, status, { ok: false, message: error.message });
    }
  }

  if (path === '/transfer') {
    if (req.method !== 'POST') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, { ok: false, message: 'Sign in again to continue.' });
    }
    const body = await readBody(req);
    const accountNo = String(body.accountNo || '').replace(/\D/g, '');
    const bankCode = String(body.bankcode || '').trim();
    const amount = Math.round(Number(body.amount));
    if (accountNo.length !== 10) {
      return sendJson(res, 400, {
        ok: false,
        message: 'Enter a valid 10-digit account number.',
      });
    }
    if (!bankCode) {
      return sendJson(res, 400, {
        ok: false,
        message: 'Choose the recipient bank.',
      });
    }
    const purchase = purchases.describe('sendmoney', { amount });
    if (!purchase.ok) {
      return sendJson(res, 400, {
        ok: false,
        message:
          purchase.error || 'The amount for this transfer is missing or invalid.',
      });
    }
    if (!flutterwave.isConfigured()) {
      return sendJson(res, 503, {
        ok: false,
        message:
          'Bank transfer is not configured: the backend needs FLW_SECRET_KEY.',
      });
    }

    // The uid stays in the reference so a later payout failure reported by the
    // webhook can find the wallet that has to be refunded.
    const clientRef = String(body.ref || Date.now()).replace(/[^A-Za-z0-9]/g, '');
    const reference = `trf-${user.uid}-${clientRef || Date.now()}`;
    const debit = await wallet.debit(user.uid, {
      amount: purchase.customerCharge,
      reference,
      description: purchase.label,
      txRef: clientRef,
    });
    if (debit.duplicate) {
      return sendJson(res, 409, {
        ok: false,
        message: 'This transfer was already submitted.',
      });
    }
    if (debit.insufficient) {
      return sendJson(res, 402, {
        ok: false,
        message:
          `Your wallet has ₦${Number(debit.balance || 0).toLocaleString('en-NG')} but this transfer costs ` +
          `₦${purchase.customerCharge.toLocaleString('en-NG')} ` +
          `(₦${purchase.providerAmount.toLocaleString('en-NG')} + ` +
          `₦${purchase.fee.toLocaleString('en-NG')} service charge). Fund your wallet and try again.`,
      });
    }

    try {
      const transfer = await flutterwave.createTransfer({
        bankCode,
        accountNumber: accountNo,
        amount: purchase.providerAmount,
        narration: String(body.sender || 'Nation Sub').slice(0, 100),
        reference,
      });
      const balance = await wallet.balance(user.uid);
      return sendJson(res, 200, {
        ok: true,
        id: transfer.id,
        status: transfer.status,
        reference: transfer.reference,
        accountName: transfer.accountName,
        bankName: transfer.bankName,
        amount: purchase.providerAmount,
        fee: purchase.fee,
        customerCharge: purchase.customerCharge,
        balance,
        message:
          `₦${purchase.providerAmount.toLocaleString('en-NG')} is on its way to ` +
          `${transfer.accountName || 'the recipient'}` +
          `${transfer.bankName ? ` (${transfer.bankName})` : ''}. ` +
          `Reference ${transfer.reference}.`,
      });
    } catch (error) {
      // The payout was refused, so the customer keeps their money.
      try {
        await wallet.credit(user.uid, {
          amount: purchase.customerCharge,
          reference: `${reference}-refund`,
          description: 'Refund: bank transfer',
          txRef: clientRef,
          source: 'refund',
        });
      } catch (refundError) {
        console.warn(
          `transfer refund FAILED for ${reference}: ${refundError.message}`,
        );
      }
      const status = error.code === 'unconfigured' ? 503 : 502;
      return sendJson(res, status, {
        ok: false,
        message: `${error.message} Nothing was taken from your wallet.`,
      });
    }
  }

  if (path === '/webhook/flutterwave') {
    // Public endpoint: authenticated by the verif-hash Flutterwave sends, not by
    // a Firebase token.
    if (req.method !== 'POST') {
      return sendJson(res, 405, { status: 'error', message: 'Method not allowed.' });
    }
    if (!flutterwave.verifyWebhookHash(req.headers['verif-hash'])) {
      return sendJson(res, 401, {
        status: 'rejected',
        message: 'Invalid verif-hash.',
      });
    }
    const body = await readBody(req);
    const data = (body && body.data) || {};

    // Payout events from /transfers. A transfer the bank rejects has to give
    // the customer their wallet money back: it was charged when submitted.
    if (String(body.event || '') === 'transfer.completed') {
      const reference = String(data.reference || '');
      const state = String(data.status || '').toUpperCase();
      if (state === 'SUCCESSFUL') {
        return sendJson(res, 200, { status: 'ok' });
      }
      const match = /^trf-([A-Za-z0-9]+)-([A-Za-z0-9]+)$/.exec(reference);
      if (!match) {
        console.warn(`flutterwave webhook: unknown transfer ${reference}`);
        return sendJson(res, 200, { status: 'ignored-reference' });
      }
      const purchase = purchases.describe('sendmoney', { amount: data.amount });
      if (!purchase.ok) {
        return sendJson(res, 200, { status: 'ignored-amount' });
      }
      const refund = await wallet.credit(match[1], {
        amount: purchase.customerCharge,
        reference: `${reference}-refund`,
        description: 'Refund: bank transfer',
        txRef: match[2],
        source: 'refund',
      });
      return sendJson(res, 200, {
        status: 'refunded',
        credited: refund.credited,
        balance: refund.balance,
      });
    }

    const txRef = String(data.tx_ref || '');
    if (!txRef || String(data.status || '') !== 'successful') {
      return sendJson(res, 200, { status: 'ignored' });
    }
    const topup = await wallet.getTopup(txRef);
    if (!topup) {
      console.warn(`flutterwave webhook: unknown reference ${txRef}`);
      return sendJson(res, 200, { status: 'unknown-reference' });
    }
    if (String(data.currency || 'NGN').toUpperCase() !== 'NGN') {
      return sendJson(res, 200, { status: 'ignored-currency' });
    }

    // Confirm with the provider so a forged body cannot mint wallet credit.
    if (data.id) {
      const verified = await flutterwave.verifyTransaction(data.id);
      if (verified && verified.status !== 'successful') {
        console.warn(
          `flutterwave webhook: transaction ${data.id} is ${verified.status}; not crediting ${txRef}`,
        );
        return sendJson(res, 200, { status: 'not-successful' });
      }
    }

    const amount = Number(data.amount);
    if (!Number.isFinite(amount) || amount <= 0) {
      return sendJson(res, 200, { status: 'ignored-amount' });
    }

    const creditAmount = creditForTopup(topup, amount);
    if (!(creditAmount > 0)) {
      return sendJson(res, 200, { status: 'ignored-amount' });
    }
    const result = await wallet.credit(topup.uid, {
      amount: creditAmount,
      reference: `flw-${data.id || txRef}`,
      description: 'Wallet top-up',
      txRef,
      flwRef: String(data.flw_ref || topup.flwRef || ''),
    });
    return sendJson(res, 200, {
      status: 'ok',
      credited: result.credited,
      balance: result.balance,
    });
  }

  if (path === '/cable-plans') {
    if (req.method !== 'GET') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const provider = (url.searchParams.get('provider') || '').toLowerCase();
    if (!CABLE_PROVIDERS.has(provider)) {
      return sendJson(res, 400, { ok: false, message: 'Unknown cable provider.' });
    }
    const catalogue = await fetchCablePlans(provider);
    if (!catalogue.ok) {
      return sendJson(res, 502, { ok: false, message: catalogue.message });
    }
    return sendJson(res, 200, {
      ok: true,
      source: 'provider',
      provider,
      plans: withCustomerPrices('cable', catalogue.plans),
      ...(catalogue.plans.length ? {} : { message: catalogue.message }),
    });
  }

  if (path === '/plans') {
    const network = (url.searchParams.get('network') || '').toLowerCase();
    if (network) {
      const plans = plansFor(network);
      if (!plans) {
        return sendJson(res, 404, {
          ok: false,
          message: `Unknown network "${network}".`,
        });
      }
      return sendJson(res, 200, {
        ok: true,
        network,
        source: 'backend',
        plans: withCustomerPrices('data', plans),
      });
    }
    return sendJson(res, 200, {
      ok: true,
      source: 'backend',
      networks: Object.keys(CATALOGUE).map((id) => ({
        id,
        label: CATALOGUE[id].label,
        plans: withCustomerPrices('data', plansFor(id)),
      })),
    });
  }

  if (path.startsWith('/vtu/')) {
    const service = path.slice('/vtu/'.length).replace(/\/+$/, '');
    if (!ALLOWED_SERVICES.has(service)) {
      return sendJson(res, 404, { ok: false, message: 'Unknown service.' });
    }
    const user = await requireUser(req);
    if (!user || user.error) {
      return sendJson(res, 401, {
        code: 401,
        description: {
          Status: 'Failed',
          message: user?.error === 'missing-token'
            ? 'No Firebase ID token was sent. Sign in before requesting this service.'
            : 'Firebase rejected this ID token. Sign out, sign in again, and retry.',
        },
      });
    }
    if (req.method !== 'GET' && req.method !== 'POST') {
      return sendJson(res, 405, { ok: false, message: 'Method not allowed.' });
    }
    const body = req.method === 'POST' ? await readBody(req) : {};
    const params = new URLSearchParams(url.searchParams);
    for (const [name, value] of Object.entries(body)) {
      if (value !== undefined && value !== null) params.set(name, String(value));
    }
    // The price is worked out here, never taken from the app, and the money
    // comes out of the customer's wallet instead of the owner's VTU float.
    let cablePlans = [];
    if (service === 'paytv') {
      const catalogue = await fetchCablePlans(
        String(params.get('service') || '').toLowerCase(),
      );
      cablePlans = catalogue.plans || [];
    }
    const purchase = purchases.describe(service, Object.fromEntries(params), {
      cablePlans,
    });
    if (purchase.error) {
      return sendJson(res, 400, {
        code: 400,
        description: { Status: 'Failed', message: purchase.error },
      });
    }

    let charged = null;
    if (purchase.ok) {
      const float = await providerFloat();
      if (float !== null && float < purchase.providerAmount) {
        return sendJson(res, 503, {
          code: 503,
          description: {
            Status: 'Failed',
            message:
              'The provider wallet is too low for this purchase. Top up the VTU Africa balance and try again. Nothing was charged.',
          },
        });
      }
      const reference = `buy-${params.get('ref') || `${user.uid}-${Date.now()}`}`;
      const debit = await wallet.debit(user.uid, {
        amount: purchase.customerCharge,
        reference,
        description: purchase.label,
        txRef: params.get('ref') || '',
      });
      if (debit.insufficient) {
        return sendJson(res, 402, {
          code: 402,
          description: {
            Status: 'Failed',
            message:
              `Your wallet has ₦${Number(debit.balance || 0).toLocaleString('en-NG')} but this purchase costs ` +
              `₦${purchase.customerCharge.toLocaleString('en-NG')} ` +
              `(₦${purchase.providerAmount.toLocaleString('en-NG')} + ` +
              `₦${purchase.fee.toLocaleString('en-NG')} service charge). Fund your wallet and try again.`,
          },
        });
      }
      if (!debit.debited) {
        return sendJson(res, 502, {
          code: 502,
          description: {
            Status: 'Failed',
            message: debit.duplicate
              ? 'This purchase reference was already used, so nothing new was charged.'
              : 'The wallet could not be charged, so nothing was sent.',
          },
        });
      }
      charged = { reference, purchase };
    }

    const result = await proxyToVtu(service, params);
    const succeeded =
      result.status === 200 && String(result.body && result.body.code) === '101';
    if (charged && !succeeded) {
      // The provider refused or could not be reached, so the customer keeps
      // their money: the charge is credited straight back.
      try {
        await wallet.credit(user.uid, {
          amount: charged.purchase.customerCharge,
          reference: `${charged.reference}-refund`,
          description: `Refund: ${charged.purchase.label}`,
          txRef: params.get('ref') || '',
          source: 'refund',
        });
        console.warn(`wallet refund issued for ${charged.reference}`);
      } catch (error) {
        console.warn(
          `wallet refund FAILED for ${charged.reference}: ${error.message}`,
        );
      }
    }
    return sendJson(res, result.status, result.body);
  }

  return sendJson(res, 404, { ok: false, message: 'Not found.' });
}

let wrapped;
try {
  const { onRequest } = require('firebase-functions/v2/https');
  wrapped = onRequest(
    { cors: true, region: 'us-central1', timeoutSeconds: 60 },
    handler,
  );
} catch (_) {
  // Running outside Firebase (local dev): export the bare handler.
  wrapped = handler;
}

// Firebase deploys every exported function, so `handler` (a bare node
// (req, res) function used by dev-server.js) must not be exported there.
const exported = { api: wrapped };
if (process.env.NATIONSB_DEV_SERVER === '1') exported.handler = handler;
module.exports = exported;
