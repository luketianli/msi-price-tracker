"""Tests for threshold filtering, cooldown de-duplication and health tracking."""

import json
from datetime import timedelta

import pytest

from tracker import state as state_mod
from tracker.main import qualifying
from tracker.models import Listing

ALERT = {"threshold": 650.0, "conditions": ["new"], "cooldown_hours": 24, "renotify_drop": 10.0}


def listing(price, condition="new", retailer="Newegg", in_stock=True, source="newegg"):
    return Listing(
        source=source, retailer=retailer, title="MSI MAG 321UP QD-OLED X24",
        price=price, currency="USD", url="https://example.test", condition=condition,
        in_stock=in_stock,
    )


class TestQualifying:
    def test_under_threshold_new_and_in_stock_qualifies(self):
        assert qualifying([listing(649.99)], ALERT)

    def test_exactly_at_threshold_qualifies(self):
        assert qualifying([listing(650.00)], ALERT)

    def test_above_threshold_does_not(self):
        assert not qualifying([listing(650.01)], ALERT)

    def test_open_box_excluded_by_default(self):
        """The real open-box X24 sits at ~$665; at a $700 target it must still
        not alert while conditions is ['new']."""
        relaxed = {**ALERT, "threshold": 700.0}
        assert not qualifying([listing(664.99, condition="open_box")], relaxed)

    def test_open_box_included_when_opted_in(self):
        opted = {**ALERT, "threshold": 700.0, "conditions": ["new", "open_box"]}
        assert qualifying([listing(664.99, condition="open_box")], opted)

    def test_out_of_stock_excluded(self):
        assert not qualifying([listing(500.0, in_stock=False)], ALERT)

    def test_unknown_stock_is_allowed(self):
        assert qualifying([listing(500.0, in_stock=None)], ALERT)


class TestShouldAlert:
    def _call(self, state, item):
        return state_mod.should_alert(state, item, cooldown_hours=24, renotify_drop=10.0)

    def test_first_sighting_alerts(self):
        assert self._call({}, listing(640.0))

    def test_same_price_within_cooldown_is_suppressed(self):
        item = listing(640.0)
        state = {"alerts": {}}
        state_mod.mark_alerted(state, [item])
        assert not self._call(state, item)

    def test_further_drop_realerts_immediately(self):
        state = {"alerts": {}}
        state_mod.mark_alerted(state, [listing(640.0)])
        assert self._call(state, listing(629.0))      # -11, past renotify_drop
        assert not self._call(state, listing(635.0))  # -5, not enough

    def test_cooldown_expiry_realerts(self):
        item = listing(640.0)
        state = {"alerts": {}}
        state_mod.mark_alerted(state, [item])
        stale = (state_mod.utcnow() - timedelta(hours=25)).isoformat()
        state["alerts"][item.key]["last_alert"] = stale
        assert self._call(state, item)

    def test_condition_is_part_of_the_key(self):
        """Alerting on an open-box unit must not suppress a later new-unit alert."""
        state = {"alerts": {}}
        state_mod.mark_alerted(state, [listing(600.0, condition="open_box")])
        assert self._call(state, listing(600.0, condition="new"))

    def test_corrupt_timestamp_errs_toward_alerting(self):
        item = listing(640.0)
        state = {"alerts": {item.key: {"last_alert": "not-a-date", "last_price": 640.0}}}
        assert self._call(state, item)


class TestHealthTracking:
    def test_streak_increments_then_resets(self):
        state = state_mod.load_state(state_mod.Path("nonexistent-state.json"))
        assert state_mod.note_run_outcome(state, False) == 1
        assert state_mod.note_run_outcome(state, False) == 2
        assert state_mod.note_run_outcome(state, True) == 0

    def test_health_alert_waits_for_threshold(self):
        state = {"consecutive_failures": 0}
        assert not state_mod.health_alert_due(state, 2, 3)
        assert state_mod.health_alert_due(state, 3, 3)

    def test_health_alert_not_repeated_within_window(self):
        state = {"last_health_alert": state_mod.utcnow().isoformat()}
        assert not state_mod.health_alert_due(state, 5, 3)


class TestStateRoundTrip:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "state.json"
        state = state_mod.load_state(path)
        state_mod.mark_alerted(state, [listing(600.0)])
        state_mod.record_lowest(state, [listing(600.0), listing(590.0)])
        state_mod.save_state(state, path)

        reloaded = state_mod.load_state(path)
        assert reloaded["lowest_seen"]["newegg:newegg:new"] == 590.0
        assert reloaded["last_run"]
        assert "newegg:newegg:new" in reloaded["alerts"]

    def test_corrupt_state_file_starts_fresh(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{ not json", encoding="utf-8")
        state = state_mod.load_state(path)
        assert state["alerts"] == {}

    def test_history_is_append_only_jsonl(self, tmp_path):
        path = tmp_path / "history.jsonl"
        state_mod.append_history([listing(800.0)], path)
        state_mod.append_history([listing(790.0), listing(700.0, retailer="Amazon")], path)
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 3
        assert rows[0]["price"] == 800.0
        assert {r["retailer"] for r in rows} == {"Newegg", "Amazon"}
