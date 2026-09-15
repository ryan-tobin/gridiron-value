"""Evaluate an initial offense-and-defense EPA model on held-out games."""

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer

from gridiron_value.data import file_sha256, write_json

MODEL_NAMES = ("league_mean", "offense_only", "offense_defense")


def make_team_model(
    alpha: float,
    include_defense: bool,
    defense_alpha: float | None = None,
):
    """Build ridge regression with separate offense and defense penalties."""
    defense_alpha = alpha if defense_alpha is None else defense_alpha

    if (
        not np.isfinite(alpha)
        or not np.isfinite(defense_alpha)
        or alpha <= 0
        or defense_alpha <= 0
    ):
        raise ValueError("Both penalties must be finite and positive.")

    transformers = [
        (
            "offense",
            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
            [0],
        )
    ]
    weights = {"offense": 1.0}

    if include_defense:
        transformers.append(
            (
                "defense",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                [1],
            )
        )

        # Scaling by s makes the effective penalty alpha / s**2.
        weights["defense"] = float(np.sqrt(alpha / defense_alpha))

    features = ColumnTransformer(
        transformers,
        transformer_weights=weights,
    )

    return make_pipeline(
        features,
        Ridge(alpha=alpha, solver="svd"),
    )


def evaluate_models(
    frame: pl.DataFrame,
    alpha: float = 100.0,
    n_splits: int = 5,
    defense_alpha: float | None = None,
) -> pl.DataFrame:
    """Generate predictions with each complete game held out once."""
    required = [
        "season",
        "game_id",
        "play_id",
        "posteam",
        "defteam",
        "epa",
    ]

    missing = set(required) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing model columns: {sorted(missing)}")

    if frame.is_empty():
        raise ValueError("The cohort is empty.")

    if any(frame.select(required).null_count().row(0)):
        raise ValueError("Model input columns contain null values.")

    if frame["season"].n_unique() != 1:
        raise ValueError("This prototype evaluates one season at a time.")

    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("alpha must be finite and positive.")

    if not 2 <= n_splits <= frame["game_id"].n_unique():
        raise ValueError("Choose between 2 and the number of games for folds.")

    # Stable row order makes saved predictions easier to compare.
    frame = frame.sort("game_id", "play_id")

    teams = frame.select("posteam", "defteam").to_numpy()
    target = frame["epa"].to_numpy()
    groups = frame["game_id"].to_numpy()

    if not np.isfinite(target).all():
        raise ValueError("EPA must contain only finite values.")

    if any(not str(team).strip() for team in teams.ravel()):
        raise ValueError("Offense or defense labels are blank.")

    predictions = {name: np.full(frame.height, np.nan) for name in MODEL_NAMES}
    fold_ids = np.full(frame.height, -1, dtype=int)

    splitter = GroupKFold(n_splits=n_splits)

    for fold, (train, test) in enumerate(splitter.split(teams, target, groups)):
        fold_ids[test] = fold
        predictions["league_mean"][test] = target[train].mean()

        for name, include_defense in (
            ("offense_only", False),
            ("offense_defense", True),
        ):
            model = make_team_model(
                alpha=alpha,
                include_defense=include_defense,
                defense_alpha=defense_alpha,
            )

            model.fit(teams[train], target[train])
            predictions[name][test] = model.predict(teams[test])

    if (fold_ids < 0).any():
        raise ValueError("Some plays were not assigned to a test fold.")

    if any(not np.isfinite(values).all() for values in predictions.values()):
        raise ValueError("Some predictions are missing or non-finite.")

    return frame.select(required).with_columns(
        pl.Series("fold", fold_ids),
        *[pl.Series(f"pred_{name}", predictions[name]) for name in MODEL_NAMES],
    )


