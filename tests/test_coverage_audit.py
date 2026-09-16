import polars as pl
import pytest

from gridiron_value.coverage_audit import identity_coverage, profile, select_season


def test_wrong_season_is_not_reported_as_current():
    frame = pl.DataFrame({"season": [2025, 2026], "week": [18, 1]})
    assert select_season(frame, 2026)["week"].to_list() == [1]
    assert select_season(frame, 2027).is_empty()
    with pytest.raises(ValueError, match="season column"):
        select_season(frame.drop("season"), 2026)


def test_profile_separates_aggregate_week_and_season_types():
    frame = pl.DataFrame({"season_type": ["REG", "REG", "POST"],
                          "week": [0, 1, 1], "value": [None, float("nan"), 1.0]})
    result = profile(frame)
    assert len(result["week_counts"]) == 3
    field = next(r for r in result["fields"] if r["column"] == "value")
    assert field["nulls"] == 1
    assert field["nonfinite"] == 1


def test_identity_membership_keeps_missing_and_unmatched_separate():
    frames = {"rosters": pl.DataFrame({"gsis_id": ["a", "a", "b"]}),
              "player_stats": pl.DataFrame({"player_id": ["a", "x", None, ""]})}
    result = identity_coverage(frames)[0]
    assert result["matched_ids"] == 1
    assert result["unmatched_ids"] == ["x"]
    assert result["missing_id_rows"] == 2
    assert result["roster_ids_with_multiple_rows"] == 1


def test_empty_roster_cannot_claim_zero_percent_coverage():
    result = identity_coverage({"rosters": pl.DataFrame(),
                                "player_stats": pl.DataFrame({"player_id": ["a"]})})
    assert result[0]["status"] == "not_evaluated"


def test_cli_preserves_partial_results_after_feed_failure(tmp_path, monkeypatch):
    import json
    import sys
    from gridiron_value import coverage_audit as audit

    (tmp_path / "pyproject.toml").write_text("")
    code = tmp_path / "coverage_audit.py"
    code.write_text("# test code snapshot")
    monkeypatch.setattr(audit, "__file__", str(code))
    monkeypatch.setattr(audit, "update_config", lambda **kwargs: None)
    def fail(*args, **kwargs):
        raise RuntimeError("simulated unavailable feed")
    monkeypatch.setattr(audit.nfl, "load_pbp", fail)
    monkeypatch.setattr(audit.nfl, "load_rosters", lambda *a, **kw:
                        pl.DataFrame({"season": [2026], "gsis_id": ["a"]}))
    monkeypatch.setattr(sys, "argv", ["coverage_audit", "--season", "2026",
                        "--project-root", str(tmp_path), "--feeds", "pbp", "rosters"])
    audit.main()
    manifest = next(tmp_path.glob("reports/tables/coverage_*/coverage_manifest.json"))
    state = json.loads(manifest.read_text())
    assert state["status"] == "completed_with_errors"
    assert state["feeds"]["pbp"]["status"] == "error"
    assert state["feeds"]["rosters"]["status"] == "available"
    assert (tmp_path / state["feeds"]["rosters"]["snapshot"]["path"]).exists()