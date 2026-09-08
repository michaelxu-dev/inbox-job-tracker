"""IMAP: Gmail, Outlook.com, Fastmail, or any provider that speaks it.

No OAuth app registration — an app password is enough, which makes this by far
the shortest path from clone to first run. Gmail needs 2-Step Verification on,
then an App Password (myaccount.google.com/apppasswords) in IMAP_PASSWORD.
"""
import email
import email.utils
import datetime as dt
import base64
import imaplib
import os
import quopri
import re
import sys
from email.header import decode_header, make_header
from html import unescape
from urllib.parse import quote

from ..prefilter import is_candidate


def _decode(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


# Tags that end a line of prose. Turning them into newlines rather than spaces
# is what lets the rules read a sentence at a time: HTML mail is written without
# a full stop before the closing tag, so "</div><div>" collapsed to a space runs
# a rejection straight into the sign-off as one sentence, and the Notes column
# then quotes the wrong half of it.
_BLOCK_END = re.compile(
    r"</(p|div|tr|li|ul|ol|h[1-6]|table|blockquote|section|article)\s*>"
    r"|<br\s*/?>|</?(td|th)[^>]*>", re.I)


def _html_to_text(html):
    """Readable text out of an HTML part.

    Style and script go first, and they have to: a marketing mail's <style>
    block is routinely longer than its prose, so a body truncated to a few
    thousand characters can otherwise be pure CSS with no readable text in it
    at all - undecidable for the rules and for anyone reading the queue.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1\s*>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _body(message, limit):
    """Prefer text/plain; fall back to stripping tags off the HTML part."""
    parts = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                parts.append(part)
    elif message.get_content_type() == "text/plain":
        # Only when it really is plain text. A single-part text/html mail used
        # to be returned here verbatim, tags and all, which left every rule
        # matching against markup rather than prose.
        parts.append(message)
    for part in parts:
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload:
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, "replace")[:limit]
    for part in (message.walk() if message.is_multipart() else [message]):
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            return _html_to_text(text)[:limit]
    return ""


HEADERS = "BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)]"
# The first body part is text/plain in essentially every mail an ATS sends.
# 1500 octets is more than the 600 the prefilter reads, with room for the
# transfer encoding to expand.
PREVIEW = "BODY.PEEK[1]<0.1500>"
CHUNK = 100
_SEQ = re.compile(rb"^\*?\s*(\d+)\s+\(")


def _decode_preview(raw):
    """Undo the transfer encoding enough for the prefilter to read the text.

    The preview arrives raw, because asking the server to decode it would mean
    fetching the whole part. Quoted-printable and base64 are the two that turn
    an ordinary rejection into bytes no keyword matches.
    """
    if not raw:
        return ""
    if b"=3D" in raw or b"=\r\n" in raw or b"=\n" in raw:
        try:
            raw = quopri.decodestring(raw)
        except Exception:
            pass
    elif len(raw) > 32 and not re.search(rb"[ \t<>]", raw[:200]):
        try:
            # A truncated part is unlikely to be a multiple of four.
            raw = base64.b64decode(raw[:len(raw) // 4 * 4], validate=False)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def _peek(conn, nums):
    """Header fields plus a slice of the first body part, for many messages at
    once. This is what keeps a large mailbox affordable: the alternative is
    downloading every message in full, attachments included, only to discard
    most of them."""
    out = {}
    status, data = conn.fetch(b",".join(nums), "(%s %s)" % (HEADERS, PREVIEW))
    if status != "OK":
        return out
    current, part = None, None
    for item in data:
        if isinstance(item, tuple):
            prefix, payload = item[0] or b"", item[1] or b""
            match = _SEQ.match(prefix)
            if match:
                current = match.group(1)
                out.setdefault(current, {"header": b"", "preview": b""})
            if current is None:
                continue
            part = "header" if b"HEADER.FIELDS" in prefix else "preview"
            out[current][part] = payload
    return out


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
            status, data = conn.search(None, "(SINCE %s)" % since)
            if status != "OK":
                continue
            nums = data[0].split()
            print("Folder: %s (%d messages)" % (folder, len(nums)), file=sys.stderr)

            # Pass one: headers and a slice of the first body part, in batches.
            # Everything in the window is read, but almost none of it is
            # downloaded.
            wanted = []
            for at in range(0, len(nums), CHUNK):
                chunk = nums[at:at + CHUNK]
                peeked = _peek(conn, chunk)
                for num in chunk:
                    scanned += 1
                    got = peeked.get(num)
                    if not got:
                        continue
                    head = email.message_from_bytes(got["header"])
                    message_id = (head.get("Message-ID")
                                  or "imap-%s-%s" % (folder, num.decode()))
                    if message_id in seen:
                        continue
                    seen.add(message_id)
                    subject = _decode(head.get("Subject"))
                    name, address = email.utils.parseaddr(_decode(head.get("From")))
                    # Stripped before the prefilter reads it: the slice is the
                    # top of the HTML part as often as not, which is where the
                    # <style> block lives, and raw CSS matches nothing.
                    reasons = is_candidate(
                        subject, name, address,
                        _html_to_text(_decode_preview(got["preview"]))[:600])
                    if reasons:
                        wanted.append((num, message_id, subject, name, address,
                                       head.get("Date"), reasons))
                if len(nums) > CHUNK:
                    print("  %d/%d" % (min(at + CHUNK, len(nums)), len(nums)),
                          file=sys.stderr)

            # Pass two: the full message, only for what survived.
            if wanted:
                print("  fetching %d bodies..." % len(wanted), file=sys.stderr)
            for num, message_id, subject, name, address, date, reasons in wanted:
                status, raw = conn.fetch(num, "(RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    continue
                message = email.message_from_bytes(raw[0][1])
                body = _body(message, int(cfg.get("max_body_chars", 4000)))
                stamp = email.utils.parsedate_to_datetime(date or message.get("Date"))
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
