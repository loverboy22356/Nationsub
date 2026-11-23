"""Data bundle catalogue served to the app.

VTU exposes no plan-listing endpoint, so the catalogue is owned here. The plan
rows live in catalogue_data.py, which tools/sync_catalogue.js regenerates from
the Firebase function's functions/catalogue.js so both backends quote the same
prices.

Update prices there, run `node tools/sync_catalogue.js`, then redeploy.
"""

from __future__ import annotations

from .catalogue_data import NETWORKS

CATALOGUE = {
    entry["id"]: {"label": entry["label"], "plans": entry["plans"]} for entry in NETWORKS
}


def plans_for(network):
    entry = CATALOGUE.get(str(network or "").lower())
    if not entry:
        return None
    return [dict(plan) for plan in entry["plans"]]


def network_ids():
    return [entry["id"] for entry in NETWORKS]
