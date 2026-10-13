"""Tests for the dashboard renderer."""

import json
import re

from tracker import dashboard


def write_history(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return path


def obs(ts, retailer, price, condition="new", source="smartprix", in_stock=True):
    return {
        "ts": ts, "retailer": retailer, "price": price, "condition": condition,
        "source": source, "currency": "USD", "in_stock": in_stock,
        "url": f"https://example.test/{retailer.lower()}",
    }


T1 = "2026-10-13T01:00:00+00:00"
T2 = "2026-10-13T04:00:00+00:00"


class TestBuildSeries:
    def test_direct_retailer_source_beats_the_aggregator(self, tmp_path):
        """Both smartprix and the Newegg API report Newegg/new in the same run
        at different prices. The direct API must win, or the chart shows a
        stale price for a retailer we scrape directly."""
        p = write_history(tmp_path / "h.jsonl", [
            obs(T1, "Newegg", 838.26, source="smartprix"),
            obs(T1, "Newegg", 772.99, source="newegg"),
        ])
        series = dashboard.build_series(dashboard.load_history(p))
        assert len(series) == 1
        (key, rows), = series.items()
        assert key == ("Newegg", "new")
        assert [r["price"] for r in rows] == [772.99]

    def test_condition_splits_series(self, tmp_path):
        p = write_history(tmp_path / "h.jsonl", [
            obs(T1, "Newegg", 772.99, condition="new", source="newegg"),
            obs(T1, "Newegg", 680.06, condition="open_box", source="newegg"),
        ])
        series = dashboard.build_series(dashboard.load_history(p))
        assert set(series) == {("Newegg", "new"), ("Newegg", "open_box")}

    def test_series_ordered_cheapest_current_first(self, tmp_path):
        p = write_history(tmp_path / "h.jsonl", [
            obs(T1, "Amazon", 799.49),
            obs(T1, "Walmart", 772.99),
            obs(T1, "Newegg", 680.06, condition="open_box", source="newegg"),
        ])
        series = dashboard.build_series(dashboard.load_history(p))
        assert [k[0] for k in series] == ["Newegg", "Walmart", "Amazon"]

    def test_points_sorted_by_time(self, tmp_path):
        p = write_history(tmp_path / "h.jsonl", [
            obs(T2, "Amazon", 780.0),
            obs(T1, "Amazon", 799.49),
        ])
        series = dashboard.build_series(dashboard.load_history(p))
        rows = series[("Amazon", "new")]
        assert [r["price"] for r in rows] == [799.49, 780.0]


class TestLoadHistory:
    def test_skips_malformed_and_unparseable_rows(self, tmp_path):
        p = tmp_path / "h.jsonl"
        p.write_text(
            json.dumps(obs(T1, "Amazon", 799.49)) + "\n"
            + "{ not json\n"
            + "\n"
            + json.dumps({"ts": "nonsense", "price": 1, "retailer": "X"}) + "\n"
            + json.dumps({"ts": T1, "price": None, "retailer": "Y"}) + "\n",
            encoding="utf-8",
        )
        rows = dashboard.load_history(p)
        assert len(rows) == 1
        assert rows[0]["retailer"] == "Amazon"

    def test_missing_file_is_empty(self, tmp_path):
        assert dashboard.load_history(tmp_path / "nope.jsonl") == []


class TestRender:
    def _render(self, tmp_path, rows, threshold=650.0, conditions=("new",)):
        hist = write_history(tmp_path / "h.jsonl", rows)
        state = tmp_path / "state.json"
        state.write_text(json.dumps({
            "last_run": T2,
            "lowest_seen": {"newegg:newegg:new": 772.99, "smartprix:amazon:new": 799.49},
        }), encoding="utf-8")
        out = tmp_path / "docs" / "index.html"
        dashboard.render(hist, state, out, "MSI MAG 321UP QD-OLED X24",
                         threshold, list(conditions), "https://example.test/repo")
        return out.read_text(encoding="utf-8")

    def test_renders_self_contained_page(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T1, "Amazon", 799.49), obs(T2, "Amazon", 780.00),
        ])
        assert "<title>" in html
        assert "width=device-width" in html
        # No external stylesheet, font or script.
        assert not re.search(r'<script[^>]*\ssrc=', html)
        assert "<link" not in html
        assert "@import" not in html

    def test_exactly_one_hero_figure(self, tmp_path):
        html = self._render(tmp_path, [obs(T1, "Amazon", 799.49), obs(T2, "Amazon", 780.0)])
        assert html.count('<p class="hero">') == 1
        assert "$780.00" in html

    def test_hero_uses_cheapest_alertable_not_cheapest_overall(self, tmp_path):
        """An open-box unit is cheaper but isn't alertable by default, so the
        headline number must ignore it."""
        html = self._render(tmp_path, [
            obs(T1, "Newegg", 680.06, condition="open_box", source="newegg"),
            obs(T1, "Amazon", 799.49),
            obs(T2, "Newegg", 680.06, condition="open_box", source="newegg"),
            obs(T2, "Amazon", 799.49),
        ])
        hero = re.search(r'<p class="hero">([^<]+)</p>', html).group(1)
        assert hero == "$799.49"

    def test_out_of_stock_excluded_from_hero(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T1, "Newegg", 700.0, source="newegg", in_stock=False),
            obs(T1, "Amazon", 799.49),
            obs(T2, "Newegg", 700.0, source="newegg", in_stock=False),
            obs(T2, "Amazon", 799.49),
        ])
        assert re.search(r'<p class="hero">([^<]+)</p>', html).group(1) == "$799.49"
        assert "Out of stock" in html

    def test_at_target_is_called_out(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T1, "Amazon", 640.0), obs(T2, "Amazon", 640.0),
        ])
        assert "at-target" in html
        assert "At or below your" in html

    def test_end_labels_never_collide(self, tmp_path):
        """Two retailers at the same price must not stack two labels."""
        html = self._render(tmp_path, [
            obs(T1, "Walmart", 800.0), obs(T1, "Amazon", 800.0),
            obs(T2, "Walmart", 772.99), obs(T2, "Amazon", 772.99),
        ])
        ys = [float(y) for y in re.findall(r'class="end-label"', html) and
              re.findall(r'<text x="[\d.]+" y="([\d.]+)" class="end-label">', html)]
        assert len(ys) == 1, "identical prices should yield a single label"
        # Both series still identified by the legend.
        assert len(re.findall(r'class="key"', html)) == 2

    def test_all_series_keep_a_dot_and_legend_entry(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T1, "Walmart", 800.0), obs(T1, "Amazon", 810.0),
            obs(T2, "Walmart", 772.99), obs(T2, "Amazon", 772.99),
        ])
        assert len(re.findall(r'<circle ', html)) == 2
        assert len(re.findall(r'class="key"', html)) == 2

    def test_single_observation_shows_no_chart(self, tmp_path):
        html = self._render(tmp_path, [obs(T1, "Amazon", 799.49)])
        assert "once the tracker has run at least twice" in html
        assert "<svg" not in html
        # The table view still carries the data.
        assert "$799.49" in html

    def test_empty_history_still_renders(self, tmp_path):
        html = self._render(tmp_path, [])
        assert "No observations recorded yet." in html
        assert '<p class="hero">&mdash;</p>' in html

    def test_table_view_lists_every_current_listing(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T2, "Amazon", 799.49),
            obs(T2, "Walmart", 772.99),
            obs(T2, "Newegg", 680.06, condition="open_box", source="newegg"),
        ])
        body = html.split("<tbody>")[1].split("</tbody>")[0]
        assert body.count("<tr>") == 3
        for name in ("Amazon", "Walmart", "Newegg"):
            assert name in body
        assert "open box" in body

    def test_target_rule_is_labelled(self, tmp_path):
        html = self._render(tmp_path, [
            obs(T1, "Amazon", 799.49), obs(T2, "Amazon", 780.0),
        ])
        assert "target $650.00" in html

    def test_dark_mode_declared_under_both_scopes(self, tmp_path):
        html = self._render(tmp_path, [obs(T1, "Amazon", 799.49), obs(T2, "Amazon", 780.0)])
        assert "@media (prefers-color-scheme: dark)" in html
        assert ':root[data-theme="dark"]' in html
        assert ':root:not([data-theme="light"])' in html

    def test_html_is_escaped(self, tmp_path):
        rows = [obs(T1, "Evil<script>", 700.0), obs(T2, "Evil<script>", 700.0)]
        html = self._render(tmp_path, rows)
        assert "Evil<script>" not in html
        assert "Evil&lt;script&gt;" in html
