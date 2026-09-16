import json

import polars as pl
import pytest

from gridiron_value import historical as h
from gridiron_value.project_status import build_status, chronological_rows


def _record(root, path):
    return h.record(root, path)


def test_build_status_writes_report_tables_dashboard_and_manifest(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    coverage_dir = tmp_path / "coverage"
    coverage_dir.mkdir()
    coverage = coverage_dir / "coverage_manifest.json"
    h.save_json(coverage, {
        "season": 2026, "status": "completed", "feeds": {
            "pbp": {"status": "available", "rows": 10},
            "rosters": {"status": "available", "rows": 4},
        }, "identity_coverage": [{
            "feed": "player_stats", "status": "evaluated", "distinct_ids": 2,
            "matched_ids": 2, "distinct_id_match_fraction": 1.0,
        }],
    })
    scores = coverage_dir / "season_scores.csv"
    pl.DataFrame({"season": [2025, 2025, 2026, 2026],
                  "model": ["offense_only", "offense_defense"] * 2,
                  "mse": [0.10, 0.08, 0.20, 0.19]}).write_csv(scores)
    chrono = coverage_dir / "chronological_manifest.json"
    h.save_json(chrono, {"settings": {"first_test_week": 5},
                         "files": {"season_scores": _record(tmp_path, scores)}})
    output = tmp_path / "status"
    manifest, state = build_status(tmp_path, {"coverage": coverage,
                                               "chronological": chrono}, output)
    assert manifest.exists()
    assert (output / "report.md").read_text().count("Coverage") == 1
    assert (output / "dashboard.html").read_text().startswith("<!doctype html>")
    assert state["status"] == "completed"
    assert json.loads(manifest.read_text())["files"]["coverage"]["path"] == "status/coverage_summary.csv"


def test_chronological_rows_calculates_mse_gain(tmp_path):
    scores = tmp_path / "scores.csv"
    pl.DataFrame({"season": [2025, 2025],
                  "model": ["offense_only", "offense_defense"],
                  "mse": [0.25, 0.20]}).write_csv(scores)
    root = tmp_path
    record = _record(root, scores)
    rows = chronological_rows(root, {"files": {"season_scores": record}})
    assert rows[0]["season"] == 2025
    assert rows[0]["offense_only_mse"] == pytest.approx(0.25)
    assert rows[0]["offense_defense_mse"] == pytest.approx(0.2)
    assert rows[0]["defense_mse_gain"] == pytest.approx(0.05)


def test_coverage_table_uses_display_headers_and_position_rows_fallback(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    coverage_dir = tmp_path / "coverage"
    coverage_dir.mkdir()
    coverage = coverage_dir / "coverage_manifest.json"
    h.save_json(coverage, {"status": "completed", "feeds": {
        "pbp": {"status": "available", "rows": 12},
    }})
    metrics = coverage_dir / "position_metrics.parquet"
    pl.DataFrame({"metric_role": ["QB", "WR", "OTHER"]}).write_parquet(metrics)
    position = coverage_dir / "position_metrics_manifest.json"
    h.save_json(position, {"files": {"all": _record(tmp_path, metrics)}})
    manifest, _ = build_status(tmp_path, {"coverage": coverage,
                                           "position_metrics": position},
                                tmp_path / "status")
    report = manifest.parent.joinpath("report.md").read_text()
    assert "| feed | pbp | available | 12 |" in report
    assert "| Position metrics | completed | 3 |" in report
