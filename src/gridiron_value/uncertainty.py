"""Assess game-level stability of the opponent-model improvement."""

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl
from scipy.stats import bootstrap

from gridiron_value.data import file_sha256, write_json


def weighted_gain(
    weights: np.ndarray,
    offense_error: np.ndarray,
    joint_error: np.ndarray,
    axis: int = -1,
) -> np.ndarray:
    """Positive values mean adding defense reduced play-weighted MSE."""
    return np.sum(
        weights * (offense_error - joint_error),
        axis=axis,
    ) / np.sum(weights, axis=axis)


def game_bootstrap(
    game_errors: pl.DataFrame,
    n_resamples: int = 10_000,
    seed: int = 2026,
) -> dict:
    """Resample paired game errors while retaining dropback weighting."""
    columns = [
        "dropbacks",
        "offense_only_mse",
        "offense_defense_mse",
    ]

    if game_errors.height < 2:
        raise ValueError("At least two games are required.")

    if n_resamples < 1:
        raise ValueError("n_resamples must be positive.")

    missing = set(columns) - set(game_errors.columns)
    if missing:
        raise ValueError(f"Missing error columns: {sorted(missing)}")

    if any(game_errors.select(columns).null_count().row(0)):
        raise ValueError("Game error inputs contain null values.")

    weights, offense, joint = (
        game_errors[column].to_numpy().astype(float) for column in columns
    )

    if not all(np.isfinite(array).all() for array in (weights, offense, joint)):
        raise ValueError("Game error inputs must be finite.")

    if (weights <= 0).any() or (offense < 0).any() or (joint < 0).any():
        raise ValueError("Weights must be positive and MSEs nonnegative.")

    estimate = float(weighted_gain(weights, offense, joint))

    result = bootstrap(
        data=(weights, offense, joint),
        statistic=weighted_gain,
        paired=True,
        vectorized=True,
        axis=-1,
        confidence_level=0.95,
        method="percentile",
        n_resamples=n_resamples,
        batch=1000,
        rng=np.random.default_rng(seed),
    )

    game_gain = offense - joint

    return {
        "games": game_errors.height,
        "dropbacks": int(weights.sum()),
        "mse_reduction": estimate,
        "conditional_percentile_95_interval": {
            "low": float(result.confidence_interval.low),
            "high": float(result.confidence_interval.high),
        },
        "games_improved": int((game_gain > 0).sum()),
        "games_worsened": int((game_gain < 0).sum()),
        "games_tied": int((game_gain == 0).sum()),
        "n_resamples": n_resamples,
        "seed": seed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check the game-level stability of opponent adjustment."
    )
    parser.add_argument("--adjustment-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    root = args.project_root.resolve()
    source = json.loads(args.adjustment_manifest.read_text(encoding="utf-8"))

    error_path = root / source["files"]["game_errors"]["path"]
    expected_hash = source["files"]["game_errors"]["sha256"]

    if file_sha256(error_path) != expected_hash:
        raise ValueError("Game error checksum does not match the manifest.")

    errors = pl.read_csv(error_path).sort("game_id")

    if errors["game_id"].null_count():
        raise ValueError("Game identifiers are missing.")

    if errors["game_id"].n_unique() != errors.height:
        raise ValueError("Expected exactly one error record per game.")

    if errors.height != source["games"]:
        raise ValueError("Game count does not match the model manifest.")

    if errors["dropbacks"].sum() != source["rows"]:
        raise ValueError("Dropback count does not match the model manifest.")

    result = game_bootstrap(
        errors,
        n_resamples=args.resamples,
        seed=args.seed,
    )

    if not np.isclose(
        result["mse_reduction"],
        source["mse_reduction_from_adding_defense"],
        rtol=1e-8,
        atol=1e-10,
    ):
        raise ValueError("Game-level errors do not reproduce the original gain.")

    fold_summary = (
        errors.group_by("fold")
        .agg(
            pl.len().alias("games"),
            pl.col("dropbacks").sum().alias("dropbacks"),
            (
                (
                    pl.col("dropbacks")
                    * (pl.col("offense_only_mse") - pl.col("offense_defense_mse"))
                ).sum()
                / pl.col("dropbacks").sum()
            ).alias("mse_reduction"),
        )
        .sort("fold")
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    metadata_dir = root / "data" / "metadata" / run_id
    report_dir = root / "reports" / "tables" / run_id

    metadata_dir.mkdir(parents=True, exist_ok=False)
    report_dir.mkdir(parents=True, exist_ok=False)

    fold_path = report_dir / "fold_stability.csv"
    fold_summary.write_csv(fold_path)

    result.update(
        {
            "run_id": run_id,
            "source_adjustment_run_id": source["run_id"],
            "source_game_errors_sha256": expected_hash,
            "code_sha256": file_sha256(Path(__file__)),
            "packages": {name: version(name) for name in ("numpy", "polars", "scipy")},
            "fold_summary": fold_summary.to_dicts(),
            "fold_report": {
                "path": fold_path.relative_to(root).as_posix(),
                "sha256": file_sha256(fold_path),
            },
            "interpretation": (
                "Approximate paired game-resampling diagnostic conditional "
                "on fixed predictions and folds. Models are not refitted. "
                "Dependence across games sharing teams and overlapping "
                "training sets is not fully captured."
            ),
        }
    )

    output_path = metadata_dir / "stability.json"
    write_json(output_path, result)

    interval = result["conditional_percentile_95_interval"]

    print("Game error checksum and original MSE gain verified.")
    print(f"Games: {result['games']:,}")
    print(f"Dropbacks: {result['dropbacks']:,}")
    print(f"MSE reduction: {result['mse_reduction']:.6f}")
    print(
        "Conditional 95% percentile interval: "
        f"[{interval['low']:.6f}, {interval['high']:.6f}]"
    )
    print(f"Games improved: {result['games_improved']}")
    print(f"Games worsened: {result['games_worsened']}")
    print(f"Games tied: {result['games_tied']}")

    print("\nImprovement by evaluation fold:")
    with pl.Config(float_precision=6):
        print(fold_summary)

    print(f"\nStability report: {output_path.relative_to(root)}")


if __name__ == "__main__":
    main()
