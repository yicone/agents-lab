"""Startup-only feature registry; policy overrides defaults, no environment inputs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    default: bool
    description: str


FEATURES = {
    'write_token_required': Feature(False, 'Require the independent Atlas write token.'),
}


def resolve_features(overrides=None):
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict) or set(overrides) - FEATURES.keys():
        raise ValueError('invalid or unknown feature name')
    if any(type(value) is not bool for value in overrides.values()):
        raise ValueError('feature values must be boolean')
    return {name: overrides.get(name, feature.default) for name, feature in FEATURES.items()}
