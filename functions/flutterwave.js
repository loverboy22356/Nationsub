'use strict';

// Small Flutterwave v3 client for wallet funding.
//
// A dynamic virtual account is created per top-up: Flutterwave issues an
// account number that is tied to that amount and reference, the customer
// transfers to it, and the webhook tells us the money arrived.
//
// Docs: https://developer.flutterwave.com/docs/virtual-accounts/ngn

const crypto = require('crypto');

const API_BASE = process.env.FLW_API_BASE || 'https://api.flutterwave.com/v3';
const REQUEST_TIMEOUT_MS = 30000;

function secretKey() {
  return process.env.FLW_SECRET_KEY || '';
}

function isConfigured() {
  return secretKey().length > 0;
}

async function request(method, path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${secretKey()}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch (_) {
      payload = { message: text };
    }
    return { status: response.status, payload };
  } catch (error) {
    return { status: 0, payload: { status: 'error', message: error.message } };
  } finally {
    clearTimeout(timer);
  }
}

function failureMessage(result) {
  const payload = result.payload || {};
  const data = payload.data;
  const detail =
    (typeof data === 'string' && data) ||
    (data && typeof data === 'object' && (data.message || data.narration)) ||
    payload.message ||
    `Flutterwave returned HTTP ${result.status}.`;
  return typeof detail === 'string' ? detail : JSON.stringify(detail);
}

/// Creates a one-amount virtual account. Flutterwave only issues static
/// (permanent) NGN accounts with the customer's BVN, which an app cannot
/// collect, so top-ups use a dynamic account per request.
async function createVirtualAccount({ email, amount, txRef, phone, name }) {
  if (!isConfigured()) {
    const error = new Error('FLW_SECRET_KEY is not configured on the backend.');
    error.code = 'unconfigured';
    throw error;
  }
  const body = {
    email,
    amount,
    currency: 'NGN',
    tx_ref: txRef,
    is_permanent: false,
  };
  if (phone) body.phonenumber = phone;
  if (name) body.narration = name;

  const result = await request('POST', '/virtual-account-numbers', body);
  const payload = result.payload || {};
  const data = payload.data || {};
  if (payload.status !== 'success' || !data.account_number) {
    const error = new Error(failureMessage(result));
    error.code = 'provider';
    error.providerStatus = result.status;
    throw error;
  }
  return {
    accountNumber: String(data.account_number),
    bankName: String(data.bank_name || data.account_bank || 'Flutterwave MFB'),
    accountName: String(data.narration || name || ''),
    flwRef: String(data.flw_ref || ''),
    orderRef: String(data.order_ref || ''),
  };
}

/// Server-side re-check of a transaction, so a forged webhook body cannot
/// credit a wallet Flutterwave never paid.
async function verifyTransaction(id) {
  if (!isConfigured()) return null;
  const result = await request(
    'GET',
    `/transactions/${encodeURIComponent(id)}/verify`,
  );
  const payload = result.payload || {};
  if (payload.status !== 'success') return null;
  const data = payload.data || {};
  return {
    status: String(data.status || '').toLowerCase(),
    amount: Number(data.amount || 0),
    currency: String(data.currency || ''),
    txRef: String(data.tx_ref || ''),
  };
}

/// Looks a payment up by the reference we created the account with. This is
/// what lets a wallet credit itself without waiting for a webhook, which a
/// backend running on localhost can never receive.
async function verifyByReference(txRef) {
  if (!isConfigured() || !txRef) return null;
  const result = await request(
    'GET',
    `/transactions/verify_by_reference?tx_ref=${encodeURIComponent(txRef)}`,
  );
  const payload = result.payload || {};
  if (payload.status !== 'success') return null;
  const raw = payload.data;
  const data = Array.isArray(raw) ? raw[0] : raw;
  if (!data || typeof data !== 'object') return null;
  return {
    id: data.id ? String(data.id) : '',
    status: String(data.status || '').toLowerCase(),
    amount: Number(data.amount || 0),
    currency: String(data.currency || ''),
    flwRef: String(data.flw_ref || ''),
    paymentType: String(data.payment_type || ''),
  };
}

/// Confirms the account holder before money is sent. Flutterwave refuses a
/// transfer to an account it cannot resolve, so the app always asks first.
async function resolveAccount({ accountNumber, bankCode }) {
  if (!isConfigured()) {
    const error = new Error('FLW_SECRET_KEY is not configured on the backend.');
    error.code = 'unconfigured';
    throw error;
  }
  const result = await request('POST', '/accounts/resolve', {
    account_number: String(accountNumber),
    account_bank: String(bankCode),
  });
  const payload = result.payload || {};
  const data = payload.data || {};
  if (payload.status !== 'success' || !data.account_name) {
    const error = new Error(failureMessage(result));
    error.code = 'provider';
    error.providerStatus = result.status;
    throw error;
  }
  return {
    accountName: String(data.account_name),
    accountNumber: String(data.account_number || accountNumber),
  };
}

/// Pays a Nigerian bank account out of the owner's Flutterwave balance. The
/// customer's wallet is charged by this backend; Flutterwave moves the money,
/// so a transfer never spends the VTU Africa float.
async function createTransfer({
  bankCode,
  accountNumber,
  amount,
  narration,
  reference,
  callbackUrl,
}) {
  if (!isConfigured()) {
    const error = new Error('FLW_SECRET_KEY is not configured on the backend.');
    error.code = 'unconfigured';
    throw error;
  }
  const body = {
    account_bank: String(bankCode),
    account_number: String(accountNumber),
    amount: Number(amount),
    currency: 'NGN',
    debit_currency: 'NGN',
    narration: narration || 'Nation Sub transfer',
    reference: String(reference),
  };
  if (callbackUrl) body.callback_url = callbackUrl;

  const result = await request('POST', '/transfers', body);
  const payload = result.payload || {};
  const data = payload.data || {};
  if (payload.status !== 'success' || !data.id) {
    const error = new Error(failureMessage(result));
    error.code = 'provider';
    error.providerStatus = result.status;
    throw error;
  }
  return {
    id: String(data.id),
    status: String(data.status || 'NEW'),
    reference: String(data.reference || reference),
    accountName: String(data.full_name || ''),
    bankName: String(data.bank_name || ''),
    fee: Number(data.fee || 0),
  };
}

/// Flutterwave sends the configured secret hash in `verif-hash`; anything else
/// is not from them.
function verifyWebhookHash(header) {
  const expected = process.env.FLW_SECRET_HASH || '';
  if (!expected || !header) return false;
  const received = Buffer.from(String(header));
  const secret = Buffer.from(expected);
  if (received.length !== secret.length) return false;
  return crypto.timingSafeEqual(received, secret);
}

module.exports = {
  isConfigured,
  createTransfer,
  createVirtualAccount,
  resolveAccount,
  verifyByReference,
  verifyTransaction,
  verifyWebhookHash,
};
