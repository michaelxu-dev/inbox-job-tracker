"""Configuration: a JSON file, overridable by environment variables.

Every setting has a working default, so a first run needs nothing but mailbox
credentials. Anything secret belongs in the environment, never in the file.

One file can describe several mailboxes. An "accounts" block names them, and
each name holds only what differs from the settings above it:

    {
      "own_addresses": ["me@example.com"],     # shared by every account
      "default_account": "outlook",
      "accounts": {
        "outlook": {"source": "graph", "client_id": "..."},
        "gmail":   {"source": "imap", "imap_user": "me@gmail.com"}
      }
    }

Pick one with --account, or JOBTRACKER_ACCOUNT. Each account keeps its own
store, so mailboxes never merge into one another's history: unless it says
otherwise, an account named "gmail" reads and writes data/gmail.
"""
import json
import os
import sys

DEFAULTS = {
    "source": "demo",
    "lookback_days": 90,
    "folders": ["inbox", "archive", "junkemail"],
    "own_addresses": [],
    "review_batch_size": 40,
    "interview_round_gap_days": 10,
    "max_body_chars": 4000,
    "judge": "off",
    "judge_model": "claude-haiku-4-5-20251001",
    "data_dir": "data",
    # Microsoft Graph
    "client_id": "",
    "authority": "https://login.microsoftonline.com/consumers",
    "scopes": ["Mail.Read"],
    "token_cache_path": "token_cache.json",
    # IMAP (Gmail, Yahoo, Fastmail, and friends)
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_user": "",
    "imap_folders": ["INBOX"],
    # Which environment variable holds this account's password. Named per
    # account so two IMAP mailboxes can be open at once.
    "password_env": "IMAP_PASSWORD",
}

# Settings that are per-mailbox by nature. When an account leaves one unset it
# is derived from the account name rather than inherited, so two mailboxes can
# never quietly share one store or one token cache.
PER_ACCOUNT = {
    "data_dir": lambda name: os.path.join("data", name),
    "token_cache_path": lambda name: "token_cache-%s.json" % name,
}

# Environment overrides, so secrets and CI never touch the config file.
ENV_MAP = {
    "JOBTRACKER_SOURCE": ("source", str),
    "JOBTRACKER_DATA_DIR": ("data_dir", str),
    "JOBTRACKER_JUDGE": ("judge", str),
    "GRAPH_CLIENT_ID": ("client_id", str),
    "IMAP_HOST": ("imap_host", str),
    "IMAP_PORT": ("imap_port", int),
    "IMAP_USER": ("imap_user", str),
}


def _read(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def accounts(path="config.json"):
    """The account names in the file, in the order they were written."""
    return list((_read(path).get("accounts") or {}).keys())


def load(path="config.json", account=None):
    raw = _read(path)
    defined = raw.get("accounts") or {}
    shared = {k: v for k, v in raw.items() if k != "accounts"}

    cfg = dict(DEFAULTS)
    cfg.update(shared)
    cfg["account"] = None

    name = account or os.environ.get("JOBTRACKER_ACCOUNT") or raw.get("default_account")
    if defined:
        if not name:
            name = next(iter(defined))
        if name not in defined:
            sys.exit("Unknown account %r in %s. Defined: %s"
                     % (name, path, ", ".join(defined) or "none"))
        block = defined[name]
        cfg.update(block)
        cfg["account"] = name
        for key, derive in PER_ACCOUNT.items():
            if key not in block and key not in shared:
                cfg[key] = derive(name)
    elif name:
        sys.exit("%s defines no accounts, so --account %r has nothing to select."
                 % (path, name))

    for env, (key, cast) in ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = cast(os.environ[env])
    cfg["own_addresses"] = [a.strip().lower() for a in cfg.get("own_addresses", []) if a.strip()]
    return cfg


def data_path(cfg, name):
    directory = cfg.get("data_dir") or "data"
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, name)
