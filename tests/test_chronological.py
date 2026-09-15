from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from gridiron_value.adjustment import MODEL_NAMES
from gridiron_value.chronological import evaluate_weeks


def cohort():
    return pl.DataFrame([
        {"season": 2025, "week": week,
         "game_date": str(date(2025, 9, 1) + timedelta(weeks=week - 1)),
         "game_id": f"g{week}", "play_id": play,
         "posteam": "A" if play % 2 else "B",
         "defteam": "B" if play % 2 else "A",
         "epa": float(week + play) / 10}
        for week in range(1, 7) for play in range(1, 5)
    ])


def test_future_and_test_outcomes_do_not_change_current_predictions():
    frame = cohort()
    original, _ = evaluate_weeks(frame, 3)
    altered = frame.with_columns(
        pl.when(pl.col("week") >= 4).then(pl.col("epa") + 100)
        .otherwise(pl.col("epa")).alias("epa")
    )
    changed, _ = evaluate_weeks(altered, 3)
    for name in MODEL_NAMES:
        np.testing.assert_allclose(
            original.filter(pl.col("week") <= 4)[f"pred_{name}"],
            changed.filter(pl.col("week") <= 4)[f"pred_{name}"],
        )


def test_training_mean_and_disjoint_windows():
    predictions, audit = evaluate_weeks(cohort(), 3)
    assert predictions.height == 16
    assert audit["test_week"].to_list() == [3, 4, 5, 6]
    assert audit["train_dropbacks"].to_list() == [8, 12, 16, 20]
    for row in audit.to_dicts():
        assert row["train_last_date"] < row["test_first_date"]
    expected = cohort().filter(pl.col("week") < 3)["epa"].mean()
    np.testing.assert_allclose(predictions.filter(pl.col("week") == 3)["pred_league_mean"], expected)


def test_overlapping_dates_rejected():
    bad = cohort().with_columns(pl.lit("2025-09-01").alias("game_date"))
    with pytest.raises(ValueError, match="dates overlap"):
        evaluate_weeks(bad, 3)


def test_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="Duplicate play"):
        evaluate_weeks(pl.concat([cohort(), cohort().head(1)]), 3)


def test_nonfinite_target_rejected():
    bad = cohort().with_columns(pl.lit(float("nan")).alias("epa"))
    with pytest.raises(ValueError, match="finite"):
        evaluate_weeks(bad, 3)


def test_unseen_teams_are_reported_and_predictions_are_finite():
    frame = cohort().with_columns(
        pl.when(pl.col("week") == 3).then(pl.lit("NEW"))
        .otherwise(pl.col("posteam")).alias("posteam")
    )
    predictions, audit = evaluate_weeks(frame, 3)
    assert audit["unseen_offense_plays"][0] == 4
    for name in MODEL_NAMES:
        assert np.isfinite(predictions[f"pred_{name}"].to_numpy()).all()