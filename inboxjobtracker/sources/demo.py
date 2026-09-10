"""Synthetic mailbox, so the tool can be tried without connecting an account.

The messages are the real failure modes this classifier was built against — a
negated rejection, a receipt that describes the pipeline, a layoff letter, an
account-verification notice — with every name invented.
"""
import datetime as dt
import json
import os
from urllib.parse import quote

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
    # utcnow() is deprecated and due for removal; datetime.UTC does not exist
    # before 3.11, and this supports 3.9, so it goes through timezone.utc and
    # drops the tzinfo to stay comparable with the naive fixture stamps.
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    shift = (now - dt.timedelta(days=1)) - max(stamps)
    for cand, stamp in zip(candidates, stamps):
        cand["received"] = (stamp + shift).strftime("%Y-%m-%dT%H:%M:%SZ")
    return candidates


def demo_link(fixture_id):
    """A Web Link in the exact shape the IMAP source builds for Gmail.

    Without one the column is empty and the page renders every subject as
    plain text, so the demo hides the half of a verdict that makes it
    checkable - the way back to the message it rests on. The message ids are
    invented, because there is no real mail behind a synthetic mailbox.
    """
    mid = "demo-%s@inbox-job-tracker.example" % fixture_id
    return ("https://mail.google.com/mail/?authuser=you@example.com#search/%s"
            % quote("rfc822msgid:" + mid, safe=""))


def fetch(cfg, days):
    candidates = []
    for item in load_fixtures():
        row = {k: v for k, v in item.items() if k not in ("expect", "why")}
        row.setdefault("preview", row["body"][:600])
        row.setdefault("web_link", demo_link(item["id"]))
        row.setdefault("matched", ["demo"])
        candidates.append(row)
    return recent(candidates), len(candidates)
