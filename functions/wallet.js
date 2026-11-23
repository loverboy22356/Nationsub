'use strict';

// Wallet ledger for top-ups.
//
// In Firebase the balance and its entries live in Firestore:
//   wallets/{uid}                       { balance, currency, updatedAt }
//   wallets/{uid}/transactions/{ref}    { amount, source, txRef, createdAt }
//   topups/{txRef}                      { uid, amount, accountNumber, ... }
//
// Local development has no Firebase credentials, so with NATIONSB_ALLOW_ANON=1,
// an explicit NATIONSB_WALLET_FILE, or the no-Admin REST mode set by
// dev-server.js, the same API is served from a JSON file next to this module.
// Those switches only ever exist on a developer machine: the deployed function
// always uses Firestore, which stays the real wallet.

const fs = require('fs');
const path = require('path');

const CURRENCY = 'NGN';

function decodeFirestoreValue(value) {
  if (!value || typeof value !== 'object') return value;
  if (Object.prototype.hasOwnProperty.call(value, 'stringValue')) return value.stringValue;
  if (Object.prototype.hasOwnProperty.call(value, 'integerValue')) return Number(value.integerValue);
  if (Object.prototype.hasOwnProperty.call(value, 'doubleValue')) return Number(value.doubleValue);
  if (Object.prototype.hasOwnProperty.call(value, 'booleanValue')) return value.booleanValue;
  if (Object.prototype.hasOwnProperty.call(value, 'timestampValue')) return value.timestampValue;
  if (Object.prototype.hasOwnProperty.call(value, 'nullValue')) return null;
  if (Object.prototype.hasOwnProperty.call(value, 'mapValue')) {
    return decodeFirestoreFields(value.mapValue.fields || {});
  }
  if (Object.prototype.hasOwnProperty.call(value, 'arrayValue')) {
    return (value.arrayValue.values || []).map(decodeFirestoreValue);
  }
  return value;
}

function decodeFirestoreFields(fields = {}) {
  return Object.fromEntries(
    Object.entries(fields).map(([key, value]) => [key, decodeFirestoreValue(value)]),
  );
}

