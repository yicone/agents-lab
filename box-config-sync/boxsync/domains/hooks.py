"""Hooks belong to the Mini, outside the Box collector."""


def collect(ctx):
    # The sentinel records that hooks live on the Mini. domain_states stays
    # not_collected: ok would present an uncollected inventory as collected.
    return {"scope": "external_host", "host": "tr-mini-m4", "state": "not_collected_on_box"}, "not_collected"
