"""Endpoint credentials for the status bridge.

Look up per-dest secrets from the login Keychain (service one.yic.multica-bridge,
account=<endpoint_ref>). Steward may fall back to webhook.env until Keychain is
populated. Never log URL, Authorization, or secret material.
"""
from __future__ import annotations

import json
import os
import subprocess

KEYCHAIN_SERVICE = "one.yic.multica-bridge"


def _parse_secret(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        url = (obj.get("url") or obj.get("WEBHOOK_URL") or "").strip()
        header = (obj.get("header") or obj.get("WEBHOOK_HEADER") or "").strip()
        auth = (obj.get("authorization") or obj.get("Authorization") or "").strip()
        if auth and ":" not in header:
            header = f"Authorization: {auth}" if not auth.lower().startswith("authorization") else auth
        if url:
            return {"url": url, "header": header or None}
        return None
    # two-line url\\nheader
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return None
    url = lines[0]
    header = lines[1] if len(lines) > 1 else None
    return {"url": url, "header": header}


def keychain_get(account):
    """Return parsed cred dict or None. Raises nothing on missing item."""
    if not account:
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", str(account), "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return _parse_secret(out.stdout)


def env_steward(conf):
    """Deprecated steward fallback from webhook.env. conf is a dict of keys."""
    conf = conf or {}
    url = (conf.get("WEBHOOK_URL") or "").strip()
    if not url:
        return None
    header = (conf.get("WEBHOOK_HEADER") or "").strip() or None
    return {"url": url, "header": header}


def load_cred(endpoint_ref, conf=None, steward_dest="steward"):
    """(cred_dict_or_None, source). source is keychain|env|missing. No secrets."""
    ref = endpoint_ref or steward_dest
    cred = keychain_get(ref)
    if cred and cred.get("url"):
        return cred, "keychain"
    if ref == steward_dest:
        fallback = env_steward(conf)
        if fallback and fallback.get("url"):
            return fallback, "env"
    return None, "missing"
