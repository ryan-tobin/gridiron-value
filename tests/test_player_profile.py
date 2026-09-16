
import json

import polars as pl
import pytest

from gridiron_value import historical as h
from gridiron_value.player_profile import (
    build,
    combine_games,
    profiles_from_games,
    render_profile,
)


def inputs():
    common = {"season": 2026, "team": "PHI", "position": "QB"}
    metrics = pl.DataFrame(
        [
            {
                **common,
                "gsis_id": "p1",
                "game_id": "g1",
                "week": 1,
                "season_type": "REG",
                "player_display_name": "Example QB",
                "attempts": 10,
                "completions": 8,
                "passing_yards": 100,
            },
            {
                **common,
                "gsis_id": "p1",
                "game_id": "g2",
                "week": 2,
                "season_type": "REG",
                "player_display_name": "Example QB",
                "attempts": 30,
                "completions": 12,
                "passing_yards": 150,
            },
            {
                **common,
                "gsis_id": "p2",
                "game_id": "g1",
                "week": 1,
                "season_type": "REG",
                "player_display_name": "Production only",
                "attempts": 0,
                "completions": 0,
                "passing_yards": 0,
            },
        ]
    )
    participation = pl.DataFrame(
        [
            {
                **common,
                "resolved_gsis_id": "p1",
                "game_id": "g1",
                "week": 1,
                "game_type": "REG",
                "identity_status": "resolved",
                "has_player_stats": True,
                "offense_snaps": 20,
                "defense_snaps": 0,
                "st_snaps": 0,
                "player": "Example QB",
            },
            {
                **common,
                "resolved_gsis_id": "p1",
                "game_id": "g2",
                "week": 2,
                "game_type": "REG",
                "identity_status": "resolved_override",
                "has_player_stats": True,
                "offense_snaps": 40,
                "defense_snaps": 0,
                "st_snaps": 0,
                "player": "Example QB",
            },
            {
                **common,
                "resolved_gsis_id": "p3",
                "game_id": "g1",
                "week": 1,
                "game_type": "REG",
                "identity_status": "resolved",
                "has_player_stats": False,
                "offense_snaps": 60,
                "defense_snaps": 0,
                "st_snaps": 2,
                "player": "Snap only",
                "position": "T",
            },
        ]
    )
    return metrics, participation


def test_union_preserves_both_unmatched_populations_and_uses_ratio_of_totals():
    profiles = profiles_from_games(combine_games(*inputs()), 2026)
    by_id = {p["gsis_id"]: p for p in profiles}
    assert set(by_id) == {"p1", "p2", "p3"}
    assert by_id["p1"]["rates"]["completion_rate"]["value"] == 0.5
    assert by_id["p1"]["totals"]["offense_snaps"]["value"] == 60
    assert by_id["p2"]["totals"]["offense_snaps"]["value"] is None
    assert by_id["p2"]["rates"]["completion_rate"]["value"] is None
    assert by_id["p3"]["totals"]["attempts"]["observed_sum"] is None
    assert by_id["p3"]["totals"]["offense_snaps"]["value"] == 60


def test_missing_game_keeps_partial_sum_and_pairs_rate_inputs():
    metrics, participation = inputs()
    metrics = metrics.with_columns(
        pl.when(pl.col("week") == 2)
        .then(None)
        .otherwise(pl.col("completions"))
        .alias("completions")
    )
    profile = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p1"
    )[0]
    assert profile["totals"]["completions"]["value"] is None
    assert profile["totals"]["completions"]["observed_sum"] == 8
    rate = profile["rates"]["completion_rate"]
    assert rate["value"] is None
    assert rate["observed_value"] == 0.8
    assert (
        rate["denominator"] == 10
    )  # Excludes the 30-attempt game with missing completions.
    assert rate["observed_games"] == 1


def test_nonfinite_fields_are_missing_and_ngs_is_game_only():
    metrics, participation = inputs()
    metrics = metrics.with_columns(
        pl.lit(float("nan")).alias("passing_yards"),
        pl.lit(2.5).alias("ngs_passing_avg_time_to_throw"),
    )
    profile = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p1"
    )[0]
    assert profile["totals"]["passing_yards"]["value"] is None
    assert "ngs_passing_avg_time_to_throw" not in profile["totals"]
    assert profile["games"][0]["context"]["ngs_passing_avg_time_to_throw"] == 2.5
    json.dumps(profile, allow_nan=False)


def test_duplicate_keys_and_mismatched_week_fail():
    metrics, participation = inputs()
    with pytest.raises(ValueError, match="Duplicate"):
        combine_games(pl.concat([metrics, metrics.head(1)]), participation)
    with pytest.raises(ValueError, match="Conflicting week"):
        combine_games(metrics, participation.with_columns(pl.lit(9).alias("week")))


def test_season_type_filter_and_traded_team_history():
    metrics, participation = inputs()
    games = combine_games(metrics, participation)
    games.append({**games[0], "season_type": "POST", "game_id": "post1", "week": 1})
    next(g for g in games if g["game_id"] == "g2")["team"] = "NYJ"
    profile = profiles_from_games(games, 2026, gsis_id="p1")[0]
    assert profile["games_observed"] == 2
    assert profile["teams"] == ["NYJ", "PHI"]
    assert len(profiles_from_games(games, 2026, "POST")) == 1


def test_identity_flags_and_production_flag_must_agree():
    metrics, participation = inputs()
    with pytest.raises(ValueError, match="unresolved"):
        combine_games(
            metrics,
            participation.with_columns(pl.lit("conflict").alias("identity_status")),
        )
    with pytest.raises(ValueError, match="production flag"):
        combine_games(
            metrics, participation.with_columns(pl.lit(False).alias("has_player_stats"))
        )


