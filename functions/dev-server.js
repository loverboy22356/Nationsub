'use strict';

// Local runner for the Nation Sub backend, so the app can be tested on Windows
// (or any device) before the functions are deployed.
//
//   VTU_API_KEY=... NATIONSB_ALLOW_ANON=1 node dev-server.js
//
// NATIONSB_ALLOW_ANON only exists for local testing; never set it in Firebase.

const fs = require('fs');
const path = require('path');
const http = require('http');

// Without Admin credentials, local reads use the signed-in user's ID token
// through Firestore REST and obey Firestore Security Rules. With a local
// service-account key, use the same Admin SDK path as the deployed function.
const hasAdminCredentials =
  fs.existsSync(path.join(__dirname, 'serviceAccountKey.json')) ||
  Boolean(process.env.GOOGLE_APPLICATION_CREDENTIALS);
if (!hasAdminCredentials) {
  process.env.NATIONSB_LOCAL_FIRESTORE_REST = '1';
} else {
  delete process.env.NATIONSB_LOCAL_FIRESTORE_REST;
}

if (process.env.NATIONSB_LOCAL_FIRESTORE_REST === '1') {
  const ledger = process.env.NATIONSB_WALLET_FILE || path.join(__dirname, '.local-wallet.json');
  console.warn('No Firebase Admin credential: wallet balances come from the local dev ledger.');
  console.warn(`Local ledger file: ${ledger}`);
  console.warn('Add functions/serviceAccountKey.json to switch back to Firestore.');
}

// Tells index.js to also export the bare node handler (never exported in
// Firebase, where every export would be deployed as a function).
process.env.NATIONSB_DEV_SERVER = '1';

const { handler } = require('./index');

// NATIONSB_PORT, not PORT: build runners often inject PORT themselves.
const port = Number(process.env.NATIONSB_PORT || 8787);

http
  .createServer((req, res) => {
    handler(req, res).catch((error) => {
      res.statusCode = 500;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ ok: false, message: String(error) }));
    });
  })
  .listen(port, '127.0.0.1', () => {
    console.log(`Nation Sub backend on http://127.0.0.1:${port}`);
    console.log(`VTU_API_KEY: ${process.env.VTU_API_KEY ? 'set' : 'missing'}`);
  });
