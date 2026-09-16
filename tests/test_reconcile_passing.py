import polars as pl
import pytest

from gridiron_value import reconcile_passing as rp


def sample():
    base = {
        "season": 2026,
        "season_type": "REG",
        "game_id": "g1",
        "posteam": "A",
        "play_type": "pass",
        "qb_dropback": 1,
        "qb_scramble": 0,
        "qb_spike": 0,
        "qb_kneel": 0,
        "two_point_attempt": 0,
        "sack": 0,
        "penalty": 0,
        "pass_attempt": 1,
        "complete_pass": 0,
        "epa": 0.0,
        "qb_epa": 0.0,
        "passer_id": "p1",
        "passer": "Player",
        "passer_player_id": "p1",
        "passer_player_name": "Player",
        "rusher_player_id": None,
        "rusher_player_name": None,
        "desc": "synthetic audit play",
    }
    changes = [
        {"epa": -3.0, "qb_epa": 1.0, "complete_pass": 1},
        {"epa": -2.0, "qb_epa": -2.0, "sack": 1},
        {
            "epa": -0.1,
            "qb_epa": -0.1,
            "qb_spike": 1,
            "qb_dropback": 0,
            "play_type": "qb_spike",
            "passer_id": None,
        },
        {"epa": -0.5, "qb_epa": -0.5, "two_point_attempt": 1},
        {
            "epa": 0.4,
            "qb_epa": 0.4,
            "qb_scramble": 1,
            "play_type": "run",
            "pass_attempt": 0,
            "passer_player_id": None,
            "rusher_player_id": "p1",
        },
        {
            "epa": 1.0,
            "qb_epa": 1.0,
            "play_type": "no_play",
            "penalty": 1,
            "pass_attempt": 0,
            "passer_player_id": None,
        },
    ]
    pbp = pl.DataFrame(
        [{**base, **edit, "play_id": i} for i, edit in enumerate(changes)]
    )
    stats = pl.DataFrame(
        [
            {
                "season": 2026,
                "season_type": "REG",
                "game_id": "g1",
                "team": "A",
                "player_id": "p1",
                "attempts": 2,
                "completions": 1,
                "sacks_suffered": 1,
                "passing_epa": -1.6,
            }
        ]
    )
    return pbp, stats


def test_definitions_are_reconciled_separately():
    tables = rp.reconcile(*sample(), 2026)
    rows = {r["candidate"]: r for r in tables["comparisons"].to_dicts()}
    for name in ("attempts", "completions", "sacks", "source_qb_epa"):
        assert rows[name]["status"] == "match"
    assert rows["source_epa"]["pbp_value"] == pytest.approx(-5.6)
    assert rows["eligible_epa"]["pbp_value"] == pytest.approx(-4.6)
    assert rows["eligible_epa"]["status"] == "difference"
    assert tables["source_components"]["plays"].sum() == 4
    assert tables["play_ledger"].height == 6


def test_missing_stat_row_is_not_zero_filled():
    pbp, stats = sample()
    stats = stats.with_columns(pl.lit("someone_else").alias("player_id"))
    out = rp.reconcile(pbp, stats, 2026)["comparisons"]
    row = out.filter(
        (pl.col("player_id") == "p1") & (pl.col("candidate") == "attempts")
    ).row(0, named=True)
    assert row["status"] == "missing_stats_row"
    assert row["stat_value"] is None and row["pbp_minus_stat"] is None


def test_missing_qb_epa_withholds_sum_without_changing_cohort():
    pbp, stats = sample()
    pbp = pbp.with_columns(
        pl.when(pl.col("play_id") == 0)
        .then(None)
        .otherwise(pl.col("qb_epa"))
        .alias("qb_epa")
    )
    out = rp.reconcile(pbp, stats, 2026)["comparisons"]
    row = out.filter(pl.col("candidate") == "source_qb_epa").row(0, named=True)
    assert row["status"] == "incomplete_pbp"
    assert row["pbp_value"] is None and row["invalid_pbp_values"] == 1


def test_null_flags_are_not_silently_treated_as_zero():
    pbp, stats = sample()
    pbp = pbp.with_columns(
        pl.when(pl.col("play_id") == 0)
        .then(None)
        .otherwise(pl.col("pass_attempt"))
        .alias("pass_attempt")
    )
    out = rp.reconcile(pbp, stats, 2026)["comparisons"]
    assert (
        out.filter(pl.col("candidate") == "attempts")["status"].item()
        == "incomplete_pbp"
    )


def test_unattributed_rows_are_preserved():
    pbp, stats = sample()
    stats = stats.with_columns(pl.lit(None, dtype=pl.String).alias("player_id"))
    tables = rp.reconcile(pbp, stats, 2026)
    assert tables["unattributed_stats"].height == 1
    assert set(tables["comparisons"]["status"]) == {"missing_stats_row"}


def test_duplicate_stats_and_wrong_season_fail():
    pbp, stats = sample()
    with pytest.raises(ValueError, match="Duplicate"):
        rp.reconcile(pbp, pl.concat([stats, stats]), 2026)
    with pytest.raises(ValueError, match="season"):
        rp.reconcile(pbp, stats, 2025)


def test_checksum_failure_creates_no_audit(tmp_path):
    from gridiron_value import historical as h

    pbp, stats = sample()
    feeds = {}
    for name, frame in (("pbp", pbp), ("player_stats", stats)):
        path = tmp_path / f"{name}.parquet"
        frame.write_parquet(path)
        feeds[name] = {"status": "available", "snapshot": h.record(tmp_path, path)}
    manifest = tmp_path / "coverage.json"
    h.save_json(manifest, {"season": 2026, "feeds": feeds})
    (tmp_path / "pbp.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Checksum"):
        rp.build(tmp_path, manifest, 2026)
    assert not (tmp_path / "reports").exists()
