import polars as pl
import pytest

from gridiron_value.cohort import resolve_players


@pytest.mark.parametrize(
    ("standard_id", "scramble", "runner_id", "expected"),
    [
        ("QB-1", 0, None, "QB-1"),
        ("QB-1", 1, "QB-1", "QB-1"),
        (None, 1, "WR-1", "WR-1"),
        (None, 0, "RB-1", None),
        (None, 1, None, None),
        (" ", 1, "WR-1", "WR-1"),
    ],
)
def test_player_resolution(standard_id, scramble, runner_id, expected):
    frame = pl.DataFrame(
        {
            "passer_id": [standard_id],
            "passer": [None],
            "passer_player_id": [None],
            "passer_player_name": [None],
            "rusher_player_id": [runner_id],
            "rusher_player_name": [None],
            "qb_scramble": [scramble],
        }
    )

    result = resolve_players(frame)

    assert result["dropback_player_id"].item() == expected


def test_conflicting_ids_stop_resolution():
    frame = pl.DataFrame(
        {
            "passer_id": ["QB-1"],
            "passer": ["Quarterback"],
            "passer_player_id": [None],
            "passer_player_name": [None],
            "rusher_player_id": ["OTHER-PLAYER"],
            "rusher_player_name": ["Other"],
            "qb_scramble": [1],
        }
    )

    with pytest.raises(ValueError, match="player-ID conflicts"):
        resolve_players(frame)
