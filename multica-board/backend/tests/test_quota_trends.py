"""Quota thresholds, persisted historical estimates and read-only presentation."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.usage import (
    UsageCollector, enrich_quota_trends, history_from, present_quotas,
    quota_alerts_for, read_history, write_snapshot, load_snapshot,
)
from multica_board.views import render_attention_text
from multica_board.store import Store
from multica_board.collector import CollectOptions

NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)
RESET = "2026-11-19T00:00:00Z"


def quotas(used=50, reset=RESET):
    return [{"provider": "cursor", "status": "ok", "updated_at": NOW.isoformat(),
             "last_success_at": NOW.isoformat(), "windows": [
                 {"key": "tertiary", "label": "wrong upstream label", "used_percent": used,
                  "resets_at": reset, "window_minutes": 44640, "pace": None}]}]


class QuotaTrendTests(unittest.TestCase):
    def setUp(self):
        self.history = json.loads((Path(__file__).parent / "fixtures/quota_history.json").read_text())

    def window(self, raw, now=NOW):
        return present_quotas(raw, now)[0]["windows"][0]

    def test_exact_thresholds_and_legacy_signal(self):
        for used, band in ((0, "normal"), (69.99, "normal"), (70, "watch"),
                           (84.99, "watch"), (85, "warning"), (94.99, "warning"),
                           (95, "critical"), (99.89, "critical"), (99.9, "exhausted"), (100, "exhausted")):
            with self.subTest(used=used):
                raw = quotas(used)
                w = self.window(raw)
                self.assertEqual(w["band"], band)
                self.assertIn(w["signal"], ("normal", "dispatchable", "exhausting"))
                alerts = quota_alerts_for({"external_quotas": raw}, NOW)
                self.assertEqual(bool(alerts), used >= 85)
                if alerts:
                    self.assertEqual(alerts[0]["identifier"], "cursor:tertiary")
                    self.assertEqual(alerts[0]["label"], "Third Party")
                    self.assertEqual(alerts[0]["band"], band)

    def test_freshness_source_time_errors_and_unknown(self):
        raw = quotas(100)
        for seconds, stale in ((0, False), (2700, False), (2701, True)):
            q = present_quotas(raw, NOW + timedelta(seconds=seconds))[0]
            self.assertEqual(q["sample_age_s"], seconds)
            self.assertEqual(q["stale"], stale)
            self.assertEqual(q["windows"][0]["stale"], stale)
            self.assertEqual(q["windows"][0]["band"], "unknown" if stale else "exhausted")
        raw[0]["updated_at"] = (NOW - timedelta(hours=1)).isoformat()
        self.assertTrue(self.window(raw)["stale"])  # successful fetch cannot hide old source
        raw[0].pop("updated_at")
        raw[0]["status"] = "error"
        self.assertFalse(self.window(raw)["stale"])  # recent cached success remains usable
        raw[0].pop("last_success_at")
        w = self.window(raw)
        self.assertIsNone(w["sample_age_s"])
        self.assertEqual(w["band"], "unknown")
        self.assertEqual(w["signal"], "normal")
        alert = quota_alerts_for({"external_quotas": raw}, NOW)[0]
        self.assertEqual(alert["status"], "quota_unknown")
        self.assertIn("无采样时间", alert["title"])

    def test_daily_24h_and_eta_are_persisted_and_get_is_pure(self):
        raw = quotas()
        enrich_quota_trends(raw, self.history, NOW)
        w = self.window(raw)
        self.assertEqual(w["delta_24h_percent"], 40)
        self.assertEqual(w["daily_delta"], [
            {"date": "2026-10-22", "delta_percent": None},
            {"date": "2026-10-23", "delta_percent": 30}])
        self.assertEqual(w["eta_source"], "history_24h")
        self.assertEqual(w["eta_warning_at"], "2026-10-24T15:00:00Z")
        self.assertEqual(w["eta_exhausted_at"], "2026-10-25T00:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            write_snapshot(path, {"external_quotas": raw})
            before = path.read_bytes()
            snapshot = load_snapshot(path)
            with patch("multica_board.usage.read_history", side_effect=AssertionError("GET read history")):
                self.assertEqual(quota_alerts_for(snapshot, NOW), [])
                later = self.window(snapshot["external_quotas"], NOW + timedelta(minutes=15))
                self.assertEqual(later["eta_exhausted_at"], w["eta_exhausted_at"])
            self.assertEqual(path.read_bytes(), before)
        stale = self.window(raw, NOW + timedelta(hours=1))
        self.assertIsNone(stale["eta_exhausted_at"])
        self.assertIsNone(stale["delta_24h_percent"])

    def test_partial_cycle_requires_three_distinct_samples(self):
        raw = quotas()
        enrich_quota_trends(raw, self.history[-1:], NOW)
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])
        enrich_quota_trends(raw, self.history[-2:], NOW)
        self.assertEqual(self.window(raw)["eta_source"], "history_cycle")
        self.assertEqual(self.window(raw)["eta_exhausted_at"], "2026-10-25T00:00:00Z")
        # Failed/stale records cannot provide observations or inflate sample count.
        bad = copy.deepcopy(self.history[-2:])
        bad[0]["providers"][0]["stale"] = True
        enrich_quota_trends(raw, bad, NOW)
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])
        duplicated = [self.history[-1]] * 5
        enrich_quota_trends(raw, duplicated, NOW)
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])

    def test_resets_decreases_and_flat_samples_break_estimates(self):
        raw = quotas(reset="2026-11-26T00:00:00Z")
        enrich_quota_trends(raw, self.history, NOW)
        self.assertIsNone(self.window(raw)["delta_24h_percent"])
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])
        # Same reset string after an intervening changed reset is still a boundary.
        history = copy.deepcopy(self.history)
        history[-1]["providers"][0]["windows"][0]["resets_at"] = raw[0]["windows"][0]["resets_at"]
        raw = quotas()
        enrich_quota_trends(raw, history, NOW)
        self.assertIsNone(self.window(raw)["delta_24h_percent"])
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])
        for used in (5, 10):
            raw = quotas(used)
            history = copy.deepcopy(self.history)
            for record in history:
                record["providers"][0]["windows"][0]["used_percent"] = 10
            enrich_quota_trends(raw, history, NOW)
            self.assertIsNone(self.window(raw)["eta_exhausted_at"])

    def test_pace_and_reached_thresholds_and_reset_horizon(self):
        raw = quotas(50, reset="2026-10-25T18:00:00Z")
        raw[0]["windows"][0].update(window_minutes=4320, pace={"expected_used_percent": 33.3333333333})
        enrich_quota_trends(raw, [], NOW)
        self.assertEqual(self.window(raw)["eta_source"], "pace")
        self.assertEqual(self.window(raw)["eta_exhausted_at"], "2026-10-24T18:00:00Z")
        raw = quotas(100)
        enrich_quota_trends(raw, [], NOW)
        self.assertEqual(self.window(raw)["eta_exhausted_at"], "2026-10-23T18:00:00Z")
        raw = quotas(reset="2026-10-23T19:00:00Z")
        enrich_quota_trends(raw, self.history, NOW)
        self.assertIsNone(self.window(raw)["eta_exhausted_at"])

    def test_history_reader_and_new_record_roundtrip(self):
        raw = quotas()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "history.jsonl"
            path.write_text('\n'.join([json.dumps(self.history[1]), '{bad', 'null',
                                        json.dumps(self.history[0])]))
            self.assertEqual(read_history(path), self.history[:2])
        record = history_from({"external_quotas": present_quotas(raw, NOW), "usage_collected_at": NOW.isoformat()})
        self.assertFalse(record["providers"][0]["stale"])
        self.assertEqual(record["providers"][0]["windows"][0]["sample_at"], NOW.isoformat())
        malformed = [{"at": NOW.isoformat(), "providers": [None, "bad", {"provider": "cursor", "windows": [None]}]},
                     {"at": NOW.isoformat(), "providers": 42}]
        enrich_quota_trends(raw, self.history + malformed, NOW)
        self.assertEqual(self.window(raw)["delta_24h_percent"], 40)

    def test_collector_writes_trends_before_snapshot_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            history = path / "history.jsonl"
            history.write_text(''.join(json.dumps(row) + '\n' for row in self.history))
            store = Store(":memory:")
            try:
                collector = UsageCollector(None, store, "test", CollectOptions(), now=NOW,
                                           snapshot_path=path / "snapshot.json", history_path=history)
                with patch.object(collector, "_codexbar", return_value=quotas()), \
                     patch.object(collector, "_tokens", return_value=([], 0, [])):
                    result = collector.collect()
                self.assertTrue(result["ok"])
                snapshot = load_snapshot(path / "snapshot.json")
                w = snapshot["external_quotas"][0]["windows"][0]
                self.assertEqual(w["delta_24h_percent"], 40)
                self.assertEqual(w["eta_exhausted_at"], "2026-10-25T00:00:00Z")
                self.assertEqual(len(read_history(history)), 5)
            finally:
                store.close()

    def test_enriched_attention_text_multibyte_budget_and_columns(self):
        raw = quotas(96)
        enrich_quota_trends(raw, self.history, NOW)
        alert = quota_alerts_for({"external_quotas": raw}, NOW)[0]
        body = {"generated_at": NOW.isoformat(), "window_h": 24, "quota_alerts": [alert]}
        text = render_attention_text(body)
        for value in ("cursor:tertiary", "Third Party", "band=critical", "Δ24h_pp=86", "ETA100="):
            self.assertIn(value, text)
        line = next(line for line in text.splitlines() if line.startswith("cursor:tertiary"))
        self.assertEqual(len(line.split('\t')), 5)
        body["quota_alerts"] *= 80
        text = render_attention_text(body)
        self.assertLessEqual(len(text.encode("utf-8")), 2048)
        self.assertIn("quota_alerts 80", text)
        self.assertTrue(text.endswith("…\n"))


if __name__ == "__main__":
    unittest.main()
