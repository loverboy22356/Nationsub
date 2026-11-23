'use strict';

// Active data bundles on VTU Africa's published API price table.
//
// VTU exposes no plan-listing endpoint (every variation lookup answers
// code 102 "No active variations found"), so the catalogue is owned here and
// served to the app by the backend. Keep `code` and `service` exactly as VTU
// documents them; `price` is the Portal Owners column and must match the tier
// of the VTU account behind VTU_API_KEY.
//
// Update this file when VTU changes its plan table, then redeploy functions.

const CATALOGUE = {
  mtn: {
    label: 'MTN',
    service: 'MTNSME',
    plans: [
      { code: '500W', label: '500MB', price: 330, validity: '7-Days' },
      { code: '5000W', label: '5GB', price: 1840, validity: '7-Days' },
      { code: '6000W', label: '6GB', price: 2460, validity: '7-Days' },
      { code: '1000', label: '1GB', price: 770, validity: '30-Days' },
      { code: '2000', label: '2GB', price: 1430, validity: '30-Days' },
      { code: '3000', label: '3GB', price: 1770, validity: '30-Days' },
      { code: '10000', label: '10GB', price: 4470, validity: '30-Days' },
    ],
  },
  airtel: {
    label: 'Airtel',
    service: 'AIRTELSME',
    plans: [
      { code: '150', label: '150MB', price: 65, validity: '1-Day' },
      { code: '300', label: '300MB', price: 114, validity: '2-Days' },
      { code: '600', label: '600MB', price: 220, validity: '2-Days' },
      { code: '1000D', label: '1GB', price: 358, validity: '1-Day' },
      { code: '3000W', label: '3GB', price: 1070, validity: '7-Days' },
      { code: '7000W', label: '7GB', price: 2035, validity: '7-Days' },
      { code: '4000', label: '4GB', price: 2450, validity: '30-Days' },
      { code: '10000', label: '10GB', price: 3100, validity: '30-Days' },
      { code: '13000', label: '13GB', price: 4925, validity: '30-Days' },
    ],
  },
  glo: {
    label: 'Glo',
    service: 'GLOSME',
    plans: [
      { code: '50', label: '50MB', price: 52, validity: '1-Day' },
      { code: '125', label: '125MB', price: 98, validity: '1-Day' },
      { code: '260', label: '260MB', price: 192, validity: '2-Days' },
      { code: '350', label: '350MB', price: 100, validity: '1-Day' },
      { code: '750N', label: '750MB', price: 119, validity: '1-Night Plan' },
      { code: '750', label: '750MB', price: 205, validity: '1-Day' },
      { code: '1250D', label: '1.25GB', price: 200, validity: '1 Sunday Plan' },
      { code: '1500D', label: '1.5GB', price: 300, validity: '1-Day' },
      { code: '2500D', label: '2.5GB', price: 500, validity: '2-Days' },
      { code: '10000W', label: '10GB', price: 2000, validity: '7-Days' },
    ],
  },
  '9mobile': {
    label: '9mobile',
    service: '9MOBILESME',
    plans: [
      { code: '250', label: '250MB', price: 81, validity: '14-Days' },
      { code: '500', label: '500MB', price: 135, validity: '30-Days' },
      { code: '3500', label: '3.5GB', price: 905, validity: '30-Days' },
      { code: '7000', label: '7GB', price: 1750, validity: '30-Days' },
      { code: '15000', label: '15GB', price: 3100, validity: '30-Days' },
    ],
  },
};

