import json

import polars as pl
import pytest

from gridiron_value import current_dropbacks as cd
from gridiron_value import historical as h


def raw():
    rows = []
    for i, epa in enumerate([1.0, 3.0, -2.0, 0.0]):
        rows.append(
            {
                "season": 2026,
                "season_type": "REG",
                "game_id": "g1" if i < 3 else "g2",
                "play_id": i,
                "week": 1,
                "game_date": "2026-09-10",
                "posteam": "A",
                "defteam": "B",
                "play_type": "pass",
                "qb_dropback": 1,
                "qb_scramble": 0,
                "qb_spike": 0,
                "qb_kneel": 0,
                "two_point_attempt": 0,
                "sack": 0,
                "penalty": 0,
                "epa": epa,
                "passer_id": "p1",
                "passer": "Player",
                "passer_player_id": "p1",
                "passer_player_name": "Player",
                "rusher_player_id": None,
                "rusher_player_name": None,
                "down": 1,
                "ydstogo": 3,
                "yardline_100": 20,
                "score_differential": 0,
            }
        )
    return pl.DataFrame(rows)


def fixture(root, frame=None):
    (root / "src/gridiron_value").mkdir(parents=True)
    # build records the installed code, so integration fixtures use a real project root below.
    path = root / "pbp.parquet"
    (raw() if frame is None else frame).write_parquet(path)
    manifest = root / "coverage.json"
    h.save_json(
        manifest,
        {
            "season": 2026,
            "feeds": {"pbp": {"status": "available", "snapshot": h.record(root, path)}},
        },
    )
    return manifest


def test_unknown_context_and_boundary_labels():
    data = pl.DataFrame(
        {
            "down": [1.0, 4.0, 0.0, None],
            "ydstogo": [3.0, 7.0, 8.0, float("nan")],
            "yardline_100": [20.0, 50.0, 100.0, -1.0],
            "score_differential": [-1.0, 0.0, 1.0, None],
        }
    )
    labeled = cd.labels(data)
    assert labeled["situation_distance"].to_list() == [
        "short",
        "medium",
        "long",
        "unknown",
    ]
    assert labeled["situation_field_position"].to_list() == [
        "red_zone",
        "opponent_half_outside_red_zone",
        "own_half",
        "unknown",
    ]
    assert labeled["situation_score_state"].to_list() == [
        "trailing",
        "tied",
        "leading",
        "unknown",
    ]
    assert labeled["situation_down"].to_list() == ["1", "4", "unknown", "unknown"]
    assert cd.labels(pl.DataFrame({"x": [1]}))["situation_distance"].item() == "unknown"


def test_splits_conserve_counts_epa_and_use_play_weighting():
    cohort, _ = cd.build_cohort(raw())
    tables = cd.summarize(cohort)
    assert tables["player_totals"]["epa_per_eligible_dropback"].item() == 0.5
    for scope in ("player", "team"):
        splits = tables[scope + "_situations"]
        for dimension in cd.CONTEXT:
            subset = splits.filter(pl.col("dimension") == dimension)
            assert subset["eligible_dropbacks"].sum() == 4
            assert subset["total_epa"].sum() == 2


def test_season_and_checksum_fail_before_output(tmp_path):
    manifest = fixture(tmp_path)
    with pytest.raises(ValueError, match="season"):
        cd.build(tmp_path, manifest, 2025)
    (tmp_path / "pbp.parquet").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Checksum"):
        cd.build(tmp_path, manifest, 2026)
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    "change,match",
    [
        ({"season": 2025}, "season"),
        ({"week": 0}, "positive"),
        ({"posteam": ""}, "Blank"),
    ],
)
def test_invalid_input_fails_before_output(tmp_path, change, match):
    frame = raw().with_columns(*(pl.lit(v).alias(k) for k, v in change.items()))
    manifest = fixture(tmp_path, frame)
    with pytest.raises(ValueError, match=match):
        cd.build(tmp_path, manifest, 2026)
    assert not (tmp_path / "reports").exists()


def test_reg_only_scramble_fallback_and_unidentified_fail():
    frame = raw().with_columns(
        pl.when(pl.col("play_id") == 0)
        .then(pl.lit("POST"))
        .otherwise(pl.col("season_type"))
        .alias("season_type")
    )
    cohort, ex = cd.build_cohort(frame)
    assert cohort.height == 3 and ex[0]["excluded"] == 1
    scramble = (
        raw()
        .head(1)
        .with_columns(
            pl.lit(None, dtype=pl.String).alias("passer_id"),
            pl.lit(1).alias("qb_scramble"),
            pl.lit("runner").alias("rusher_player_id"),
        )
    )
    assert cd.build_cohort(scramble)[0]["dropback_player_id"].item() == "runner"
    with pytest.raises(ValueError, match="unidentified"):
        cd.build_cohort(
            raw().with_columns(pl.lit(None, dtype=pl.String).alias("passer_id"))
        )


def test_build_records_inputs_outputs_and_no_pass_plus(tmp_path, monkeypatch):
    manifest = fixture(tmp_path)
    # Record bundled test copies as code artifacts inside the test project.
    from pathlib import Path

    code = Path(cd.__file__)
    for name in ("current_dropbacks.py", "cohort.py", "historical.py"):
        (tmp_path / "src/gridiron_value" / name).write_bytes(
            code.with_name(name).read_bytes()
        )
    monkeypatch.setattr(
        cd, "__file__", str(tmp_path / "src/gridiron_value/current_dropbacks.py")
    )
    result = cd.build(tmp_path, manifest, 2026)
    state = json.loads(result.read_text())
    assert state["rows"] == 4 and state["observed_weeks"] == [1]
    assert state["chronological_policy"]["has_test_week"] is False
    for record in state["files"].values():
        h.verify(tmp_path, record)
    assert state["source_coverage_manifest"] == h.record(tmp_path, manifest)
    assert (
        "pass_plus"
        not in pl.read_csv(tmp_path / state["files"]["player_totals"]["path"]).columns
    )


@pytest.mark.parametrize("second_team", ["A", "C"])
def test_player_game_totals_preserve_keys_and_weighting(second_team):
    frame = raw().with_columns(
        pl.when(pl.col("game_id") == "g2")
        .then(pl.lit(second_team))
        .otherwise(pl.col("posteam"))
        .alias("posteam")
    )
    cohort, _ = cd.build_cohort(frame)
    tables = cd.summarize(cohort)
    games = tables["player_game_totals"].sort("game_id")

    assert games.select(
        "season", "season_type", "game_id", "team", "gsis_id"
    ).rows() == [
        (2026, "REG", "g1", "A", "p1"),
        (2026, "REG", "g2", second_team, "p1"),
    ]

    assert games["eligible_dropbacks"].to_list() == [3, 1]
    assert games["total_epa"].to_list() == [2.0, 0.0]
    assert games["epa_per_eligible_dropback"].to_list() == pytest.approx([2 / 3, 0.0])
    assert games["positive_epa_rate"].to_list() == pytest.approx([2 / 3, 0.0])
    assert games["games_observed"].to_list() == [1, 1]

    season = tables["player_totals"].row(0, named=True)

    assert games["eligible_dropbacks"].sum() == season["eligible_dropbacks"]
    assert games["total_epa"].sum() == pytest.approx(season["total_epa"])

    weighted_rate = games["total_epa"].sum() / games["eligible_dropbacks"].sum()

    assert weighted_rate == pytest.approx(season["epa_per_eligible_dropback"])
    assert weighted_rate == pytest.approx(0.5)
    assert games["epa_per_eligible_dropback"].mean() != pytest.approx(weighted_rate)
