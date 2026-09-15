"""Present experimental opponent-adjusted production on a Pass+ scale."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl

from gridiron_value.data import file_sha256, write_json


def add_pass_plus(leaderboard: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Standardize player-season rates using dropback weights."""
    required = {
        "season",
        "dropback_player_id",
        "dropbacks",
        "opponent_adjusted_epa_per_dropback",
    }

    missing = required - set(leaderboard.columns)
    if missing:
        raise ValueError(f"Missing index columns: {sorted(missing)}")

    if leaderboard.is_empty():
        raise ValueError("The leaderboard is empty.")

    if any(leaderboard.select(sorted(required)).null_count().row(0)):
        raise ValueError("Index inputs contain null values.")

    if leaderboard["season"].n_unique() != 1:
        raise ValueError("Calculate the index separately for each season.")

    if leaderboard["dropback_player_id"].n_unique() != leaderboard.height:
        raise ValueError("Expected one row per player in the season.")

    weights = leaderboard["dropbacks"].to_numpy().astype(float)
    rates = leaderboard["opponent_adjusted_epa_per_dropback"].to_numpy()

    if (
        not np.isfinite(weights).all()
        or not np.isfinite(rates).all()
        or (weights <= 0).any()
    ):
        raise ValueError("Rates must be finite and dropback weights positive.")

    mean = float(np.average(rates, weights=weights))
    variance = float(np.average((rates - mean) ** 2, weights=weights))
    standard_deviation = float(np.sqrt(variance))

    if standard_deviation <= 0:
        raise ValueError("Player rates have no variation; cannot standardize.")

    scores = 100.0 + 15.0 * (rates - mean) / standard_deviation

    reference = {
        "season": int(leaderboard["season"][0]),
        "participants": leaderboard.height,
        "dropbacks": int(weights.sum()),
        "league_mean_adjusted_epa_per_dropback": mean,
        "weighted_sd_of_player_rates": standard_deviation,
        "index_center": 100.0,
        "index_points_per_sd": 15.0,
        "weighting": "Eligible dropbacks per player-season",
    }

    return (
        leaderboard.with_columns(pl.Series("pass_plus", scores)),
        reference,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the experimental Pass+ presentation."
    )
    parser.add_argument("--schedule-manifest", type=Path, required=True)
    parser.add_argument("--variability-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-dropbacks", type=int, default=100)
    args = parser.parse_args()

    if args.min_dropbacks < 1:
        parser.error("--min-dropbacks must be positive.")

    root = args.project_root.resolve()
    schedule = json.loads(args.schedule_manifest.read_text(encoding="utf-8"))
    variability = json.loads(args.variability_manifest.read_text(encoding="utf-8"))

    if variability["source_schedule_run_id"] != schedule["run_id"]:
        raise ValueError("The two manifests refer to different schedule runs.")

    if (
        variability["source_scored_plays_sha256"]
        != schedule["files"]["scored_plays"]["sha256"]
    ):
        raise ValueError("The underlying scored-play snapshots differ.")

    leaderboard_info = schedule["files"]["leaderboard"]
    variability_info = variability["report"]

    leaderboard_path = root / leaderboard_info["path"]
    variability_path = root / variability_info["path"]

    for path, info in (
        (leaderboard_path, leaderboard_info),
        (variability_path, variability_info),
    ):
        if file_sha256(path) != info["sha256"]:
            raise ValueError(f"Report checksum failed: {path}")

    leaderboard = pl.read_csv(leaderboard_path)
    ranges = pl.read_csv(variability_path)

    indexed, reference = add_pass_plus(leaderboard)

    if reference["dropbacks"] != schedule["dropbacks"]:
        raise ValueError("Index population does not match the schedule cohort.")

    report = indexed.join(
        ranges.select(
            "season",
            "dropback_player_id",
            pl.col("dropbacks").alias("resampling_dropbacks"),
            "resampled_rate_p025",
            "resampled_rate_p975",
        ),
        on=["season", "dropback_player_id"],
        how="left",
        validate="1:1",
    )

    mean = reference["league_mean_adjusted_epa_per_dropback"]
    standard_deviation = reference["weighted_sd_of_player_rates"]

    # Transform ranges with the fixed original league reference.
    report = report.with_columns(
        (
            100.0 + 15.0 * (pl.col("resampled_rate_p025") - mean) / standard_deviation
        ).alias("pass_plus_resampled_p025"),
        (
            100.0 + 15.0 * (pl.col("resampled_rate_p975") - mean) / standard_deviation
        ).alias("pass_plus_resampled_p975"),
    ).sort(
        ["pass_plus", "dropback_player_id"],
        descending=[True, False],
    )

    displayed = report.filter(pl.col("dropbacks") >= args.min_dropbacks)

    missing_ranges = displayed.filter(pl.col("resampling_dropbacks").is_null())
    if missing_ranges.height:
        raise ValueError(
            "Some displayed players lack a variability record. "
            "Regenerate variability using a lower dropback threshold."
        )

    mismatched_counts = displayed.filter(
        pl.col("dropbacks") != pl.col("resampling_dropbacks")
    )
    if mismatched_counts.height:
        raise ValueError("Leaderboard and variability dropback counts differ.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_dir = root / "reports" / "tables" / run_id
    metadata_dir = root / "data" / "metadata" / run_id
    report_dir.mkdir(parents=True, exist_ok=False)
    metadata_dir.mkdir(parents=True, exist_ok=False)

    report_path = report_dir / "pass_plus_leaderboard.csv"
    manifest_path = metadata_dir / "pass_plus_manifest.json"

    # Preserve full precision in the saved report.
    report.write_csv(report_path)

    write_json(
        manifest_path,
        {
            "run_id": run_id,
            "status": "experimental",
            "index_version": "0.1.0",
            "source_schedule_run_id": schedule["run_id"],
            "source_variability_run_id": variability["run_id"],
            "source_leaderboard_sha256": leaderboard_info["sha256"],
            "source_variability_sha256": variability_info["sha256"],
            "reference": reference,
            "display_min_dropbacks": args.min_dropbacks,
            "code_sha256": file_sha256(Path(__file__)),
            "packages": {
                "numpy": np.__version__,
                "polars": pl.__version__,
            },
            "report": {
                "path": report_path.relative_to(root).as_posix(),
                "sha256": file_sha256(report_path),
            },
            "interpretation": [
                "100 is the play-weighted league reference.",
                "15 points represent one weighted SD of player-season rates.",
                "Scores are not percentage-above-average values.",
                "All eligible participants determine the reference.",
                "Display thresholds do not alter the index.",
                "Resampling ranges use fixed schedule and index references.",
                "Ranges are not confidence intervals for isolated skill.",
                "The index is a presentation transform, not a new model.",
            ],
        },
    )

    print(f"Reference participants: {reference['participants']}")
    print(f"Reference dropbacks: {reference['dropbacks']:,}")
    print(f"League adjusted EPA/dropback: {mean:.6f}")
    print(f"Weighted SD of player rates: {standard_deviation:.6f}")

    table = displayed.select(
        "player",
        "games",
        "dropbacks",
        pl.col("pass_plus").round(0).cast(pl.Int64).alias("Pass+"),
        pl.col("pass_plus_resampled_p025")
        .round(0)
        .cast(pl.Int64)
        .alias("resampled_low"),
        pl.col("pass_plus_resampled_p975")
        .round(0)
        .cast(pl.Int64)
        .alias("resampled_high"),
        pl.col("opponent_adjusted_epa_above_average").round(1).alias("adjusted_EPA_AA"),
    )

    print("\nExperimental Pass+ leaderboard:")
    print("Resampled bounds describe game variability, not isolated skill.")

    with pl.Config(tbl_rows=15, tbl_cols=7, tbl_width_chars=150):
        print(table.head(15))

    print(f"\nReport: {report_path.relative_to(root)}")
    print(f"Manifest: {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
