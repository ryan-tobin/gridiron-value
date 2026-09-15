"""Check batch recovery and assembly without downloading NFL data."""

import csv
from unittest.mock import patch

import pytest

from gridiron_value import historical as h


def source(root, season):
    raw = root / f"raw_{season}.parquet"
    raw.write_bytes(b"test snapshot; pipeline calls are mocked")
    manifest = root / f"raw_{season}.json"
    h.save_json(
        manifest,
        {
            "season_requested": season,
            "files": {"raw": h.record(root, raw)},
        },
    )
    return manifest


def completed(root, season, duplicate=False):
    report = root / f"players_{season}.csv"
    rows = [
        {
            "season": season,
            "dropback_player_id": "00-001",
            "dropbacks": 200,
            "pass_plus": 115.125,
        },
        {
            "season": season,
            "dropback_player_id": "00-001" if duplicate else "00-002",
            "dropbacks": 1,
            "pass_plus": 80.25,
        },
    ]
    h.save_csv(report, rows)
    reference = {
        "season": season,
        "participants": 2,
        "dropbacks": 201,
        "league_mean_adjusted_epa_per_dropback": season / 100000,
        "weighted_sd_of_player_rates": 0.18,
    }
    payloads = {
        "cohort": {},
        "schedule": {"dropbacks": 201},
        "variability": {},
        "reporting": {"reference": reference, "report": h.record(root, report)},
    }
    stages = {}
    for stage, payload in payloads.items():
        path = root / f"{season}_{stage}.json"
        h.save_json(path, payload)
        stages[stage] = h.record(root, path)
    return {"season": season, "stages": stages}


def test_duplicate_raw_seasons_are_rejected(tmp_path):
    path = source(tmp_path, 2019)
    with pytest.raises(ValueError, match="exactly one"):
        h.select_inputs(tmp_path, [path, path])


def test_combine_preserves_season_references_and_small_samples(tmp_path):
    entries = [completed(tmp_path, year) for year in (2019, 2025)]
    rows, references = h.collect(tmp_path, entries)
    assert len(rows) == 4
    assert [row["season"] for row in references] == [2019, 2025]
    assert (
        rows[0]["reference_mean_adjusted_epa_db"]
        != rows[2]["reference_mean_adjusted_epa_db"]
    )
    assert rows[0]["dropback_player_id"] == "00-001"
    assert rows[0]["pass_plus"] == "115.125"
    assert rows[1]["dropbacks"] == "1"


def test_duplicate_player_season_is_rejected(tmp_path):
    entry = completed(tmp_path, 2019, duplicate=True)
    with pytest.raises(ValueError, match="duplicate player-season"):
        h.collect(tmp_path, [entry])


def test_modified_completed_report_is_rejected(tmp_path):
    entry = completed(tmp_path, 2019)
    (tmp_path / "players_2019.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        h.collect(tmp_path, [entry])


def test_resume_skips_completed_season_after_failure(tmp_path):
    inputs = h.select_inputs(tmp_path, [source(tmp_path, y) for y in (2019, 2025)])
    state = {"inputs": inputs, "completed": [], "output_dir": "reports"}
    checkpoint = tmp_path / "historical_manifest.json"

    def first_attempt(root, item, output_dir):
        if item["season"] == 2025:
            raise RuntimeError("simulated interruption")
        return completed(root, item["season"])

    with patch.object(h, "run_season", side_effect=first_attempt):
        with pytest.raises(RuntimeError, match="simulated interruption"):
            h.execute(tmp_path, state, checkpoint)
    saved = h.read_json(checkpoint)
    assert saved["status"] == "failed"
    assert [item["season"] for item in saved["completed"]] == [2019]

    def retry(root, item, output_dir):
        return completed(root, item["season"])

    with patch.object(h, "run_season", side_effect=retry) as run:
        h.execute(tmp_path, saved, checkpoint)
    assert run.call_count == 1
    assert run.call_args.args[1]["season"] == 2025
    assert h.read_json(checkpoint)["status"] == "completed"
    with (tmp_path / "reports/historical_pass_plus.csv").open(newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 4