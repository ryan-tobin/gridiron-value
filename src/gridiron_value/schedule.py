"""Calculate experimental opponent-adjusted dropback production."""

import argparse
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.model_selection import GroupKFold

from gridiron_value.adjustment import make_team_model
from gridiron_value.baseline import passing_baseline
from gridiron_value.data import file_sha256, write_json


def defense_contribution(model, teams: np.ndarray) -> np.ndarray:
    """Extract the defensive contribution in EPA units."""
    transformer = model.steps[0][1]
    regression = model.steps[1][1]

    encoded = transformer.transform(teams)
    defense_slice = transformer.output_indices_["defense"]

    # The transformed features already include the defensive scaling.
    return encoded[:, defense_slice] @ regression.coef_[defense_slice]


def schedule_adjustments(
    cohort: pl.DataFrame,
    offense_alpha: float = 100.0,
    defense_alpha: float = 1000.0,
    n_splits: int = 5,
) -> pl.DataFrame:
    """Estimate held-out defensive effects, then center their corrections."""
    required = [
        "season",
        "game_id",
        "play_id",
        "posteam",
        "defteam",
        "epa",
    ]

    missing = set(required) - set(cohort.columns)
    if missing:
        raise ValueError(f"Missing schedule columns: {sorted(missing)}")

    if cohort.is_empty():
        raise ValueError("The cohort is empty.")

    if any(cohort.select(required).null_count().row(0)):
        raise ValueError("Schedule input columns contain null values.")

    if cohort["season"].n_unique() != 1:
        raise ValueError("Calculate schedule adjustments one season at a time.")

    if not 2 <= n_splits <= cohort["game_id"].n_unique():
        raise ValueError("Invalid number of game folds.")

    frame = cohort.sort("game_id", "play_id")
    teams = frame.select("posteam", "defteam").to_numpy()
    target = frame["epa"].to_numpy()
    groups = frame["game_id"].to_numpy()

    if not np.isfinite(target).all():
        raise ValueError("EPA must be finite.")

    effects = np.full(frame.height, np.nan)
    fold_ids = np.full(frame.height, -1, dtype=int)
    splitter = GroupKFold(n_splits=n_splits)

    for fold, (train, test) in enumerate(splitter.split(teams, target, groups)):
        model = make_team_model(
            alpha=offense_alpha,
            include_defense=True,
            defense_alpha=defense_alpha,
        )
        model.fit(teams[train], target[train])

        effects[test] = defense_contribution(model, teams[test])
        fold_ids[test] = fold

    if not np.isfinite(effects).all() or (fold_ids < 0).any():
        raise ValueError("Some defensive effects were not calculated.")

    # This final normalization is descriptive, using the season's full
    # exposure mix. The individual defensive terms exclude their own games.
    reference_effect = float(effects.mean())
    corrections = reference_effect - effects

    if not np.isclose(corrections.sum(), 0.0, atol=1e-8):
        raise ValueError("Schedule corrections do not balance to zero.")

    return frame.with_columns(
        pl.Series("schedule_fold", fold_ids),
        pl.Series("opponent_effect_oof", effects),
        pl.lit(reference_effect).alias("opponent_reference"),
        pl.Series("schedule_adjustment", corrections),
        pl.Series("opponent_adjusted_epa", target + corrections),
    )


