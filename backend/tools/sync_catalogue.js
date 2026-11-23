'use strict';

// Regenerates app/catalogue_data.py from the Firebase function's catalogue, so
// the Python backend and the Cloud Function always quote the same plan prices.
//
//   node tools/sync_catalogue.js

const fs = require('fs');
const path = require('path');

const source = path.join(__dirname, '..', '..', 'functions', 'catalogue.js');
const target = path.join(__dirname, '..', 'app', 'catalogue_data.py');

const { CATALOGUE, plansFor } = require(source);

const networks = Object.keys(CATALOGUE).map((id) => ({
  id,
  label: CATALOGUE[id].label,
  plans: plansFor(id),
}));

const header = [
  '"""Data bundle catalogue (generated).',
  '',
  'Produced by tools/sync_catalogue.js from functions/catalogue.js.',
  'Do not edit by hand: change the Firebase catalogue and run',
  '  node tools/sync_catalogue.js',
  '"""',
  '',
  '',
  'NETWORKS = ',
].join('\n');

fs.writeFileSync(target, `${header}${JSON.stringify(networks, null, 2)}\n`, 'utf8');
console.log(`wrote ${target} (${networks.length} networks)`);