async function firestoreRest(path, userToken) {
  const projectId =
    process.env.GCLOUD_PROJECT || process.env.GCP_PROJECT || 'nationsub-84906';
  const response = await fetch(
    `https://firestore.googleapis.com/v1/projects/${projectId}/databases/(default)/documents/${path}`,
    { headers: { Authorization: `Bearer ${userToken}`, Accept: 'application/json' } },
  );
  const text = await response.text();
  let body = {};
  try { body = text ? JSON.parse(text) : {}; } catch (_) {}
  if (!response.ok) {
    const detail = body.error?.message || `Firestore returned HTTP ${response.status}.`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return body;
}

function useFileStore() {
  return (
    Boolean(process.env.NATIONSB_WALLET_FILE) ||
    process.env.NATIONSB_ALLOW_ANON === '1' ||
    process.env.NATIONSB_LOCAL_FIRESTORE_REST === '1'
  );
}

function money(value) {
  return Math.round(Number(value) * 100) / 100;
}

// ---------------------------------------------------------------- Firestore

let database = null;

function firestore() {
  if (database) return database;
  const firebaseAdmin = require('firebase-admin');
  if (!firebaseAdmin.apps.length) firebaseAdmin.initializeApp();
  database = firebaseAdmin.firestore();
  return database;
}

function serverTime() {
  return require('firebase-admin').firestore.FieldValue.serverTimestamp();
}

// --------------------------------------------------------------- JSON files

let queue = Promise.resolve();

function fileName() {
  return (
    process.env.NATIONSB_WALLET_FILE ||
    path.join(__dirname, '.local-wallet.json')
  );
}

function readFile() {
  try {
    const raw = fs.readFileSync(fileName(), 'utf8');
    const parsed = JSON.parse(raw);
    return {
      wallets: parsed.wallets || {},
      entries: parsed.entries || {},
      topups: parsed.topups || {},
    };
  } catch (_) {
    return { wallets: {}, entries: {}, topups: {} };
  }
}

function writeFile(data) {
  fs.writeFileSync(fileName(), JSON.stringify(data, null, 2), 'utf8');
}

/// Serialises read-modify-write cycles on the JSON file.
function withFile(work) {
  const run = queue.then(work);
  queue = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

// --------------------------------------------------------------------- API

/// Records the virtual account issued for a top-up so the webhook can find the
/// user it belongs to from the reference alone.
async function saveTopup(txRef, record) {
  const payload = { ...record, txRef, currency: CURRENCY, status: 'pending' };
  if (useFileStore()) {
    return withFile(() => {
      const data = readFile();
      data.topups[txRef] = { ...payload, createdAt: new Date().toISOString() };
      writeFile(data);
    });
  }
  await firestore()
    .collection('topups')
    .doc(txRef)
    .set({ ...payload, createdAt: serverTime() });
}

async function getTopup(txRef) {
  if (!txRef) return null;
  if (useFileStore()) {
    const data = readFile();
    return data.topups[txRef] || null;
  }
  const snapshot = await firestore().collection('topups').doc(txRef).get();
  return snapshot.exists ? snapshot.data() : null;
}

async function balance(uid, userToken) {
  if (useFileStore()) {
    const data = readFile();
    return money((data.wallets[uid] || {}).balance || 0);
  }
  if (process.env.NATIONSB_LOCAL_FIRESTORE_REST === '1' && userToken) {
    try {
      const document = await firestoreRest(`wallets/${encodeURIComponent(uid)}`, userToken);
      return money(decodeFirestoreFields(document.fields).balance || 0);
    } catch (error) {
      if (error.status === 404) return 0;
      throw error;
    }
  }
  const snapshot = await firestore().collection('wallets').doc(uid).get();
  return snapshot.exists ? money(snapshot.get('balance') || 0) : 0;
}

/// Adds money to a wallet once per reference; repeated webhook deliveries for
/// the same payment return the existing balance and change nothing.
async function credit(uid, options) {
  const amount = money(options.amount);
  if (!uid) throw new Error('A wallet credit needs a user.');
  if (!(amount > 0)) throw new Error('A wallet credit needs a positive amount.');
  const reference = String(options.reference || `credit-${Date.now()}`);
  const entry = {
    amount,
    currency: CURRENCY,
    source: options.source || 'flutterwave',
    description: options.description || 'Wallet top-up',
    txRef: String(options.txRef || ''),
    flwRef: String(options.flwRef || ''),
    status: 'success',
  };

  if (useFileStore()) {
    return withFile(() => {
      const data = readFile();
      const key = `${uid}/${reference}`;
      const current = money((data.wallets[uid] || {}).balance || 0);
      if (data.entries[key]) {
        return { credited: false, balance: current };
      }
      const next = money(current + amount);
      data.wallets[uid] = { balance: next, currency: CURRENCY, updatedAt: new Date().toISOString() };
      data.entries[key] = { ...entry, uid, createdAt: new Date().toISOString() };
      writeFile(data);
      return { credited: true, balance: next };
    });
  }

  const store = firestore();
  return store.runTransaction(async (transaction) => {
    const walletRef = store.collection('wallets').doc(uid);
    const entryRef = walletRef.collection('transactions').doc(reference);
    const existing = await transaction.get(entryRef);
    const wallet = await transaction.get(walletRef);
    const current = wallet.exists ? money(wallet.get('balance') || 0) : 0;
    if (existing.exists) {
      return { credited: false, balance: current };
    }
    const next = money(current + amount);
    transaction.set(
      walletRef,
      { balance: next, currency: CURRENCY, updatedAt: serverTime() },
      { merge: true },
    );
    transaction.set(entryRef, { ...entry, createdAt: serverTime() });
    return { credited: true, balance: next };
  });
}

/// Takes a purchase out of a wallet once per reference. It refuses, and
/// changes nothing, when the balance cannot cover the amount.
async function debit(uid, options) {
  const amount = money(options.amount);
  if (!uid) throw new Error('A wallet debit needs a user.');
  if (!(amount > 0)) throw new Error('A wallet debit needs a positive amount.');
  const reference = String(options.reference || `debit-${Date.now()}`);
  const entry = {
    amount: -amount,
    currency: CURRENCY,
    source: options.source || 'purchase',
    description: options.description || 'Purchase',
    txRef: String(options.txRef || ''),
    status: 'success',
  };

  if (useFileStore()) {
    return withFile(() => {
      const data = readFile();
      const key = `${uid}/${reference}`;
      const current = money((data.wallets[uid] || {}).balance || 0);
      if (data.entries[key]) {
        return { debited: false, duplicate: true, balance: current };
      }
      if (current < amount) {
        return { debited: false, insufficient: true, balance: current };
      }
      const next = money(current - amount);
      data.wallets[uid] = {
        balance: next,
        currency: CURRENCY,
        updatedAt: new Date().toISOString(),
      };
      data.entries[key] = { ...entry, uid, createdAt: new Date().toISOString() };
      writeFile(data);
      return { debited: true, balance: next };
    });
  }

  const store = firestore();
  return store.runTransaction(async (transaction) => {
    const walletRef = store.collection('wallets').doc(uid);
    const entryRef = walletRef.collection('transactions').doc(reference);
    const existing = await transaction.get(entryRef);
    const wallet = await transaction.get(walletRef);
    const current = wallet.exists ? money(wallet.get('balance') || 0) : 0;
    if (existing.exists) {
      return { debited: false, duplicate: true, balance: current };
    }
    if (current < amount) {
      return { debited: false, insufficient: true, balance: current };
    }
    const next = money(current - amount);
    transaction.set(
      walletRef,
      { balance: next, currency: CURRENCY, updatedAt: serverTime() },
      { merge: true },
    );
    transaction.set(entryRef, { ...entry, createdAt: serverTime() });
    return { debited: true, balance: next };
  });
}

function toDate(value) {
  if (!value) return null;
  if (value instanceof Date) return value;
  if (typeof value.toDate === 'function') return value.toDate();
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/// Top-ups that have not been credited yet, newest first, so a wallet read can
/// ask Flutterwave whether any of them was actually paid.
async function pendingTopups(uid, limit = 5) {
  if (useFileStore()) {
    const data = readFile();
    return Object.values(data.topups)
      .filter((topup) => topup.uid === uid && topup.status !== 'paid')
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
      .slice(0, limit);
  }
  const snapshot = await firestore()
    .collection('topups')
    .where('uid', '==', uid)
    .limit(50)
    .get();
  return snapshot.docs
    .map((doc) => ({ ...doc.data(), txRef: doc.id }))
    .filter((topup) => topup.status !== 'paid')
    .sort(
      (a, b) =>
        (toDate(b.createdAt)?.getTime() || 0) -
        (toDate(a.createdAt)?.getTime() || 0),
    )
    .slice(0, limit);
}

async function markTopupPaid(txRef, patch = {}) {
  if (useFileStore()) {
    return withFile(() => {
      const data = readFile();
      const topup = data.topups[txRef];
      if (!topup) return;
      data.topups[txRef] = {
        ...topup,
        ...patch,
        status: 'paid',
        paidAt: new Date().toISOString(),
      };
      writeFile(data);
    });
  }
  await firestore()
    .collection('topups')
    .doc(txRef)
    .set({ ...patch, status: 'paid', paidAt: serverTime() }, { merge: true });
}

async function recentEntries(uid, limit = 10, userToken) {
  if (useFileStore()) {
    const data = readFile();
    return Object.entries(data.entries)
      .filter(([key]) => key.startsWith(`${uid}/`))
      .map(([, value]) => value)
      .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
      .slice(0, limit);
  }
  if (process.env.NATIONSB_LOCAL_FIRESTORE_REST === '1' && userToken) {
    const result = await firestoreRest(
      `wallets/${encodeURIComponent(uid)}/transactions?pageSize=${limit}&orderBy=createdAt%20desc`,
      userToken,
    );
    return (result.documents || []).map((document) =>
      decodeFirestoreFields(document.fields),
    );
  }
  const snapshot = await firestore()
    .collection('wallets')
    .doc(uid)
    .collection('transactions')
    .orderBy('createdAt', 'desc')
    .limit(limit)
    .get();
  return snapshot.docs.map((doc) => doc.data());
}

module.exports = {
  CURRENCY,
  balance,
  credit,
  debit,
  getTopup,
  markTopupPaid,
  pendingTopups,
  recentEntries,
  saveTopup,
};
