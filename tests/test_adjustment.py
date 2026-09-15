import numpy as np
import polars as pl
import pytest

from gridiron_value.adjustment import evaluate_models, summarize_errors


@pytest.fixture
def synthetic_cohort():
    rows = []

    # Balanced combinations of two offenses and two defenses.
    for game in range(12):
        offense = game % 2
        defense = (game // 2) % 2

        offense_effect = 0.3 if offense == 0 else -0.3
        defense_effect = -0.6 if defense == 0 else 0.6

        for play in range(4):
            rows.append(
                {
                    "season": 2025,
                    "game_id": f"game_{game:02d}",
                    "play_id": play,
                    "posteam": f"O{offense}",
                    "defteam": f"D{defense}",
                    "epa": offense_effect + defense_effect,
                }
            )

    return pl.DataFrame(rows)


def test_each_game_stays_in_one_fold(synthetic_cohort):
    predictions = evaluate_models(synthetic_cohort, alpha=1.0, n_splits=3)

    folds_per_game = predictions.group_by("game_id").agg(
        pl.col("fold").n_unique().alias("fold_count")
    )

    assert predictions.height == synthetic_cohort.height
    assert predictions["fold"].n_unique() == 3
    assert folds_per_game["fold_count"].to_list() == [1] * 12


def test_model_recovers_known_defense_signal(synthetic_cohort):
    predictions = evaluate_models(synthetic_cohort, alpha=1e-6, n_splits=3)

    scores = {
        row["model"]: row["mse"] for row in summarize_errors(predictions).to_dicts()
    }

    assert scores["offense_defense"] < 1e-8
    assert scores["offense_defense"] < scores["offense_only"]


def test_held_out_outcomes_cannot_change_their_own_predictions(
    synthetic_cohort,
):
    original = evaluate_models(synthetic_cohort, alpha=1.0, n_splits=3)
    held_out_games = original.filter(pl.col("fold") == 0)["game_id"].unique().to_list()

    changed_data = synthetic_cohort.with_columns(
        pl.when(pl.col("game_id").is_in(held_out_games))
        .then(pl.col("epa") + 100.0)
        .otherwise(pl.col("epa"))
        .alias("epa")
    )
    changed = evaluate_models(changed_data, alpha=1.0, n_splits=3)

    for name in ("league_mean", "offense_only", "offense_defense"):
        before = original.filter(pl.col("fold") == 0)[f"pred_{name}"].to_numpy()
        after = changed.filter(pl.col("fold") == 0)[f"pred_{name}"].to_numpy()

        np.testing.assert_allclose(before, after)


def test_equal_penalties_reproduce_original_model(synthetic_cohort):
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import OneHotEncoder

    from gridiron_value.adjustment import make_team_model

    teams = synthetic_cohort.select("posteam", "defteam").to_numpy()
    target = synthetic_cohort["epa"].to_numpy()

    original = make_pipeline(
        OneHotEncoder(handle_unknown="ignore", sparse_output=False),
        Ridge(alpha=100.0, solver="svd"),
    )
    revised = make_team_model(
        alpha=100.0,
        include_defense=True,
        defense_alpha=100.0,
    )

    original.fit(teams, target)
    revised.fit(teams, target)

    np.testing.assert_allclose(
        original.predict(teams),
        revised.predict(teams),
        rtol=1e-10,
        atol=1e-10,
    )


def test_stronger_defense_penalty_reduces_defense_contrast(synthetic_cohort):
    from gridiron_value.adjustment import make_team_model

    teams = synthetic_cohort.select("posteam", "defteam").to_numpy()
    target = synthetic_cohort["epa"].to_numpy()
    comparison = np.array([["O0", "D0"], ["O0", "D1"]])

    contrasts = []

    for penalty in (100.0, 1000.0):
        model = make_team_model(
            alpha=100.0,
            include_defense=True,
            defense_alpha=penalty,
        )
        model.fit(teams, target)
        predicted = model.predict(comparison)
        contrasts.append(abs(predicted[1] - predicted[0]))

    assert contrasts[1] < contrasts[0]


def test_defense_extraction_matches_prediction_contrast(synthetic_cohort):
    from gridiron_value.adjustment import make_team_model
    from gridiron_value.schedule import defense_contribution

    teams = synthetic_cohort.select("posteam", "defteam").to_numpy()
    target = synthetic_cohort["epa"].to_numpy()

    model = make_team_model(
        alpha=100.0,
        include_defense=True,
        defense_alpha=1000.0,
    )
    model.fit(teams, target)

    comparison = np.array([["O0", "D0"], ["O0", "D1"]])
    effects = defense_contribution(model, comparison)
    predictions = model.predict(comparison)

    assert effects[0] < effects[1]
    assert effects[1] - effects[0] == pytest.approx(predictions[1] - predictions[0])


def test_schedule_correction_sign_and_balance(synthetic_cohort):
    from gridiron_value.schedule import schedule_adjustments

    scored = schedule_adjustments(synthetic_cohort, n_splits=3)

    strong = scored.filter(pl.col("defteam") == "D0")
    weak = scored.filter(pl.col("defteam") == "D1")

    assert strong["schedule_adjustment"].mean() > 0
    assert weak["schedule_adjustment"].mean() < 0
    assert scored["schedule_adjustment"].sum() == pytest.approx(0.0, abs=1e-10)
    assert scored["opponent_adjusted_epa"].sum() == pytest.approx(scored["epa"].sum())


def test_defensive_term_excludes_its_own_game(synthetic_cohort):
    from gridiron_value.schedule import schedule_adjustments

    original = schedule_adjustments(synthetic_cohort, n_splits=3)
    held_out_games = (
        original.filter(pl.col("schedule_fold") == 0)["game_id"].unique().to_list()
    )

    changed_data = synthetic_cohort.with_columns(
        pl.when(pl.col("game_id").is_in(held_out_games))
        .then(pl.col("epa") + 100.0)
        .otherwise(pl.col("epa"))
        .alias("epa")
    )
    changed = schedule_adjustments(changed_data, n_splits=3)

    # Test the held-out defensive terms before full-season centering.
    before = original.filter(pl.col("schedule_fold") == 0)[
        "opponent_effect_oof"
    ].to_numpy()
    after = changed.filter(pl.col("schedule_fold") == 0)[
        "opponent_effect_oof"
    ].to_numpy()

    np.testing.assert_allclose(before, after)
