import polars as pl
import pytest

from gridiron_value.participation import build_participation


def test_participation_preserves_snap_only_rows_and_attaches_stats():
    snaps = pl.DataFrame({"season": [2026, 2026], "game_id": ["g1", "g1"], "team": ["A", "A"], "resolved_gsis_id": ["p1", "p2"], "identity_status": ["resolved", "resolved"], "offense_snaps": [20, 10]})
    stats = pl.DataFrame({"season": [2026], "game_id": ["g1"], "team": ["A"], "gsis_id": ["p1"], "player_id": ["p1"], "position": ["WR"], "receiving_yards": [30]})
    table, unresolved = build_participation(snaps, stats)
    assert table.height == 2
    assert table.filter(pl.col("has_player_stats")).height == 1
    assert table.filter(~pl.col("has_player_stats"))["resolved_gsis_id"][0] == "p2"
    assert unresolved is None


def test_duplicate_participation_keys_rejected():
    snaps = pl.DataFrame({"season": [2026, 2026], "game_id": ["g1", "g1"], "team": ["A", "A"], "resolved_gsis_id": ["p1", "p1"], "identity_status": ["resolved", "resolved"]})
    with pytest.raises(ValueError, match="duplicate"):
        build_participation(snaps, None)


def test_unresolved_rows_are_returned_unchanged():
    snaps = pl.DataFrame({"season": [2026], "game_id": ["g1"], "team": ["A"], "resolved_gsis_id": ["p1"], "identity_status": ["resolved"]})
    unresolved = pl.DataFrame({"pfr_player_id": ["unknown"], "identity_status": ["unmapped"]})
    _, result = build_participation(snaps, None, unresolved)
    assert result.equals(unresolved)
