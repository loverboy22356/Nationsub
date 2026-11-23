"""Bundled snapshot of the data bundle catalogue.

Plans are normally read live from VTU's published price table by
provider_catalogue.py. This snapshot is the fallback for the minutes when that
page cannot be reached, so the app still shows a catalogue instead of an error.

The rows live in catalogue_data.py, which tools/sync_catalogue.js regenerates
from the Firebase function's functions/catalogue.js so both backends agree.

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