def summarize_errors(predictions: pl.DataFrame) -> pl.DataFrame:
    """Calculate pooled play-weighted errors across held-out predictions."""
    target = predictions["epa"].to_numpy()
    records = []

    for name in MODEL_NAMES:
        predicted = predictions[f"pred_{name}"].to_numpy()
        mse = float(np.mean((target - predicted) ** 2))

        records.append(
            {
                "model": name,
                "mse": mse,
                "rmse": float(np.sqrt(mse)),
            }
        )

    return pl.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate an opponent-adjustment prototype."
    )
    parser.add_argument("--cohort-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--alpha", type=float, default=100.0)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()

    root = args.project_root.resolve()
    source = json.loads(args.cohort_manifest.read_text(encoding="utf-8"))

    cohort_path = root / source["files"]["cohort"]["path"]
    expected_hash = source["files"]["cohort"]["sha256"]

    if file_sha256(cohort_path) != expected_hash:
        raise ValueError("Cohort checksum does not match the manifest.")

    cohort = pl.read_parquet(cohort_path)

    print("Cohort checksum verified.")
    print(
        f"Evaluating {cohort.height:,} plays across "
        f"{cohort['game_id'].n_unique():,} games..."
    )

    predictions = evaluate_models(
        cohort,
        alpha=args.alpha,
        n_splits=args.folds,
    )
    scores = summarize_errors(predictions)

    # Preserve paired errors by game for subsequent uncertainty analysis.
    game_errors = (
        predictions.group_by("game_id", "fold")
        .agg(
            pl.len().alias("dropbacks"),
            *[
                ((pl.col("epa") - pl.col(f"pred_{name}")) ** 2)
                .mean()
                .alias(f"{name}_mse")
                for name in MODEL_NAMES
            ],
        )
        .sort("game_id")
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    prediction_dir = root / "data" / "interim" / run_id
    report_dir = root / "reports" / "tables" / run_id
    metadata_dir = root / "data" / "metadata" / run_id

    for directory in (prediction_dir, report_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=False)

    prediction_path = prediction_dir / "held_out_predictions.parquet"
    score_path = report_dir / "model_scores.csv"
    game_error_path = report_dir / "game_errors.csv"
    manifest_path = metadata_dir / "adjustment_manifest.json"

    predictions.write_parquet(prediction_path, compression="zstd")
    scores.write_csv(score_path)
    game_errors.write_csv(game_error_path)

    scores_by_model = {row["model"]: row["mse"] for row in scores.to_dicts()}
    offense_mse = scores_by_model["offense_only"]
    joint_mse = scores_by_model["offense_defense"]
    reduction = offense_mse - joint_mse

    manifest = {
        "run_id": run_id,
        "source_cohort_run_id": source["run_id"],
        "source_cohort_sha256": expected_hash,
        "model": "Additive team offense and defense ridge regression",
        "alpha": args.alpha,
        "folds": args.folds,
        "split_method": "GroupKFold grouped by game_id",
        "evaluation": "Retrospective held-out games within one season",
        "error_weighting": "Each eligible dropback has equal weight",
        "rows": predictions.height,
        "games": predictions["game_id"].n_unique(),
        "season": int(predictions["season"][0]),
        "scores": scores.to_dicts(),
        "mse_reduction_from_adding_defense": reduction,
        "code_sha256": file_sha256(Path(__file__)),
        "packages": {
            name: version(name) for name in ("numpy", "polars", "scikit-learn")
        },
        "files": {
            name: {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
            }
            for name, path in (
                ("predictions", prediction_path),
                ("scores", score_path),
                ("game_errors", game_error_path),
            )
        },
        "limitations": [
            "Fixed pilot regularization; not optimized.",
            "Team effects do not isolate individual quarterback skill.",
            "No additional game-context covariates yet.",
            "Within-season evaluation is not chronological forecasting.",
            "Existing upstream EPA values are treated as a fixed target.",
        ],
    }
    write_json(manifest_path, manifest)

    print("\nHeld-out prediction errors; lower is better:")
    with pl.Config(float_precision=6):
        print(scores)

    print(f"\nMSE reduction from adding defense: {reduction:.6f}")
    if offense_mse > 0:
        print(f"Relative MSE reduction: {100 * reduction / offense_mse:.3f}%")

    print(f"\nPredictions: {prediction_path.relative_to(root)}")
    print(f"Game errors: {game_error_path.relative_to(root)}")
    print(f"Manifest: {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
