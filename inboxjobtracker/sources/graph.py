"""Microsoft Graph: Outlook.com, Hotmail and Microsoft 365 mailboxes.

Deliberately high-recall: it is cheap to discard a non-HR email later, and
expensive to never see a rejection at all.
"""
import datetime as dt
import json
import os
import re
import sys
import time

import requests

from ..graph_auth import get_token
from ..prefilter import is_candidate

GRAPH = "https://graph.microsoft.com/v1.0"

LIST_SELECT = "id,subject,receivedDateTime,from,sender,bodyPreview,webLink"


def _get(url, token, params=None, extra_headers=None, tries=5):
    """GET with Graph throttling handled."""
    headers = {"Authorization": "Bearer %s" % token}
    if extra_headers:
        headers.update(extra_headers)
    for attempt in range(tries):
        resp = requests.get(url, headers=headers, params=params, timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:
            wait = int(resp.headers.get("Retry-After", 2 ** attempt))
            print("  throttled (%s), waiting %ss" % (resp.status_code, wait), file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


def list_folder(folder, token, since_iso):
    """Yield lightweight message stubs from one well-known folder."""
    url = "%s/me/mailFolders/%s/messages" % (GRAPH, folder)
    params = {
        "$select": LIST_SELECT,
        "$filter": "receivedDateTime ge %s" % since_iso,
        "$orderby": "receivedDateTime desc",
        "$top": "100",
    }
    page = 0
    while url:
        try:
            data = _get(url, token, params=params)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                print("  folder '%s' not present, skipping" % folder, file=sys.stderr)
                return
            raise
        for msg in data.get("value", []):
            yield msg
        page += 1
        url = data.get("@odata.nextLink")
        params = None  # nextLink already carries the query
        if url:
            print("  %s: page %d..." % (folder, page + 1), file=sys.stderr)


def addr_of(msg):
    frm = msg.get("from") or msg.get("sender") or {}
    ea = frm.get("emailAddress") or {}
    return (ea.get("name") or "").strip(), (ea.get("address") or "").strip().lower()


def fetch_body(msg_id, token, limit):
    """Full plain-text body for one message."""
    data = _get(
        "%s/me/messages/%s" % (GRAPH, msg_id),
        token,
        params={"$select": "body"},
        extra_headers={"Prefer": 'outlook.body-content-type="text"'},
    )
    body = ((data.get("body") or {}).get("content") or "")
    body = re.sub(r"\r\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()[:limit]


def fetch(cfg, days):
    token = get_token(cfg)

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    print("Scanning the last %d days (since %s)" % (days, since_iso), file=sys.stderr)

    seen, candidates, scanned = set(), [], 0
    for folder in cfg["folders"]:
        print("Folder: %s" % folder, file=sys.stderr)
        for msg in list_folder(folder, token, since_iso):
            scanned += 1
            if msg["id"] in seen:
                continue
            seen.add(msg["id"])
            name, addr = addr_of(msg)
            reasons = is_candidate(
                msg.get("subject"), name, addr, msg.get("bodyPreview")
            )
            if not reasons:
                continue
            candidates.append({
                "id": msg["id"],
                "folder": folder,
                "subject": (msg.get("subject") or "").strip(),
                "from_name": name,
                "from_address": addr,
                "received": msg["receivedDateTime"],
                "preview": (msg.get("bodyPreview") or "").strip(),
                "web_link": msg.get("webLink"),
                "matched": reasons,
            })

    print("Scanned %d messages, %d candidates" % (scanned, len(candidates)), file=sys.stderr)

    print("Fetching full bodies...", file=sys.stderr)
    for i, cand in enumerate(candidates, 1):
        try:
            cand["body"] = fetch_body(cand["id"], token, cfg["max_body_chars"])
        except requests.HTTPError as exc:
            print("  body fetch failed for %s: %s" % (cand["id"][:12], exc), file=sys.stderr)
            cand["body"] = cand["preview"]
        if i % 25 == 0:
            print("  %d/%d" % (i, len(candidates)), file=sys.stderr)

    return candidates, scanned
