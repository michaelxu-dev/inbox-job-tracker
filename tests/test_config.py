"""One config file, several mailboxes.

What matters here is that two accounts can never quietly share a store: that is
what would merge one mailbox's history into another's and corrupt both.
"""
import json
import os

import pytest

from inboxjobtracker import config

MULTI = {
    "own_addresses": ["me@example.com"],
    "lookback_days": 45,
    "default_account": "outlook",
    "accounts": {
        "outlook": {"source": "graph", "client_id": "abc", "data_dir": "data"},
        "gmail": {"source": "imap", "imap_user": "me@gmail.com",
                  "password_env": "GMAIL_APP_PASSWORD"},
        "yahoo": {"source": "imap", "imap_host": "imap.mail.yahoo.com",
                  "imap_user": "me@yahoo.com"},
    },
}


def write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_shared_settings_reach_every_account(tmp_path):
    path = write(tmp_path, MULTI)
    for name in ("outlook", "gmail", "yahoo"):
        cfg = config.load(path, name)
        assert cfg["own_addresses"] == ["me@example.com"]
        assert cfg["lookback_days"] == 45


def test_account_block_overrides_shared_and_defaults(tmp_path):
    cfg = config.load(write(tmp_path, MULTI), "yahoo")
    assert cfg["source"] == "imap"
    assert cfg["imap_host"] == "imap.mail.yahoo.com"
    assert cfg["password_env"] == "IMAP_PASSWORD"     # default, yahoo sets none


def test_each_account_gets_its_own_store(tmp_path):
    path = write(tmp_path, MULTI)
    dirs = [config.load(path, n)["data_dir"] for n in ("outlook", "gmail", "yahoo")]
    assert dirs[0] == "data"                          # set explicitly, kept
    assert dirs[1] == os.path.join("data", "gmail")   # derived from the name
    assert dirs[2] == os.path.join("data", "yahoo")
    assert len(set(dirs)) == 3

    # Same rule for the Graph token cache: derived unless the account pins it.
    caches = [config.load(path, n)["token_cache_path"] for n in ("outlook", "gmail")]
    assert caches == ["token_cache-outlook.json", "token_cache-gmail.json"]


def test_default_account_is_used_when_none_is_named(tmp_path):
    cfg = config.load(write(tmp_path, MULTI))
    assert cfg["account"] == "outlook"


def test_first_account_wins_when_no_default_is_declared(tmp_path):
    data = dict(MULTI)
    data.pop("default_account")
    assert config.load(write(tmp_path, data))["account"] == "outlook"


