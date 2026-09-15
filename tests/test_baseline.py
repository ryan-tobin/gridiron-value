import polars as pl
import pytest

from gridiron_value.baseline import passing_baseline


@pytest.fixture
def sample_cohort():
    return pl.DataFrame(
        {
            "season": [2025, 2025, 2025, 2025, 2024, 2024],
            "dropback_player_id": ["A", "A", "A", "B", "A", "B"],
            "dropback_player_name": [
                "Player A",
                "Player A",
                "Player A",
                "Player B",
                "Player A",
                "Player B",
            ],
            "game_id": ["g1", "g1", "g2", "g2", "h1", "h1"],
            "epa": [1.0, -1.0, 0.0, 2.0, 3.0, 3.0],
            "sack": [0, 1, 0, 0, 0, 0],
            "qb_scramble": [0, 0, 1, 0, 0, 0],
            "penalty": [0, 0, 0, 0, 0, 0],
            "identity_source": ["passer_id"] * 6,
        }
    )


def test_league_reference_weights_plays_and_separates_seasons(sample_cohort):
    result = passing_baseline(sample_cohort)

    season_2025 = result.filter(pl.col("season") == 2025)
    season_2024 = result.filter(pl.col("season") == 2024)

    # In 2025: (1 - 1 + 0 + 2) / 4 = 0.5.
    # Averaging player means instead would incorrectly produce 1.0.
    assert season_2025["league_epa_per_dropback"].to_list() == [0.5, 0.5]
    assert season_2024["league_epa_per_dropback"].to_list() == [3.0, 3.0]


def test_sacks_and_scrambles_remain_in_denominator(sample_cohort):
    result = passing_baseline(sample_cohort)

    player = result.filter(
        (pl.col("season") == 2025) & (pl.col("dropback_player_id") == "A")
    ).row(0, named=True)

    assert player["dropbacks"] == 3
    assert player["games"] == 2
    assert player["sacks"] == 1
    assert player["scrambles"] == 1
    assert player["total_epa"] == pytest.approx(0.0)
    assert player["epa_per_dropback"] == pytest.approx(0.0)
    assert player["positive_epa_rate"] == pytest.approx(1 / 3)
    assert player["epa_above_average_per_dropback"] == pytest.approx(-0.5)
    assert player["epa_above_average"] == pytest.approx(-1.5)


def test_aggregation_preserves_totals_and_balances_each_season(sample_cohort):
    result = passing_baseline(sample_cohort)

    assert result["dropbacks"].sum() == sample_cohort.height
    assert result["total_epa"].sum() == pytest.approx(sample_cohort["epa"].sum())

    for season in (2024, 2025):
        season_result = result.filter(pl.col("season") == season)
        assert season_result["epa_above_average"].sum() == pytest.approx(0.0, abs=1e-10)