def saved_inputs(root):
    metrics, participation = inputs()
    metric_file, snap_file = root / "metrics.parquet", root / "participation.parquet"
    metrics.write_parquet(metric_file)
    participation.write_parquet(snap_file)
    source = {"path": "player_game_manifest.json", "sha256": "shared-reference"}
    part_path, metric_path = root / "participation.json", root / "metrics.json"
    h.save_json(
        part_path,
        {
            "season": 2026,
            "source_player_game_manifest": source,
            "files": {"participation": h.record(root, snap_file)},
        },
    )
    h.save_json(
        metric_path,
        {
            "season": 2026,
            "source_player_game_manifest": source,
            "source_participation_manifest": h.record(root, part_path),
            "files": {"all": h.record(root, metric_file)},
        },
    )
    return metric_path, part_path


def test_end_to_end_outputs_are_checksummed_and_linked(tmp_path):
    paths = saved_inputs(tmp_path)
    output = build(tmp_path, *paths, 2026)
    manifest = h.read_json(output / "profile_manifest.json")
    assert manifest["profile_count"] == 3
    for record in manifest["files"].values():
        h.verify(tmp_path, record)
    index = (output / "index.html").read_text()
    for filename in manifest["files"]:
        if filename.startswith("player_"):
            assert filename in index
    assert (output / "profiles.json").exists()


def test_tampered_inputs_fail_before_output(tmp_path):
    paths = saved_inputs(tmp_path)
    with (tmp_path / "metrics.parquet").open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="Checksum"):
        build(tmp_path, *paths, 2026)
    assert not (tmp_path / "reports").exists()


def test_mismatched_manifest_lineage_fails_before_output(tmp_path):
    metric_path, part_path = saved_inputs(tmp_path)
    state = h.read_json(metric_path)
    state["source_player_game_manifest"] = {
        "path": "different.json",
        "sha256": "different",
    }
    h.save_json(metric_path, state)
    with pytest.raises(ValueError, match="different player-game"):
        build(tmp_path, metric_path, part_path, 2026)


def test_html_escapes_source_text_and_handles_snap_only_profile():
    profiles = profiles_from_games(combine_games(*inputs()), 2026)
    profile = next(p for p in profiles if p["gsis_id"] == "p3")
    profile["name"] = "<script>alert(1)</script>"
    markup = render_profile(profile)
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;" in markup
    assert "Production available: 0 games" in markup
    assert "Unavailable" in markup


def test_profile_accepts_existing_pipeline_outputs():
    from gridiron_value.participation import build_participation
    from gridiron_value.position_metrics import derive_metrics

    stats, snaps = inputs()
    # Recreate the existing producer schemas rather than just hand-crafted profile rows.
    participation, _ = build_participation(snaps.drop("has_player_stats"), stats)
    metrics = derive_metrics(stats)
    profiles = profiles_from_games(combine_games(metrics, participation), 2026)
    assert len(profiles) == 3
    qb = next(p for p in profiles if p["gsis_id"] == "p1")
    assert qb["rates"]["completion_rate"]["value"] == 0.5
    assert "50.0%" in render_profile(qb)


def test_defender_summary_has_relevant_zeros_without_offensive_clutter():
    metrics, participation = inputs()
    metrics = metrics.with_columns(
        pl.lit("DE").alias("position"),
        *[pl.lit(0).alias(c) for c in ("attempts", "completions", "passing_yards")],
        pl.lit(2).alias("def_sacks"),
        pl.lit(0).alias("def_interceptions"),
    )
    profile = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p1"
    )[0]
    markup = render_profile(profile)
    summary = markup.split("<h2>Game log</h2>")[0]
    assert "<h2>Defense</h2>" in summary
    assert "<td>Interceptions</td><td>0</td>" in summary
    assert "<td>QB hits</td><td>Unavailable</td>" in summary
    assert "<td>Pass attempts</td>" not in summary
    assert "<td>Carries</td>" not in summary
    assert "<td>Targets</td>" not in summary
    efficiency = summary.split("<h2>Efficiency</h2>")[1]
    assert "<table>" not in efficiency
    assert "Defensive efficiency metrics are not implemented" in efficiency
    assert "<summary>Rate definitions</summary>" not in markup
    assert "<summary>Next Gen Stats units</summary>" not in markup


def test_qb_zero_interceptions_and_missing_epa_stay_distinct():
    metrics, participation = inputs()
    metrics = metrics.with_columns(pl.lit(0).alias("passing_interceptions"))
    profile = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p1"
    )[0]
    summary = render_profile(profile).split("<h2>Game log</h2>")[0]
    assert "<td>Passing interceptions</td><td>0</td>" in summary
    assert "<td>Passing EPA</td><td>Unavailable</td>" in summary
    assert "<td>Targets</td>" not in summary


def test_off_role_activity_is_retained_even_when_season_sum_is_zero():
    metrics, participation = inputs()
    metrics = metrics.with_columns(
        pl.lit("WR").alias("position"),
        pl.when(pl.col("week") == 1).then(5).otherwise(-5).alias("rushing_yards"),
    )
    profile = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p1"
    )[0]
    summary = render_profile(profile).split("<h2>Game log</h2>")[0]
    assert "<h2>Rushing</h2>" in summary
    assert "<td>Rushing yards</td><td>0</td>" in summary
    assert "<h2>Passing</h2>" in summary  # Trick-play passing is also retained.
    single_game = profiles_from_games(
        combine_games(metrics, participation), 2026, gsis_id="p2"
    )[0]
    assert "Production available: 1 game." in render_profile(single_game)