// Additional active plan types from VTU Africa's documented data API tables
// (Portal Owner prices). Disabled variations are intentionally excluded.
const EXTRA_PLANS = {
  mtn: [
    { code: '40', label: '40MB', price: 52, validity: '1-Day', service: 'MTNGIFT', category: 'Gifting' },
    { code: '75', label: '75MB', price: 76, validity: '1-Day', service: 'MTNGIFT', category: 'Gifting' },
    { code: '500', label: '500MB', price: 490, validity: '7-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '750', label: '750MB', price: 440, validity: '3-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '1000D', label: '1GB', price: 490, validity: '1-Day', service: 'MTNGIFT', category: 'Gifting' },
    { code: '2000D', label: '2GB', price: 735, validity: '2-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '2501D', label: '2.5GB', price: 735, validity: '1-Day', service: 'MTNGIFT', category: 'Gifting' },
    { code: '2500D', label: '2.5GB', price: 880, validity: '2-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '3200D', label: '3.2GB', price: 980, validity: '2-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '1000W', label: '1GB', price: 780, validity: '7-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '1500W', label: '1.5GB', price: 975, validity: '7-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '6000W', label: '6GB', price: 2415, validity: '7-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '2000', label: '2GB', price: 1465, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '2700', label: '2.7GB', price: 1950, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '3500', label: '3.5GB', price: 2425, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '10000', label: '10GB', price: 4375, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '12500', label: '12.5GB', price: 5430, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '5000', label: '5GB', price: 2580, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '7000', label: '7GB', price: 3445, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '16500', label: '16.5GB', price: 6355, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '20000', label: '20GB', price: 7500, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '25000', label: '25GB', price: 8900, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '36000', label: '36GB', price: 10800, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '75000', label: '75GB', price: 17470, validity: '30-Days', service: 'MTNGIFT', category: 'Gifting' },
    { code: '1000D', label: '1GB', price: 490, validity: '1-Day', service: 'MTNAWOOF', category: 'Awoof' },
    { code: '1400W', label: '1.4GB', price: 1756, validity: '3-Days', service: 'MTNAWOOF', category: 'Awoof' },
    { code: '20000W', label: '20GB', price: 9825, validity: '7-Days', service: 'MTNAWOOF', category: 'Awoof' },
  ],
  airtel: [
    { code: '75', label: '75MB', price: 79.9, validity: '1-Day', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '200', label: '200MB', price: 206, validity: '3-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '500', label: '500MB', price: 496, validity: '3-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '1000D', label: '1GB', price: 495, validity: '1-Day', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '1500D', label: '1.5GB', price: 595, validity: '2-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '3000D', label: '3GB', price: 990, validity: '2-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '1000W', label: '1GB', price: 790, validity: '7-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '1500W', label: '1.5GB', price: 995, validity: '7-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '6000W', label: '6GB', price: 2493, validity: '7-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '2000', label: '2GB', price: 1485, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '3000', label: '3GB', price: 1980, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '4000', label: '4GB', price: 2502, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '8000', label: '8GB', price: 2993, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '10000', label: '10GB', price: 3990, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '13000', label: '13GB', price: 4973, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '18000', label: '18GB', price: 6000, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '25000', label: '25GB', price: 8055, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '35000', label: '35GB', price: 10000, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '60000', label: '60GB', price: 15275, validity: '30-Days', service: 'AIRTELGIFT', category: 'Gifting' },
    { code: '100', label: '100MB', price: 105, validity: '7-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '300', label: '300MB', price: 270, validity: '7-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '500', label: '500MB', price: 490, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '1000', label: '1GB', price: 980, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '2000', label: '2GB', price: 1960, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '5000', label: '5GB', price: 4900, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '10000', label: '10GB', price: 9800, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '15000', label: '15GB', price: 14700, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '20000', label: '20GB', price: 19600, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
    { code: '250000', label: '250GB', price: 250000, validity: '30-Days', service: 'AIRTELCG', category: 'Corporate' },
  ],
  glo: [
    { code: '50', label: '50MB', price: 51, validity: '1-Day', service: 'GLOGIFT', category: 'Gifting' },
    { code: '150', label: '150MB', price: 97, validity: '1-Day', service: 'GLOGIFT', category: 'Gifting' },
    { code: '350', label: '350MB', price: 191, validity: '1-Day', service: 'GLOGIFT', category: 'Gifting' },
    { code: '1000W', label: '1GB', price: 470, validity: '14-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '3900', label: '3.9GB', price: 945, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '7500', label: '7.5GB', price: 2380, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '9000', label: '9.2GB', price: 1905, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '10000', label: '10.8GB', price: 2865, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '14000', label: '14GB', price: 3830, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '18000', label: '18GB', price: 4760, validity: '30-Days', service: 'GLOGIFT', category: 'Gifting' },
    { code: '200', label: '200MB', price: 90, validity: '14-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '500', label: '500MB', price: 210, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '1000', label: '1GB', price: 408, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '2000', label: '2GB', price: 816, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '3000', label: '3GB', price: 1225, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '5000', label: '5GB', price: 2040, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
    { code: '10000', label: '10GB', price: 4050, validity: '30-Days', service: 'GLOCG', category: 'Corporate' },
  ],
  '9mobile': [
    { code: '25', label: '25MB', price: 87, validity: '1-Day', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '2000D', label: '2GB', price: 850, validity: '1-Day', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '100', label: '100MB', price: 187, validity: '7-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '250', label: '250MB', price: 160, validity: '14-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '350', label: '350MB', price: 594, validity: '7-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '1500W', label: '1.5GB', price: 705, validity: '7-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '7000W', label: '7GB', price: 2510, validity: '7-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '500', label: '500MB', price: 320, validity: '14-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '5000W', label: '5GB', price: 2350, validity: '14-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '1500', label: '1.5GB', price: 1660, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '2000', label: '2GB', price: 1984, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '3000', label: '3GB', price: 2480, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '4500', label: '4.5GB', price: 3320, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '5500', label: '5.5GB', price: 6840, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '11000', label: '11GB', price: 6630, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '15000', label: '15GB', price: 8300, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '25000', label: '25GB', price: 17820, validity: '30-Days', service: '9MOBILEGIFT', category: 'Gifting' },
    { code: '500', label: '500MB', price: 147, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '1000', label: '1GB', price: 285, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '1500', label: '1.5GB', price: 435, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '2000', label: '2GB', price: 570, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '3000', label: '3GB', price: 855, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '4000', label: '4GB', price: 1140, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '4500', label: '4.5GB', price: 1283, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '5000', label: '5GB', price: 1425, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '10000', label: '10GB', price: 2850, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '11000', label: '11GB', price: 4125, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '15000', label: '15GB', price: 4275, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '20000', label: '20GB', price: 5700, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '25000', label: '25GB', price: 7125, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '30000', label: '30GB', price: 8550, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
    { code: '40000', label: '40GB', price: 11350, validity: '30-Days', service: '9MOBILECG', category: 'Corporate' },
  ],
};

function plansFor(network) {
  const id = String(network || '').toLowerCase();
  const entry = CATALOGUE[id];
  if (!entry) return null;
  const standard = entry.plans.map((plan) => ({ ...plan, category: 'SME' }));
  return [...standard, ...(EXTRA_PLANS[id] || [])].map((plan) => ({
    ...plan,
    network: id,
    service: plan.service || entry.service,
  }));
}

module.exports = { CATALOGUE, plansFor };
