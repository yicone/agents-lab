"""Read-only run-metrics.v0.1 aggregation prototype.

Projects Multica runs into the frozen contract, classifies failures with
versioned rules, and reports rework coverage. It does not emit
``provider_unstable`` and it does not hard-disable on unknown quota.
Rule pack ``failure-rules.v0.2`` fills the S4 gaps; the schema version stays
``run-metrics.v0.1``.
"""

SCHEMA_VERSION = "run-metrics.v0.1"
RULE_VERSION = "failure-rules.v0.2"

__all__ = ["RULE_VERSION", "SCHEMA_VERSION"]
