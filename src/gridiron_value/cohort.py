"""Construct an auditable dropback cohort and descriptive leaderboard."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from gridiron_value.baseline import passing_baseline
from gridiron_value.data import file_sha256, write_json

COHORT_VERSION = "0.1.0"


def clean_text(column: str) -> pl.Expr:
    """Normalize blank identifiers or names to null."""
    return pl.col(column).cast(pl.String).str.strip_chars().replace("", None)


def resolve_players(frame: pl.DataFrame) -> pl.DataFrame:
    """Identify dropback actors, with a scramble-specific fallback."""
    resolved = frame.with_columns(
        clean_text("passer_id").alias("_standard_id"),
        clean_text("passer_player_id").alias("_raw_passer_id"),
        clean_text("rusher_player_id").alias("_raw_rusher_id"),
    ).with_columns(
        pl.when(pl.col("qb_scramble") == 1)
        .then(pl.col("_raw_rusher_id"))
        .otherwise(pl.col("_raw_passer_id"))
        .alias("_event_id")
    )

    conflicts = resolved.filter(
        pl.col("_standard_id").is_not_null()
        & pl.col("_event_id").is_not_null()
        & (pl.col("_standard_id") != pl.col("_event_id"))
    )

    if conflicts.height:
        raise ValueError(
            f"Found {conflicts.height} player-ID conflicts. "
            "Inspect these before building the cohort."
        )

    resolved = resolved.with_columns(
        pl.coalesce(
            pl.col("_standard_id"),
            pl.when(pl.col("qb_scramble") == 1)
            .then(pl.col("_raw_rusher_id"))
            .otherwise(None),
        ).alias("dropback_player_id"),
        pl.when(pl.col("_standard_id").is_not_null())
        .then(pl.lit("passer_id"))
        .when((pl.col("qb_scramble") == 1) & pl.col("_raw_rusher_id").is_not_null())
        .then(pl.lit("scramble_rusher_id"))
        .otherwise(pl.lit("unresolved"))
        .alias("identity_source"),
        pl.when(pl.col("_standard_id").is_not_null())
        .then(
            pl.coalesce(
                clean_text("passer"),
                pl.when(pl.col("qb_scramble") == 1)
                .then(clean_text("rusher_player_name"))
                .otherwise(clean_text("passer_player_name")),
            )
        )
        .when(pl.col("qb_scramble") == 1)
        .then(clean_text("rusher_player_name"))
        .otherwise(None)
        .alias("dropback_player_name"),
    )

    return resolved.drop(
        "_standard_id",
        "_raw_passer_id",
        "_raw_rusher_id",
        "_event_id",
    )


def build_cohort(frame: pl.DataFrame) -> tuple[pl.DataFrame, list[dict]]:
    """Apply sequential eligibility rules and record every exclusion."""
    required = {
        "season",
        "season_type",
        "game_id",
        "play_id",
        "play_type",
        "qb_dropback",
        "qb_scramble",
        "qb_spike",
        "qb_kneel",
        "two_point_attempt",
        "sack",
        "penalty",
        "epa",
        "passer_id",
        "passer",
        "passer_player_id",
        "passer_player_name",
        "rusher_player_id",
        "rusher_player_name",
    }

    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing cohort columns: {sorted(missing)}")

    missing_keys = frame.filter(
        pl.col("game_id").is_null() | pl.col("play_id").is_null()
    )
    duplicate_keys = frame.height - frame.select("game_id", "play_id").unique().height

    if missing_keys.height or duplicate_keys:
        raise ValueError("Resolve missing or duplicate play keys first.")

    rules = [
        ("regular_season", pl.col("season_type") == "REG"),
        ("flagged_dropback", pl.col("qb_dropback") == 1),
        ("not_two_point_attempt", pl.col("two_point_attempt") == 0),
        ("not_spike", pl.col("qb_spike") == 0),
        ("not_kneel", pl.col("qb_kneel") == 0),
        ("supported_play_type", pl.col("play_type").is_in(["pass", "run"])),
        ("finite_epa", pl.col("epa").is_finite()),
    ]

    cohort = frame
    exclusions = []

    for rule_name, condition in rules:
        before = cohort.height
        cohort = cohort.filter(condition.fill_null(False))

        exclusions.append(
            {
                "rule": rule_name,
                "before": before,
                "excluded": before - cohort.height,
                "remaining": cohort.height,
            }
        )

    cohort = resolve_players(cohort)

    unresolved = cohort.filter(pl.col("dropback_player_id").is_null())
    if unresolved.height:
        raise ValueError(
            f"{unresolved.height} eligible dropbacks remain unidentified. "
            "Inspect them before producing a leaderboard."
        )

    if cohort.is_empty():
        raise ValueError("No eligible dropbacks remain.")

    return cohort, exclusions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a dropback cohort and its EPA baseline."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-dropbacks", type=int, default=100)
    args = parser.parse_args()

    if args.min_dropbacks < 1:
        parser.error("--min-dropbacks must be positive.")

    root = args.project_root.resolve()
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    raw_path = root / source["files"]["raw"]["path"]
    source_hash = source["files"]["raw"]["sha256"]

    if file_sha256(raw_path) != source_hash:
        raise ValueError("Snapshot checksum does not match its manifest.")

    frame = pl.read_parquet(raw_path)
    cohort, exclusions = build_cohort(frame)
    leaderboard = passing_baseline(cohort)

    if leaderboard["dropbacks"].sum() != cohort.height:
        raise ValueError("Leaderboard counts do not reconcile with the cohort.")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    processed_dir = root / "data" / "processed" / run_id
    metadata_dir = root / "data" / "metadata" / run_id
    report_dir = root / "reports" / "tables" / run_id

    for directory in (processed_dir, metadata_dir, report_dir):
        directory.mkdir(parents=True, exist_ok=False)

    cohort_path = processed_dir / "dropbacks.parquet"
    leaderboard_path = report_dir / "passing_baseline.csv"
    manifest_path = metadata_dir / "cohort_manifest.json"

    cohort.write_parquet(cohort_path, compression="zstd")
    leaderboard.write_csv(leaderboard_path)

    fallbacks = cohort.filter(pl.col("identity_source") == "scramble_rusher_id")
    fallbacks.select(
        "game_id",
        "play_id",
        "dropback_player_id",
        "dropback_player_name",
        "identity_source",
        "epa",
    ).write_csv(report_dir / "identity_fallbacks.csv")

    manifest = {
        "run_id": run_id,
        "cohort_version": COHORT_VERSION,
        "source_run_id": source["run_id"],
        "source_sha256": source_hash,
        "polars_version": pl.__version__,
        "code_sha256": {
            "cohort.py": file_sha256(Path(__file__)),
            "baseline.py": file_sha256(Path(__file__).with_name("baseline.py")),
        },
        "exclusions": exclusions,
        "cohort_rows": cohort.height,
        "identity_fallbacks": fallbacks.height,
        "penalty_rows_retained": cohort.filter(pl.col("penalty") == 1).height,
        "files": {
            "cohort": {
                "path": cohort_path.relative_to(root).as_posix(),
                "sha256": file_sha256(cohort_path),
            },
            "leaderboard": {
                "path": leaderboard_path.relative_to(root).as_posix(),
                "sha256": file_sha256(leaderboard_path),
            },
        },
        "display_min_dropbacks": args.min_dropbacks,
        "notes": [
            "All eligible participants are retained regardless of position.",
            "Penalty flags alone do not exclude a play.",
            "Null eligibility conditions fail the corresponding rule.",
            "Exclusion counts are sequential and mutually exclusive.",
            "The saved leaderboard includes every eligible participant.",
            "EPA is descriptive and has no additional opponent adjustment.",
        ],
    }
    write_json(manifest_path, manifest)

    print("Snapshot checksum verified.")
    print("\nSequential cohort exclusions:")
    for entry in exclusions:
        print(
            f"  {entry['rule']}: excluded {entry['excluded']:,}; "
            f"remaining {entry['remaining']:,}"
        )

    print(f"\nFinal dropbacks: {cohort.height:,}")
    print(f"Identity fallbacks: {fallbacks.height:,}")
    print(f"Player-season rows: {leaderboard.height:,}")

    league_reference = (
        leaderboard.select(
            "season",
            "league_dropbacks",
            "league_epa_per_dropback",
        )
        .unique()
        .sort("season")
    )

    print("\nLeague reference:")
    with pl.Config(float_precision=6):
        print(league_reference)

    displayed = leaderboard.filter(pl.col("dropbacks") >= args.min_dropbacks)

    for title, ranking_column in (
        ("Efficiency", "epa_per_dropback"),
        ("Accumulated value above average", "epa_above_average"),
    ):
        print(f"\n{title}: minimum {args.min_dropbacks} dropbacks")

        table = (
            displayed.sort(
                [ranking_column, "dropback_player_id"],
                descending=[True, False],
            )
            .select(
                "season",
                "player",
                "dropbacks",
                "epa_per_dropback",
                "epa_above_average",
            )
            .head(10)
        )

        with pl.Config(tbl_rows=10, float_precision=3):
            print(table)

    print(f"\nCohort: {cohort_path.relative_to(root)}")
    print(f"Leaderboard: {leaderboard_path.relative_to(root)}")
    print(f"Manifest: {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
