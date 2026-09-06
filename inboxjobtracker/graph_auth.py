"""Device-code auth against Microsoft Graph for a personal Hotmail/Outlook.com account.

Token cache is persisted to disk so only the first run is interactive.
"""
import atexit
import json
import os
import sys

import msal

HERE = os.path.dirname(os.path.abspath(__file__))


def _cache(path):
    cache = msal.SerializableTokenCache()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            cache.deserialize(fh.read())

    def _save():
        if cache.has_state_changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(cache.serialize())
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    atexit.register(_save)
    return cache


def get_token(cfg):
    """Return a Graph access token, prompting with a device code only when needed."""
    cache_path = cfg.get("token_cache_path") or "token_cache.json"
    cache = _cache(cache_path)
    app = msal.PublicClientApplication(
        cfg["client_id"], authority=cfg["authority"], token_cache=cache
    )

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(cfg["scopes"], account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

    flow = app.initiate_device_flow(scopes=cfg["scopes"])
    if "user_code" not in flow:
        sys.exit("Could not start device flow: %s" % json.dumps(flow, indent=2))

    print("\n" + "=" * 70, file=sys.stderr)
    print(flow["message"], file=sys.stderr)
    print("=" * 70 + "\n", file=sys.stderr)
    sys.stderr.flush()

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        sys.exit(
            "Sign-in failed: %s: %s"
            % (result.get("error"), result.get("error_description"))
        )
    return result["access_token"]