def summarize_adjustments(scored: pl.DataFrame) -> pl.DataFrame:
    """Combine the original baseline with player-level schedule corrections."""
    baseline = passing_baseline(scored)

    corrections = scored.group_by("season", "dropback_player_id").agg(
        pl.col("schedule_adjustment").sum().alias("schedule_adjustment_total"),
        pl.col("schedule_adjustment").mean().alias("schedule_adjustment_per_dropback"),
    )

    result = baseline.join(
        corrections,
        on=["season", "dropback_player_id"],
        how="left",
        validate="1:1",
    ).with_columns(
        (pl.col("epa_per_dropback") + pl.col("schedule_adjustment_per_dropback")).alias(
            "opponent_adjusted_epa_per_dropback"
        ),
        (pl.col("epa_above_average") + pl.col("schedule_adjustment_total")).alias(
            "opponent_adjusted_epa_above_average"
        ),
    )

    if result["dropbacks"].sum() != scored.height:
        raise ValueError("Player counts do not reconcile.")

    if not np.isclose(
        result["opponent_adjusted_epa_above_average"].sum(),
        0.0,
        atol=1e-8,
    ):
        raise ValueError("Adjusted value above average does not balance.")

    return result.sort(
        ["opponent_adjusted_epa_per_dropback", "dropback_player_id"],
        descending=[True, False],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an experimental opponent-adjusted leaderboard."
    )
    parser.add_argument("--cohort-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-dropbacks", type=int, default=100)
    args = parser.parse_args()

    if args.min_dropbacks < 1:
        parser.error("--min-dropbacks must be positive.")

    root = args.project_root.resolve()
    source = json.loads(args.cohort_manifest.read_text(encoding="utf-8"))
    cohort_info = source["files"]["cohort"]
    cohort_path = root / cohort_info["path"]

    if file_sha256(cohort_path) != cohort_info["sha256"]:
        raise ValueError("Cohort checksum does not match.")

    cohort = pl.read_parquet(cohort_path)
    scored = schedule_adjustments(cohort)
    leaderboard = summarize_adjustments(scored)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    processed_dir = root / "data" / "processed" / run_id
    report_dir = root / "reports" / "tables" / run_id
    metadata_dir = root / "data" / "metadata" / run_id

    for directory in (processed_dir, report_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=False)

    scored_path = processed_dir / "schedule_adjusted_dropbacks.parquet"
    leaderboard_path = report_dir / "opponent_adjusted_leaderboard.csv"
    manifest_path = metadata_dir / "schedule_manifest.json"

    scored.write_parquet(scored_path, compression="zstd")
    leaderboard.write_csv(leaderboard_path)

    manifest = {
        "run_id": run_id,
        "status": "experimental",
        "source_manifest": str(args.cohort_manifest),
        "source_cohort_sha256": cohort_info["sha256"],
        "season": int(scored["season"][0]),
        "dropbacks": scored.height,
        "offense_alpha": 100.0,
        "defense_alpha": 1000.0,
        "folds": 5,
        "opponent_reference": float(scored["opponent_reference"][0]),
        "schedule_adjustment_sum": float(scored["schedule_adjustment"].sum()),
        "code_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in ("schedule.py", "adjustment.py", "baseline.py")
        },
        "packages": {
            name: version(name) for name in ("numpy", "polars", "scikit-learn")
        },
        "files": {
            name: {
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
            }
            for name, path in (
                ("scored_plays", scored_path),
                ("leaderboard", leaderboard_path),
            )
        },
        "interpretation": [
            "Observed EPA plus a centered defensive-opponent correction.",
            "Each defensive term is estimated without its own game.",
            "Final league centering uses all eligible season dropbacks.",
            "Only the defensive contribution is removed.",
            "Supporting-offense contributions remain.",
            "No player-specific uncertainty intervals are available yet.",
            "This is retrospective production, not a forecasting score.",
        ],
    }
    write_json(manifest_path, manifest)

    displayed = leaderboard.filter(pl.col("dropbacks") >= args.min_dropbacks).select(
        "player",
        "dropbacks",
        pl.col("epa_per_dropback").alias("raw_epa_db"),
        pl.col("schedule_adjustment_per_dropback").alias("schedule_delta_db"),
        pl.col("opponent_adjusted_epa_per_dropback").alias("adjusted_epa_db"),
        pl.col("opponent_adjusted_epa_above_average").alias("adjusted_epa_aa"),
    )

    print(f"Dropbacks: {scored.height:,}")
    print(f"Schedule adjustment sum: {scored['schedule_adjustment'].sum():.10f}")
    print(f"\nExperimental leaderboard; minimum {args.min_dropbacks} dropbacks:")

    with pl.Config(tbl_rows=15, tbl_width_chars=150, float_precision=4):
        print(displayed.head(15))

    print(f"\nLeaderboard: {leaderboard_path.relative_to(root)}")
    print(f"Scored plays: {scored_path.relative_to(root)}")
    print(f"Manifest: {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
