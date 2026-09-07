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
