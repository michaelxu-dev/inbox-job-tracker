"""IMAP: Gmail, Outlook.com, Fastmail, or any provider that speaks it.

No OAuth app registration — an app password is enough, which makes this by far
the shortest path from clone to first run. Gmail needs 2-Step Verification on,
then an App Password (myaccount.google.com/apppasswords) in IMAP_PASSWORD.
"""
import email
import email.utils
import datetime as dt
import imaplib
import os
import sys
from email.header import decode_header, make_header
from urllib.parse import quote

from ..prefilter import is_candidate


def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body(message, limit):
    """Prefer text/plain; fall back to stripping tags off the HTML part."""
    parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                parts.append(part)
    else:
        parts.append(message)
    for part in parts:
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload:
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, "replace")[:limit]
    import re
    for part in (message.walk() if message.is_multipart() else [message]):
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            return re.sub(r"\s{2,}", " ", text).strip()[:limit]
    return ""


def _search_link(host, user):
    """A URL that opens the message in the provider's web client.

    IMAP has no per-message URL, so this is a search. Gmail will not match a
    bare Message-ID - it has to be the rfc822msgid: operator, and the id has to
    be escaped, or the @ and dots are read as more search terms.

    The mailbox is named with ?authuser=, because the obvious alternatives are
    both wrong when more than one account is signed in: /mail/u/0/ opens
    whichever account happens to be first, where the message does not exist,
    and /mail/u/<address>/ is answered with a 404.
    """
    if "gmail" not in (host or "").lower():
        return lambda mid: ""
    who = "?authuser=%s" % quote(user, safe="@") if user else "u/0/"

    def link(mid):
        mid = (mid or "").strip("<>")
        if not mid:
            return ""
        return ("https://mail.google.com/mail/%s#search/%s"
                % (who, quote("rfc822msgid:" + mid, safe="")))
    return link


def fetch(cfg, days):
    # Named per account, so a second IMAP mailbox does not have to overwrite
    # the first one's password to be read.
    var = cfg.get("password_env") or "IMAP_PASSWORD"
    password = os.environ.get(var)
    if not password:
        sys.exit(
            "%s is not set.\n"
            "Gmail: turn on 2-Step Verification, create an App Password at\n"
            "  https://myaccount.google.com/apppasswords\n"
            "Yahoo: Account Security -> Generate app password.\n"
            "then set imap_user in the config and %s in your environment."
            % (var, var))
    user = cfg.get("imap_user")
    if not user:
        sys.exit("Set imap_user in config.json, or IMAP_USER in the environment.")

    since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
    print("Scanning the last %d days over IMAP (since %s)" % (days, since),
          file=sys.stderr)

    search_link = _search_link(cfg["imap_host"], user)
    conn = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)))
    try:
        conn.login(user, password)
    except imaplib.IMAP4.error as exc:
        sys.exit("IMAP login failed: %s\n"
                 "For Gmail this must be an App Password, not your normal one." % exc)

    seen, candidates, scanned = set(), [], 0
    try:
        for folder in cfg.get("imap_folders") or ["INBOX"]:
            status, _ = conn.select('"%s"' % folder, readonly=True)
            if status != "OK":
                print("  skipping folder %r (not found)" % folder, file=sys.stderr)
                continue
            print("Folder: %s" % folder, file=sys.stderr)
            status, data = conn.search(None, "(SINCE %s)" % since)
            if status != "OK":
                continue
            for num in data[0].split():
                scanned += 1
                status, raw = conn.fetch(num, "(RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    continue
                message = email.message_from_bytes(raw[0][1])
                message_id = message.get("Message-ID") or "imap-%s-%s" % (folder, num.decode())
                if message_id in seen:
                    continue
                seen.add(message_id)

                subject = _decode(message.get("Subject"))
                name, address = email.utils.parseaddr(_decode(message.get("From")))
                body = _body(message, int(cfg.get("max_body_chars", 4000)))
                reasons = is_candidate(subject, name, address, body[:600])
                if not reasons:
                    continue
                stamp = email.utils.parsedate_to_datetime(message.get("Date"))
                candidates.append({
                    "id": message_id,
                    "folder": folder,
                    "subject": subject.strip(),
                    "from_name": name,
                    "from_address": address.lower(),
                    "received": stamp.astimezone(dt.timezone.utc)
                                     .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "preview": body[:600],
                    "body": body,
                    # IMAP has no per-message web URL; a search link is the
                    # closest thing that still lands the reader on the mail,
                    # and only Gmail's is worth guessing at.
                    "web_link": search_link(message_id),
                    "matched": reasons,
                })
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()

    print("Scanned %d messages, %d candidates" % (scanned, len(candidates)),
          file=sys.stderr)
    return candidates, scanned
