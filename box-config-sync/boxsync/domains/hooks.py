"""Hooks belong to the Mini, outside the Box collector."""


def collect(ctx):
    return {"scope": "external_host", "host": "tr-mini-m4", "state": "not_collected_on_box"}, "ok"
