import numpy as np
import polars as pl
import pytest

from gridiron_value.uncertainty import game_bootstrap, weighted_gain


def test_gain_preserves_play_weighting():
    weights = np.array([1.0, 9.0])
    offense = np.array([2.0, 2.0])
    joint = np.array([1.0, 2.1])

    # One play improves by 1.0; nine plays worsen by 0.1.
    # Net improvement per play: (1.0 - 0.9) / 10 = 0.01.
    result = weighted_gain(weights, offense, joint)

    assert result == pytest.approx(0.01)


def test_paired_bootstrap_preserves_constant_improvement():
    errors = pl.DataFrame(
        {
            "dropbacks": [10, 30, 80],
            "offense_only_mse": [1.0, 2.0, 4.0],
            "offense_defense_mse": [0.75, 1.75, 3.75],
        }
    )

    result = game_bootstrap(errors, n_resamples=100, seed=7)
    interval = result["conditional_percentile_95_interval"]

    assert result["mse_reduction"] == pytest.approx(0.25)
    assert interval["low"] == pytest.approx(0.25)
    assert interval["high"] == pytest.approx(0.25)
    assert result["games_improved"] == 3
