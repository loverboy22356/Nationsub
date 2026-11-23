'use strict';

// What a customer is charged for one purchase.
//
// A purchase spends the customer's own wallet (money collected through
// Flutterwave top-ups) and never the owner's VTU Africa float, so the backend
// must work out the price itself instead of trusting the app: the provider
// amount comes from the catalogue or the provider's own package list, and the
// service charge comes from pricing.js.
//
//   customerCharge = providerAmount + fee

const { CATALOGUE, plansFor } = require('./catalogue');
const pricing = require('./pricing');

// Gateway service -> the fee key used by pricing.js
const FEE_KEY = {
  airtime: 'airtime',
  data: 'data',
  paytv: 'cable',
  electric: 'electricity',
  betpay: 'betpay',
  sendmoney: 'sendmoney',
  'airtime-pin': 'airtime-pin',
};

/// service|plan-code -> catalogue plan, built once per isolate.
const planIndex = (() => {
  const index = new Map();
  for (const network of Object.keys(CATALOGUE)) {
    for (const plan of plansFor(network)) {
      index.set(`${plan.service}|${plan.code}`, plan);
    }
  }
  return index;
})();

function cataloguePlan(service, code) {
  const key = `${String(service || '').trim()}|${String(code || '').trim()}`;
  return planIndex.get(key) || null;
}

function positive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

/// Returns {skip:true} for services that do not spend the wallet (airtime to
/// cash, balance checks), {error} when the price cannot be established, or
/// {ok:true, providerAmount, fee, customerCharge, label} when it can.
function describe(service, params = {}, options = {}) {
  const feeKey = FEE_KEY[String(service || '').toLowerCase()];
  if (!feeKey) return { skip: true };

  let providerAmount = null;
  let label = '';
  let sizeMb = null;

  if (feeKey === 'data') {
    const plan = cataloguePlan(params.service, params.DataPlan);
    if (!plan) {
      return { error: 'That data plan is not available right now.' };
    }
    providerAmount = positive(plan.price);
    label = `${plan.label} data`;
    sizeMb = pricing.sizeInMb(plan.label);
  } else if (feeKey === 'cable') {
    const variation = String(params.variation || '').trim();
    const plans = options.cablePlans || [];
    const plan = plans.find((entry) => String(entry.code) === variation);
    if (!plan) {
      return { error: 'That cable package is not available right now.' };
    }
    providerAmount = positive(plan.providerPrice ?? plan.price);
    label = `${plan.label || 'Cable'} subscription`;
  } else if (feeKey === 'airtime-pin') {
    // VTU documents ₦100 and ₦200 denominations as variations 1 and 2.
    providerAmount = String(params.variation) === '2' ? 200 : 100;
    label = `₦${providerAmount} recharge PIN`;
  } else if (feeKey === 'sendmoney') {
    providerAmount = positive(params.amount);
    label = 'Bank transfer';
  } else if (feeKey === 'electricity') {
    providerAmount = positive(params.amount);
    label = 'Electricity token';
  } else if (feeKey === 'betpay') {
    providerAmount = positive(params.amount);
    label = 'Bet account funding';
  } else {
    providerAmount = positive(params.amount);
    label = 'Airtime';
  }

  if (!providerAmount) {
    return { error: 'The amount for this purchase is missing or invalid.' };
  }

  const fee = pricing.feeFor(feeKey, providerAmount, sizeMb);
  return {
    ok: true,
    providerAmount: Math.round(providerAmount * 100) / 100,
    fee,
    customerCharge: Math.round((providerAmount + fee) * 100) / 100,
    label,
  };
}

module.exports = { describe, cataloguePlan };
