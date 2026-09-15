"""Compare two prespecified defensive penalties on existing cohorts."""

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl

from gridiron_value.adjustment import evaluate_models, summarize_errors
from gridiron_value.data import file_sha256, write_json

OFFENSE_ALPHA = 100.0
DEFENSE_ALPHAS = (100.0, 1000.0)
FOLDS = 5


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore stronger shrinkage of defensive team effects."
    )
    parser.add_argument("--cohort-manifests", nargs="+", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    root = args.project_root.resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    metadata_dir = root / "data" / "metadata" / run_id
    report_dir = root / "reports" / "tables" / run_id
    prediction_dir = root / "data" / "interim" / run_id

    for directory in (metadata_dir, report_dir, prediction_dir):
        directory.mkdir(parents=True, exist_ok=False)

    experiment = {
        "run_id": run_id,
        "purpose": "Exploratory defensive-penalty sensitivity analysis",
        "offense_alpha": OFFENSE_ALPHA,
        "defense_alphas": list(DEFENSE_ALPHAS),
        "folds": FOLDS,
        "evaluation": "Same within-season game-grouped folds for each setting",
        "packages": {
            name: version(name) for name in ("numpy", "polars", "scikit-learn")
        },
        "code_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in ("adjustment.py", "shrinkage.py")
        },
        "runs": [],
        "status": "running",
    }

    experiment_path = metadata_dir / "shrinkage_experiment.json"
    write_json(experiment_path, experiment)

    records = []
    seen_seasons = set()

    for manifest_path in args.cohort_manifests:
        source = json.loads(manifest_path.read_text(encoding="utf-8"))
        cohort_info = source["files"]["cohort"]
        cohort_path = root / cohort_info["path"]

        if file_sha256(cohort_path) != cohort_info["sha256"]:
            raise ValueError(f"Cohort checksum failed: {cohort_path}")

        cohort = pl.read_parquet(cohort_path)

        if cohort["season"].n_unique() != 1:
            raise ValueError("Each input cohort must contain one season.")

        season = int(cohort["season"][0])

        if season in seen_seasons:
            raise ValueError(f"Duplicate input season: {season}")
        seen_seasons.add(season)

        reference_offense_mse = None

        for defense_alpha in DEFENSE_ALPHAS:
            print(f"Season {season}; defense penalty {defense_alpha:g}...")

            predictions = evaluate_models(
                cohort,
                alpha=OFFENSE_ALPHA,
                n_splits=FOLDS,
                defense_alpha=defense_alpha,
            )

            mse = {
                row["model"]: row["mse"]
                for row in summarize_errors(predictions).to_dicts()
            }

            if reference_offense_mse is None:
                reference_offense_mse = mse["offense_only"]
            elif not np.isclose(
                mse["offense_only"],
                reference_offense_mse,
                rtol=1e-10,
                atol=1e-10,
            ):
                raise ValueError("The offense-only reference changed.")

            fold_gains = predictions.group_by("fold").agg(
                (
                    (pl.col("epa") - pl.col("pred_offense_only")) ** 2
                    - (pl.col("epa") - pl.col("pred_offense_defense")) ** 2
                )
                .mean()
                .alias("gain")
            )

            gain = mse["offense_only"] - mse["offense_defense"]

            record = {
                "season": season,
                "defense_alpha": defense_alpha,
                "dropbacks": cohort.height,
                "offense_only_mse": mse["offense_only"],
                "offense_defense_mse": mse["offense_defense"],
                "mse_reduction": gain,
                "folds_improved": fold_gains.filter(pl.col("gain") > 0).height,
            }
            records.append(record)

            prediction_path = prediction_dir / (
                f"predictions_{season}_defense_{int(defense_alpha)}.parquet"
            )
            predictions.write_parquet(prediction_path, compression="zstd")

            experiment["runs"].append(
                {
                    **record,
                    "source_manifest": str(manifest_path),
                    "source_cohort_sha256": cohort_info["sha256"],
                    "predictions": prediction_path.relative_to(root).as_posix(),
                    "predictions_sha256": file_sha256(prediction_path),
                }
            )

            # Save progress after each completed fit.
            pl.DataFrame(records).write_csv(report_dir / "shrinkage_summary.csv")
            write_json(experiment_path, experiment)

    experiment["status"] = "completed"
    write_json(experiment_path, experiment)

    summary = pl.DataFrame(records).sort("season", "defense_alpha")

    with pl.Config(tbl_rows=12, tbl_cols=7, tbl_width_chars=140, float_precision=6):
        print(summary)

    print(f"\nSummary: {(report_dir / 'shrinkage_summary.csv').relative_to(root)}")
    print(f"Experiment: {experiment_path.relative_to(root)}")


if __name__ == "__main__":
    main()
