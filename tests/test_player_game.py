import polars as pl
import pytest

from gridiron_value.player_game import build_player_game


def test_join_preserves_stats_and_leaves_unmatched_snaps_audit():
    stats = pl.DataFrame({"season": [2026], "week": [1], "game_id": ["g1"], "team": ["A"], "player_id": ["p1"], "yards": [10]})
    snaps = pl.DataFrame({"season": [2026, 2026], "game_id": ["g1", "g1"], "team": ["A", "A"], "resolved_gsis_id": ["p1", "p2"], "identity_status": ["resolved", "resolved"], "offense_snaps": [20, 5]})
    ngs = {"passing": pl.DataFrame(), "rushing": pl.DataFrame(), "receiving": pl.DataFrame()}
    joined, anonymous, unmatched = build_player_game(stats, snaps, ngs)
    assert joined.height == 1 and joined["offense_snaps"][0] == 20
    assert anonymous.height == 0 and unmatched.height == 1


def test_week_zero_ngs_is_excluded():
    stats = pl.DataFrame({"season": [2026], "week": [1], "game_id": ["g1"], "team": ["A"], "player_id": ["p1"]})
    snaps = pl.DataFrame({"season": [2026], "game_id": ["g1"], "team": ["A"], "resolved_gsis_id": ["p1"], "identity_status": ["resolved"]})
    ngs = {"passing": pl.DataFrame({"season": [2026, 2026], "week": [0, 1], "team_abbr": ["A", "A"], "player_gsis_id": ["p1", "p1"], "avg_time_to_throw": [9, 2]}), "rushing": pl.DataFrame(), "receiving": pl.DataFrame()}
    joined, _, _ = build_player_game(stats, snaps, ngs)
    assert joined["ngs_passing_avg_time_to_throw"][0] == 2


def test_duplicate_snap_keys_rejected():
    stats = pl.DataFrame({"season": [2026], "week": [1], "game_id": ["g1"], "team": ["A"], "player_id": ["p1"]})
    snaps = pl.DataFrame({"season": [2026, 2026], "game_id": ["g1", "g1"], "team": ["A", "A"], "resolved_gsis_id": ["p1", "p1"], "identity_status": ["resolved", "resolved"]})
    with pytest.raises(ValueError, match="duplicate"):
        build_player_game(stats, snaps, {"passing": pl.DataFrame(), "rushing": pl.DataFrame(), "receiving": pl.DataFrame()})
