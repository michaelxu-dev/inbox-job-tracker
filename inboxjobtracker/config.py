"""Configuration: a JSON file, overridable by environment variables.

Every setting has a working default, so a first run needs nothing but mailbox
credentials. Anything secret belongs in the environment, never in the file.
"""
import json
import os

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
    # IMAP (Gmail and friends)
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_user": "",
    "imap_folders": ["INBOX"],
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


def load(path="config.json"):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    for env, (key, cast) in ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = cast(os.environ[env])
    cfg["own_addresses"] = [a.strip().lower() for a in cfg.get("own_addresses", []) if a.strip()]
    return cfg


def data_path(cfg, name):
    directory = cfg.get("data_dir") or "data"
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, name)