def test_environment_beats_the_account_block(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBTRACKER_ACCOUNT", "gmail")
    path = write(tmp_path, MULTI)
    assert config.load(path)["account"] == "gmail"
    monkeypatch.setenv("IMAP_USER", "other@gmail.com")
    assert config.load(path)["imap_user"] == "other@gmail.com"


def test_unknown_account_names_the_ones_that_exist(tmp_path):
    with pytest.raises(SystemExit) as err:
        config.load(write(tmp_path, MULTI), "protonmail")
    assert "outlook" in str(err.value) and "gmail" in str(err.value)


def test_a_single_mailbox_config_still_works(tmp_path):
    """The accounts block is optional; the flat form predates it."""
    path = write(tmp_path, {"source": "imap", "imap_user": "me@gmail.com",
                            "own_addresses": ["ME@example.com"]})
    cfg = config.load(path)
    assert cfg["account"] is None
    assert cfg["data_dir"] == "data"
    assert cfg["own_addresses"] == ["me@example.com"]     # normalised


def test_naming_an_account_a_flat_config_lacks_is_an_error(tmp_path):
    path = write(tmp_path, {"source": "imap"})
    with pytest.raises(SystemExit):
        config.load(path, "gmail")


def test_accounts_lists_names_in_file_order(tmp_path):
    assert config.accounts(write(tmp_path, MULTI)) == ["outlook", "gmail", "yahoo"]


# --- IMAP message links -------------------------------------------------

def test_gmail_link_uses_the_rfc822msgid_operator():
    """A bare Message-ID finds nothing in Gmail: the operator is required, and
    the id must be escaped or its @ and dots are read as separate terms."""
    from inboxjobtracker.sources.imap import _search_link

    link = _search_link("imap.gmail.com", "me@gmail.com")
    url = link("<20260802032307.7ee22ab1cb90da81@us.greenhouse-mail.io>")
    assert "#search/rfc822msgid%3A" in url
    assert "%40us.greenhouse-mail.io" in url
    assert "<" not in url and ">" not in url
    # The account is named with ?authuser=, since /u/0 opens whichever account
    # is first and /mail/u/<address>/ is a 404.
    assert "?authuser=me@gmail.com#search/" in url
    assert "/u/0/" not in url


def test_no_link_is_guessed_for_other_providers():
    from inboxjobtracker.sources.imap import _search_link

    assert _search_link("imap.mail.yahoo.com", "me@yahoo.com")("<a@b.com>") == ""
    assert _search_link("imap.gmail.com", "me@gmail.com")("") == ""


# --- IMAP two-pass fetch ------------------------------------------------

class FakeIMAP:
    """Enough of imaplib's FETCH response shape to test the parser.

    A multi-part FETCH interleaves tuples and bare bytes, and only the first
    tuple of each message carries its sequence number - which is the part that
    is easy to get wrong.
    """

    def __init__(self, response):
        self.response = response
        self.asked = None

    def fetch(self, nums, spec):
        self.asked = (nums, spec)
        return "OK", self.response


def test_peek_groups_parts_by_message():
    from inboxjobtracker.sources.imap import _peek

    conn = FakeIMAP([
        (b'1 (BODY[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)] {30}',
         b"Subject: One\r\nFrom: a@b.com\r\n"),
        (b' BODY[1]<0> {9}', b"body one"),
        b')',
        (b'2 (BODY[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)] {30}',
         b"Subject: Two\r\nFrom: c@d.com\r\n"),
        (b' BODY[1]<0> {9}', b"body two"),
        b')',
    ])
    out = _peek(conn, [b"1", b"2"])

    assert set(out) == {b"1", b"2"}
    assert b"Subject: One" in out[b"1"]["header"]
    assert out[b"1"]["preview"] == b"body one"
    assert out[b"2"]["preview"] == b"body two"
    # One request for the whole batch, not one per message: that is the point.
    assert conn.asked[0] == b"1,2"
    assert "BODY.PEEK[HEADER.FIELDS" in conn.asked[1]


def test_peek_survives_a_message_with_no_body_part():
    from inboxjobtracker.sources.imap import _peek

    conn = FakeIMAP([
        (b'7 (BODY[HEADER.FIELDS (SUBJECT)] {14}', b"Subject: Bare\r\n"),
        b')',
    ])
    out = _peek(conn, [b"7"])
    assert out[b"7"]["preview"] == b""


def test_preview_is_decoded_enough_for_the_prefilter_to_read():
    """The slice arrives in its transfer encoding: asking the server to decode
    it would mean fetching the whole part, which is what this avoids."""
    import base64 as b64
    from inboxjobtracker.sources.imap import _decode_preview

    plain = b"Thank you for applying to Northwind"
    assert _decode_preview(plain) == plain.decode()

    quoted = b"Unfortunately we are unable to move forward =\r\nwith your application"
    assert "move forward" in _decode_preview(quoted)

    encoded = b64.b64encode(b"We regret to inform you that other candidates")
    assert "other candidates" in _decode_preview(encoded)

    assert _decode_preview(b"") == ""
    assert _decode_preview(None) == ""


# --- HTML bodies --------------------------------------------------------

def test_html_body_becomes_readable_prose():
    """A single-part text/html mail used to reach the rules as raw markup, so
    every pattern matched against tags and sentence_around ran a rejection into
    the sign-off as one 'sentence'."""
    import email as email_mod
    from inboxjobtracker.sources.imap import _body

    raw = (
        "MIME-Version: 1.0\r\n"
        "Content-Type: text/html; charset=utf-8\r\n\r\n"
        "<style>.x{color:#fff;font-family:Helvetica}</style>"
        "<div>Hi Dana,</div><div><br></div>"
        "<div>We have made the decision to not move forward for your "
        "application at this time.</div>"
        "<div>We wish you the best of luck!</div>"
    )
    text = _body(email_mod.message_from_string(raw), 4000)

    assert "<div>" not in text and "color:#fff" not in text
    # The decline and the sign-off are separate lines, not one run-on sentence.
    assert "not move forward for your application at this time." in text
    assert text.splitlines()[-1] == "We wish you the best of luck!"


def test_entities_and_block_tags_survive_stripping():
    from inboxjobtracker.sources.imap import _html_to_text

    assert _html_to_text("<p>Ren&eacute;&nbsp;&amp; Co.</p><p>Next</p>") == \
        "René & Co.\nNext"
    # A body that is nothing but CSS leaves nothing to read, rather than
    # leaving the stylesheet to be classified as prose.
    assert _html_to_text("<style>a{b:c}</style>") == ""


def test_demo_never_writes_into_a_configured_account(tmp_path, monkeypatch):
    """`demo` is the one command sold as safe to try. data_dir is derived per
    account, so on a machine that already has data/outlook it used to overwrite
    that store - verdicts, agent decisions and all - with synthetic fixtures."""
    import json as _json
    from inboxjobtracker import cli

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(_json.dumps({
        "own_addresses": ["me@example.com"],
        "default_account": "outlook",
        "accounts": {"outlook": {"source": "graph"}},
    }), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    real = tmp_path / "data" / "outlook"
    real.mkdir(parents=True)
    (real / "store.json").write_text('{"keep": "me"}', encoding="utf-8")

    assert cli.main(["demo", "--config", str(cfg_file)]) == 0
    assert (real / "store.json").read_text(encoding="utf-8") == '{"keep": "me"}'
    assert (tmp_path / "data" / "demo" / "applications.html").exists()


def test_demo_mail_carries_a_web_link():
    """An empty Web Link makes the demo understate the product: the CSV column
    is blank and the page renders every subject as plain text, hiding the way
    back to the message a verdict rests on."""
    from inboxjobtracker.sources import demo

    candidates, _ = demo.fetch({}, 90)
    assert candidates, "the synthetic mailbox should not be empty"
    for cand in candidates:
        assert cand["web_link"].startswith("https://mail.google.com/mail/")
        # the id is escaped, or Gmail reads the @ and dots as more search terms
        assert "rfc822msgid%3A" in cand["web_link"]
        assert "@" not in cand["web_link"].split("#", 1)[1]


def test_saving_the_store_keeps_the_previous_copy(tmp_path):
    """Everything else in data/ is regenerated; the store is the only file a
    mistake can destroy, and every agent verdict in it was paid for once."""
    from inboxjobtracker import store as store_mod

    path = tmp_path / "store.json"
    store_mod.save(str(path), {"a": {"status": "Reject"}})
    assert not (tmp_path / "store.json.bak").exists(), "nothing to back up on a first write"
    store_mod.save(str(path), {"b": {"status": "Acknowledge"}})
    assert store_mod.load(str(path)) == {"b": {"status": "Acknowledge"}}
    assert store_mod.load(str(path) + ".bak") == {"a": {"status": "Reject"}}


def test_classify_warns_when_the_store_vanished(tmp_path, monkeypatch, capsys):
    """An empty store beside an existing CSV is not a first run - the two are
    written together - so it means the durable record went missing."""
    import json as _json
    from inboxjobtracker import cli

    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "candidates.json").write_text(_json.dumps({"scanned": 0, "candidates": []}),
                                          encoding="utf-8")
    (data / "applications.csv").write_text("CompanyName\n", encoding="utf-8")

    cli.cmd_classify({"own_addresses": [], "review_batch_size": 40,
                      "interview_round_gap_days": 10, "lookback_days": 90}, None)
    assert "is missing or empty" in capsys.readouterr().err
