"""Synthetic mailbox, so the tool can be tried without connecting an account.

The messages are the real failure modes this classifier was built against — a
negated rejection, a receipt that describes the pipeline, a layoff letter, an
account-verification notice — with every name invented.
"""
import datetime as dt
import json
import os

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "tests", "fixtures", "emails.json")


def load_fixtures():
    with open(FIXTURES, "r", encoding="utf-8") as fh:
        return json.load(fh)


def recent(candidates):
    """Slide the fixed fixture dates up to the present, keeping the gaps between
    them intact. The demo would otherwise fall out of the lookback window and
    print an empty file, and the gaps are what the interview-round logic reads."""
    stamps = [dt.datetime.strptime(c["received"], "%Y-%m-%dT%H:%M:%SZ")
              for c in candidates]
    shift = (dt.datetime.utcnow() - dt.timedelta(days=1)) - max(stamps)
    for cand, stamp in zip(candidates, stamps):
        cand["received"] = (stamp + shift).strftime("%Y-%m-%dT%H:%M:%SZ")
    return candidates


def fetch(cfg, days):
    candidates = []
    for item in load_fixtures():
        row = {k: v for k, v in item.items() if k not in ("expect", "why")}
        row.setdefault("preview", row["body"][:600])
        row.setdefault("web_link", "")
        row.setdefault("matched", ["demo"])
        candidates.append(row)
    return recent(candidates), len(candidates)
