"""Replicate the fixed opponent-model experiment across completed seasons."""

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import polars as pl

from gridiron_value.adjustment import evaluate_models, summarize_errors
from gridiron_value.cohort import COHORT_VERSION, build_cohort
from gridiron_value.data import download_snapshot, file_sha256, write_json
from gridiron_value.uncertainty import game_bootstrap

OFFENSE_ALPHA = 100.0
DEFENSE_ALPHA = 1000.0
FOLDS = 5
RESAMPLES = 10_000
SEED = 2026


def replicate_season(season: int, root: Path, batch_id: str) -> dict:
    """Run the unchanged experiment for one season and save its artifacts."""
    raw_manifest_path = download_snapshot(season, root)
    raw_manifest = json.loads(raw_manifest_path.read_text(encoding="utf-8"))
    raw_path = root / raw_manifest["files"]["raw"]["path"]

    if file_sha256(raw_path) != raw_manifest["files"]["raw"]["sha256"]:
        raise ValueError("Raw snapshot checksum does not match.")

    raw = pl.read_parquet(raw_path)
    cohort, exclusions = build_cohort(raw)

    predictions = evaluate_models(
        cohort,
        alpha=OFFENSE_ALPHA,
        n_splits=FOLDS,
        defense_alpha=DEFENSE_ALPHA,
    )
    scores = summarize_errors(predictions)

    game_errors = (
        predictions.group_by("game_id", "fold")
        .agg(
            pl.len().alias("dropbacks"),
            *[
                ((pl.col("epa") - pl.col(f"pred_{name}")) ** 2)
                .mean()
                .alias(f"{name}_mse")
                for name in ("offense_only", "offense_defense")
            ],
        )
        .sort("game_id")
    )

    stability = game_bootstrap(
        game_errors,
        n_resamples=RESAMPLES,
        seed=SEED,
    )

    fold_gains = (
        game_errors.group_by("fold")
        .agg(
            (
                (
                    pl.col("dropbacks")
                    * (pl.col("offense_only_mse") - pl.col("offense_defense_mse"))
                ).sum()
                / pl.col("dropbacks").sum()
            ).alias("mse_reduction")
        )
        .sort("fold")
    )

    mse = {row["model"]: row["mse"] for row in scores.to_dicts()}
    gain = mse["offense_only"] - mse["offense_defense"]
    interval = stability["conditional_percentile_95_interval"]

    record = {
        "season": season,
        "games": game_errors.height,
        "dropbacks": cohort.height,
        "identity_fallbacks": cohort.filter(
            pl.col("identity_source") == "scramble_rusher_id"
        ).height,
        "league_mean_mse": mse["league_mean"],
        "offense_only_mse": mse["offense_only"],
        "offense_defense_mse": mse["offense_defense"],
        "mse_reduction": gain,
        "relative_reduction_pct": (
            100 * gain / mse["offense_only"] if mse["offense_only"] > 0 else None
        ),
        "conditional_interval_low": interval["low"],
        "conditional_interval_high": interval["high"],
        "games_improved": stability["games_improved"],
        "folds_improved": fold_gains.filter(pl.col("mse_reduction") > 0).height,
    }

    processed_dir = root / "data" / "processed" / batch_id / str(season)
    interim_dir = root / "data" / "interim" / batch_id / str(season)
    report_dir = root / "reports" / "tables" / batch_id / str(season)
    metadata_dir = root / "data" / "metadata" / batch_id / str(season)

    for directory in (
        processed_dir,
        interim_dir,
        report_dir,
        metadata_dir,
    ):
        directory.mkdir(parents=True, exist_ok=False)

    files = {
        "cohort": processed_dir / "dropbacks.parquet",
        "predictions": interim_dir / "held_out_predictions.parquet",
        "scores": report_dir / "model_scores.csv",
        "game_errors": report_dir / "game_errors.csv",
        "fold_gains": report_dir / "fold_gains.csv",
    }

    cohort.write_parquet(files["cohort"], compression="zstd")
    predictions.write_parquet(files["predictions"], compression="zstd")
    scores.write_csv(files["scores"])
    game_errors.write_csv(files["game_errors"])
    fold_gains.write_csv(files["fold_gains"])

    manifest = {
        "batch_id": batch_id,
        "season": season,
        "source_raw_run_id": raw_manifest["run_id"],
        "source_raw_manifest": raw_manifest_path.relative_to(root).as_posix(),
        "source_raw_sha256": raw_manifest["files"]["raw"]["sha256"],
        "cohort_version": COHORT_VERSION,
        "exclusions": exclusions,
        "result": record,
        "stability": stability,
        "offense_alpha": OFFENSE_ALPHA,
        "defense_alpha": DEFENSE_ALPHA,
        "folds": FOLDS,
        "files": {
            name: {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
            }
            for name, path in files.items()
        },
    }
    write_json(metadata_dir / "replication_manifest.json", manifest)

    return record


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replicate the fixed opponent model by season."
    )
    parser.add_argument("--seasons", nargs="+", type=int, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.project_root.resolve()
    seasons = sorted(set(args.seasons))
    current_year = datetime.now(timezone.utc).year

    if not (root / "pyproject.toml").is_file():
        parser.error("Run from the project root or supply --project-root.")

    if any(not 1999 <= season < current_year for season in seasons):
        parser.error("Request completed seasons from 1999 onward.")

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    metadata_dir = root / "data" / "metadata" / batch_id
    report_dir = root / "reports" / "tables" / batch_id
    metadata_dir.mkdir(parents=True, exist_ok=False)
    report_dir.mkdir(parents=True, exist_ok=False)

    package_dir = Path(__file__).parent
    experiment = {
        "batch_id": batch_id,
        "seasons_requested": seasons,
        "offense_alpha": OFFENSE_ALPHA,
        "defense_alpha": DEFENSE_ALPHA,
        "candidate": "Separate penalties: offense 100, defense 1000",
        "development_seasons": [2020, 2021, 2022, 2023, 2024, 2025],
        "folds": FOLDS,
        "bootstrap_resamples": RESAMPLES,
        "bootstrap_seed": SEED,
        "evaluation": "Separate within-season game-grouped evaluations",
        "packages": {
            name: version(name)
            for name in ("nflreadpy", "numpy", "polars", "scipy", "scikit-learn")
        },
        "code_sha256": {
            name: file_sha256(package_dir / name)
            for name in (
                "data.py",
                "cohort.py",
                "adjustment.py",
                "uncertainty.py",
                "replicate.py",
            )
        },
        "completed": [],
        "status": "running",
    }
    experiment_path = metadata_dir / "experiment.json"
    write_json(experiment_path, experiment)

    records = []

    for season in seasons:
        print(f"\nStarting replication for {season}...")

        try:
            record = replicate_season(season, root, batch_id)
        except Exception as error:
            experiment["status"] = "failed"
            experiment["failed_season"] = season
            experiment["error"] = f"{type(error).__name__}: {error}"
            write_json(experiment_path, experiment)
            raise

        records.append(record)
        experiment["completed"].append(season)

        # Checkpoint after every successful season.
        pl.DataFrame(records).write_csv(report_dir / "replication_summary.csv")
        write_json(experiment_path, experiment)

        print(
            f"{season}: MSE reduction = {record['mse_reduction']:.6f}; "
            f"folds improved = {record['folds_improved']}/{FOLDS}"
        )

    experiment["status"] = "completed"
    write_json(experiment_path, experiment)

    summary = pl.DataFrame(records).select(
        "season",
        "dropbacks",
        "mse_reduction",
        "relative_reduction_pct",
        "conditional_interval_low",
        "conditional_interval_high",
        "folds_improved",
    )

    print("\nReplication results:")
    with pl.Config(tbl_cols=7, tbl_width_chars=150, float_precision=6):
        print(summary)

    print(f"\nSummary: {(report_dir / 'replication_summary.csv').relative_to(root)}")
    print(f"Experiment: {experiment_path.relative_to(root)}")


if __name__ == "__main__":
    main()
