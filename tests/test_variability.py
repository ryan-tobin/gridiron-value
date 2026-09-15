import numpy as np

from gridiron_value.variability import resample_rates


def test_unequal_game_volumes_use_ratio_of_totals():
    rates = resample_rates(
        epa_totals=np.array([1.0, 0.0]),
        dropbacks=np.array([1, 9]),
        n_resamples=1000,
        seed=7,
    )

    # Drawing both games gives 1 EPA / 10 dropbacks = 0.1.
    # Averaging their individual rates would incorrectly give 0.5.
    np.testing.assert_allclose(
        np.unique(rates),
        np.array([0.0, 0.1, 1.0]),
    )


def test_constant_rate_is_preserved_when_game_sizes_differ():
    rates = resample_rates(
        epa_totals=np.array([0.25, 2.5, 5.0]),
        dropbacks=np.array([1, 10, 20]),
        n_resamples=1000,
        seed=7,
    )

    np.testing.assert_allclose(rates, 0.25)
