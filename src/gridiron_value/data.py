import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import nflreadpy as nfl
import polars as pl
from nflreadpy.config import update_config

REQUIRED_COLUMNS = {
    "game_id",
    "play_id",
    "season",
    "season_type",
    "qb_dropback",
    "passer_player_id",
    "epa",
}

PACKAGES = (
    "gridiron-value",
    "nflreadpy",
    "polars",
    "numpy",
    "scipy",
    "scikit-learn",
    "matplotlib",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def summarize_schema(frame: pl.DataFrame) -> pl.DataFrame:
    records = []

    for name, dtype in frame.schema.items():
        column = frame.get_column(name)
        nulls = column.null_count()
        nans = 0

        if dtype in (pl.Float32, pl.Float64):
            nans = int(column.is_nan().sum())

        records.append(
            {
                "column": name,
                "dtype": str(dtype),
                "null_count": nulls,
                "nan_count": nans,
                "null_fraction": nulls / frame.height,
            }
        )

    return pl.DataFrame(records)


def audit_snapshot(frame: pl.DataFrame) -> dict:
    keys = frame.select("game_id", "play_id")

    season_summary = (
        frame.group_by("season", "season_type")
        .agg(
            pl.len().alias("rows"),
            pl.col("game_id").drop_nulls().n_unique().alias("games"),
        )
        .sort("season", "season_type")
    )

    flagged_dropbacks = frame.filter(
        (pl.col("season_type") == "REG") & (pl.col("qb_dropback") == 1)
    )

    invalid_epa = flagged_dropbacks.filter(
        ~pl.col("epa").is_finite().fill_null(False)
    ).height

    missing_passer = flagged_dropbacks.filter(
        pl.col("passer_player_id").is_null() | (pl.col("passer_player_id") == "")
    ).height

    return {
        "rows": frame.height,
        "columns": frame.width,
        "games": frame["game_id"].drop_nulls().n_unique(),
        "rows_with_missing_game_or_play_id": frame.filter(
            pl.col("game_id").is_null() | pl.col("play_id").is_null()
        ).height,
        "duplicate_key_rows_beyond_first": keys.height - keys.unique().height,
        "season_summary": season_summary.to_dicts(),
        "play_type_counts": (
            frame.group_by("play_type").len(name="rows").sort("play_type").to_dicts()
        ),
        "regular_season_flagged_dropbacks": flagged_dropbacks.height,
        "flagged_dropbacks_with_invalid_epa": invalid_epa,
        "flagged_dropbacks_with_missing_passer_id": missing_passer,
    }


def download_snapshot(season: int, project_root: Path) -> Path:
    project_root = project_root.resolve()

    if not (project_root / "pyproject.toml").is_file():
        raise ValueError("Project root must contain pyproject.toml")

    if not 1999 <= season < datetime.now(timezone.utc).year:
        raise ValueError("Choose a completed season from 1999 onward.")

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")

    update_config(cache_mode="off")

    print(f"Loading {season} play-by-play data...")
    frame = nfl.load_pbp([season])
    retrieved = datetime.now(timezone.utc)

    if frame.is_empty():
        raise ValueError("The loader returned an empty dataset.")

    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Required columns are missing: {sorted(missing)}")

    observed_seasons = frame["season"].drop_nulls().unique().to_list()

    if observed_seasons != [season] or frame["season"].null_count():
        raise ValueError("Returned season values do not match the request.")

    raw_dir = project_root / "data" / "raw" / run_id
    metadata_dir = project_root / "data" / "metadata" / run_id

    raw_dir.mkdir(parents=True, exist_ok=False)
    metadata_dir.mkdir(parents=True, exist_ok=False)

    raw_path = raw_dir / f"pbp_{season}.parquet"
    schema_path = metadata_dir / "schema.csv"
    manifest_path = metadata_dir / "manifest.json"

    frame.write_parquet(raw_path, compression="zstd")
    summarize_schema(frame).write_csv(schema_path)

    audit = audit_snapshot(frame)

    manifest = {
        "run_id": run_id,
        "started_at_utc": started.isoformat(),
        "retrieved_at_utc": retrieved.isoformat(),
        "season_requested": season,
        "source": {
            "repository": "https://github.com/nflverse/nflverse-data",
            "release": "https://github.com/nflverse/nflverse-data/releases/tag/pbp",
            "loader": "nflreadpy.load_pbp",
            "cache_mode": "off",
        },
        "snapshot_description": (
            "Unfiltered nflreadpy output serialized locally as Parquet. "
            "Checksums identify local files, not original upstream bytes."
        ),
        "files": {
            "raw": {
                "path": raw_path.relative_to(project_root).as_posix(),
                "bytes": raw_path.stat().st_size,
                "sha256": file_sha256(raw_path),
            },
            "schema": {
                "path": schema_path.relative_to(project_root).as_posix(),
                "sha256": file_sha256(schema_path),
            },
        },
        "environment": {
            "python": platform.python_version(),
            "packages": {package: version(package) for package in PACKAGES},
        },
        "audit": audit,
    }

    write_json(manifest_path, manifest)

    print(f"Rows: {audit['rows']:,}")
    print(f"Columns: {audit['columns']:,}")
    print(f"Games: {audit['games']:,}")

    print("\nSeason coverage:")
    for group in audit["season_summary"]:
        print(
            f"  {group['season_type']}: "
            f"{group['rows']:,} rows across {group['games']:,} games"
        )

    print("\nAudit:")
    for key in (
        "rows_with_missing_game_or_play_id",
        "duplicate_key_rows_beyond_first",
        "regular_season_flagged_dropbacks",
        "flagged_dropbacks_with_invalid_epa",
        "flagged_dropbacks_with_missing_passer_id",
    ):
        print(f"  {key}: {audit[key]:,}")

    print(f"\nRaw snapshot: {raw_path.relative_to(project_root)}")
    print(f"Schema: {schema_path.relative_to(project_root)}")
    print(f"Manifest: {manifest_path.relative_to(project_root)}")

    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and audit an NFL play-by-play snapshot."
    )
    parser.add_argument("--season", type=int, default=2024)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    download_snapshot(args.season, args.project_root)


if __name__ == "__main__":
    main()
