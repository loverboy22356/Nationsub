'use strict';

// Customer prices.
//
// The app never adds a service charge itself: the backend adds it when it
// serves a catalogue or charges a purchase, so the price the customer sees is
// the provider price plus this app's fee.
//
//   price = providerPrice + feeFor(service)
//
// Change a number here and redeploy the functions; the app follows.
const FEES = {
  cable: 100,
  airtime: 30,
  electricity: 100,
  'exam-pin': 200,
  betpay: 20,
  sendmoney: 100,
  'airtime-pin': 100,
  'airtime-cash': 0,
  // CAC is not connected to a live provider, so it carries no charge yet.
  cac: 0,
};

// Data is charged by bundle size, not price: bundles from 20MB to 200MB carry
// ₦50, everything else carries ₦100.
const DATA_SMALL_BUNDLE = { fromMb: 20, toMb: 200, fee: 50 };
const DATA_FEE = 100;

// Kept out of the wallet balance: a ₦1,000 top-up credits ₦950.
const FUNDING_FEE = 50;

function money(value) {
  return Math.round(Number(value) * 100) / 100;
}

/// Reads a plan label such as "200MB", "1GB" or "10.8GB" and returns its size
/// in megabytes, or null when the label carries no size.
function sizeInMb(label) {
  const match = String(label || '').match(/([\d.]+)\s*(KB|MB|GB|TB)/i);
  if (!match) return null;
  const size = Number(match[1]);
  if (!Number.isFinite(size) || size <= 0) return null;
  const unit = match[2].toUpperCase();
  if (unit === 'KB') return size / 1024;
  if (unit === 'GB') return size * 1024;
  if (unit === 'TB') return size * 1024 * 1024;
  return size;
}

function dataFee(sizeMb) {
  const mb = Number(sizeMb);
  if (!Number.isFinite(mb) || mb <= 0) return DATA_FEE;
  return mb >= DATA_SMALL_BUNDLE.fromMb && mb <= DATA_SMALL_BUNDLE.toMb
    ? DATA_SMALL_BUNDLE.fee
    : DATA_FEE;
}

function feeFor(service, providerPrice, sizeMb) {
  const id = String(service || '').toLowerCase();
  if (id === 'data') return dataFee(sizeMb);
  const fee = FEES[id];
  return Number.isFinite(fee) && fee > 0 ? fee : 0;
}

/// Returns the provider price, the fee and the price the customer pays, or
/// null when the provider price is not usable.
function withFee(service, providerPrice, options = {}) {
  const base = Number(providerPrice);
  if (!Number.isFinite(base) || base <= 0) return null;
  const fee = feeFor(service, base, options.sizeMb);
  return {
    providerPrice: money(base),
    fee,
    price: money(base + fee),
  };
}

module.exports = {
  DATA_FEE,
  DATA_SMALL_BUNDLE,
  FEES,
  FUNDING_FEE,
  feeFor,
  sizeInMb,
  withFee,
};
