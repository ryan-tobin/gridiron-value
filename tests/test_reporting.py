import numpy as np
import polars as pl
import pytest

from gridiron_value.reporting import add_pass_plus


def test_index_uses_dropback_weighted_reference():
    leaderboard = pl.DataFrame(
        {
            "season": [2025, 2025],
            "dropback_player_id": ["A", "B"],
            "dropbacks": [75, 25],
            "opponent_adjusted_epa_per_dropback": [0.2, -0.2],
        }
    )

    result, reference = add_pass_plus(leaderboard)

    weights = result["dropbacks"].to_numpy()
    scores = result["pass_plus"].to_numpy()

    assert reference["league_mean_adjusted_epa_per_dropback"] == pytest.approx(0.1)
    assert reference["weighted_sd_of_player_rates"] == pytest.approx(np.sqrt(0.03))
    assert np.average(scores, weights=weights) == pytest.approx(100.0)
    assert np.sqrt(np.average((scores - 100.0) ** 2, weights=weights)) == pytest.approx(
        15.0
    )


def test_identical_rates_cannot_define_a_standardized_scale():
    leaderboard = pl.DataFrame(
        {
            "season": [2025, 2025],
            "dropback_player_id": ["A", "B"],
            "dropbacks": [75, 25],
            "opponent_adjusted_epa_per_dropback": [0.1, 0.1],
        }
    )

    with pytest.raises(ValueError, match="no variation"):
        add_pass_plus(leaderboard)
