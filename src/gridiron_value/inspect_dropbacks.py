import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from gridiron_value.data import file_sha256


def clean_id(column: str) -> pl.Expr:
    """Treat null or blank player identifiers as missing."""
    return pl.col(column).cast(pl.String).str.strip_chars().replace("", None)


def inspect_dropbacks(manifest_path: Path, project_root: Path) -> None:
    """Inspect a verified local snapshot without modifying its source data."""
    project_root = project_root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    raw_path = project_root / manifest["files"]["raw"]["path"]
    expected_hash = manifest["files"]["raw"]["sha256"]

    if file_sha256(raw_path) != expected_hash:
        raise ValueError("Raw snapshot checksum does not match the manifest.")

    frame = pl.read_parquet(raw_path)

    required = {
        "game_id",
        "play_id",
        "season_type",
        "play_type",
        "qb_dropback",
        "qb_scramble",
        "sack",
        "qb_spike",
        "qb_kneel",
        "two_point_attempt",
        "penalty",
        "passer_id",
        "passer_player_id",
        "rusher_player_id",
        "epa",
        "desc",
    }

    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Inspection columns are missing: {sorted(missing)}")

    dropbacks = (
        frame.filter((pl.col("season_type") == "REG") & (pl.col("qb_dropback") == 1))
        .with_columns(
            clean_id("passer_id").alias("standard_id"),
            clean_id("passer_player_id").alias("raw_passer_id"),
            clean_id("rusher_player_id").alias("raw_rusher_id"),
        )
        .with_columns(
            pl.when(pl.col("qb_scramble") == 1)
            .then(pl.col("raw_rusher_id"))
            .otherwise(pl.col("raw_passer_id"))
            .alias("event_player_id")
        )
    )

    missing_raw = dropbacks.filter(pl.col("raw_passer_id").is_null())
    missing_standard = dropbacks.filter(pl.col("standard_id").is_null())

    # Compare identifiers only where both sources identify a player.
    conflicts = dropbacks.filter(
        pl.col("standard_id").is_not_null()
        & pl.col("event_player_id").is_not_null()
        & (pl.col("standard_id") != pl.col("event_player_id"))
    )

    missing_breakdown = (
        missing_raw.group_by("play_type", "qb_scramble", "sack", "qb_spike")
        .len(name="rows")
        .sort("rows", descending=True)
    )

    diagnostics = {
        "regular_season_flagged_dropbacks": dropbacks.height,
        "missing_raw_passer_id": missing_raw.height,
        "missing_standard_passer_id": missing_standard.height,
        "missing_raw_with_standard_id_available": missing_raw.filter(
            pl.col("standard_id").is_not_null()
        ).height,
        "standard_vs_event_id_conflicts": conflicts.height,
        "scrambles": dropbacks.filter(pl.col("qb_scramble") == 1).height,
        "sacks": dropbacks.filter(pl.col("sack") == 1).height,
        "spikes": dropbacks.filter(pl.col("qb_spike") == 1).height,
        "kneels": dropbacks.filter(pl.col("qb_kneel") == 1).height,
        "two_point_attempts": dropbacks.filter(pl.col("two_point_attempt") == 1).height,
        "penalty_flagged_rows": dropbacks.filter(pl.col("penalty") == 1).height,
        "no_play_rows": dropbacks.filter(pl.col("play_type") == "no_play").height,
    }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = project_root / "reports" / "tables" / f"dropback_audit_{run_id}"
    output_dir.mkdir(parents=True, exist_ok=False)

    review_columns = [
        "game_id",
        "play_id",
        "play_type",
        "qb_scramble",
        "sack",
        "qb_spike",
        "penalty",
        "standard_id",
        "raw_passer_id",
        "raw_rusher_id",
        "event_player_id",
        "epa",
        "desc",
    ]

    missing_raw.select(review_columns).write_csv(output_dir / "missing_raw_passer.csv")
    missing_standard.select(review_columns).write_csv(
        output_dir / "missing_standard_passer.csv"
    )
    conflicts.select(review_columns).write_csv(output_dir / "identity_conflicts.csv")
    missing_breakdown.write_csv(output_dir / "missing_raw_breakdown.csv")

    summary = {
        "source_run_id": manifest["run_id"],
        "source_sha256": expected_hash,
        "diagnostics": diagnostics,
        "missing_raw_breakdown": missing_breakdown.to_dicts(),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print("Snapshot checksum verified.")

    for name, count in diagnostics.items():
        print(f"{name}: {count:,}")

    print("\nMissing raw passer IDs by play category:")
    print(missing_breakdown)

    print(f"\nReview files: {output_dir.relative_to(project_root)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect quarterback identifiers and dropback flags."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    inspect_dropbacks(args.manifest, args.project_root)


if __name__ == "__main__":
    main()
