"""The durable record. Every mail ever classified lives here, so re-runs are
incremental and a narrower fetch window never discards earlier decisions."""
import json
import os

from .rules import ACK, INTERVIEW

# Statuses renamed since earlier versions, mapped on load.
LEGACY_STATUS = {"Pass to next round": INTERVIEW, "Acknowledgement": ACK}


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        store = json.load(fh)
    for row in store.values():
        row["status"] = LEGACY_STATUS.get(row["status"], row["status"])
    return store


def save(path, store):
    """Write the store, keeping the previous copy alongside it.

    Everything else in data/ is regenerated, so this one file is the only thing
    a mistake can destroy - and losing it is expensive rather than merely
    annoying: every agent verdict in it has to be earned again, which on the API
    tier means paying for the same mail twice. The backup costs a file copy.
    """
    if os.path.exists(path):
        os.replace(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, ensure_ascii=False)
