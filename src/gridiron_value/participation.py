"""Build a complete participation table from resolved snaps and player production."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import polars as pl

from gridiron_value import historical as h


JOIN_KEYS = ["season", "game_id", "team", "resolved_gsis_id"]


def build_participation(snaps, stats, unresolved=None):
    """Return all resolved snap participants plus an explicit production flag."""
    required_snaps = set(JOIN_KEYS + ["identity_status"])
    if not required_snaps.issubset(snaps.columns):
        raise ValueError(f"Snap table missing: {sorted(required_snaps - set(snaps.columns))}")
    snaps = snaps.filter(pl.col("identity_status").is_in(["resolved", "resolved_override"]))
    if snaps.select(JOIN_KEYS).unique().height != snaps.height:
        raise ValueError("Resolved snaps contain duplicate player-game keys.")
    if stats is None or stats.is_empty():
        return snaps.with_columns(pl.lit(False).alias("has_player_stats")), unresolved
    required_stats = {"season", "game_id", "team", "gsis_id"}
    if not required_stats.issubset(stats.columns):
        raise ValueError(f"Stats table missing: {sorted(required_stats - set(stats.columns))}")
    stats = stats.with_columns(pl.col("gsis_id").cast(pl.String).str.strip_chars())
    if stats.select(["season", "game_id", "team", "gsis_id"]).unique().height != stats.height:
        raise ValueError("Player stats contain duplicate player-game keys.")
    # Keep snap identity and participation fields as authoritative; add production fields
    # that do not duplicate join or identity columns.
    excluded = set(["season", "game_id", "team", "gsis_id", "player_id", "identity_status"])
    production = [c for c in stats.columns if c not in excluded]
    production = stats.select(["season", "game_id", "team", "gsis_id"] + production).with_columns(
        pl.lit(True).alias("_has_player_stats")
    )
    table = snaps.join(
        production,
        left_on=JOIN_KEYS,
        right_on=["season", "game_id", "team", "gsis_id"],
        how="left",
        suffix="_stats",
    ).with_columns(pl.col("_has_player_stats").fill_null(False).alias("has_player_stats")).drop("_has_player_stats")
    return table, unresolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-manifest", type=Path, required=True)
    parser.add_argument("--player-game-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.project_root.resolve()
    identity_path = h.locate(root, args.identity_manifest)
    player_game_path = h.locate(root, args.player_game_manifest)
    identity = h.read_json(identity_path)
    player_game = h.read_json(player_game_path)
    if identity["source_coverage_manifest"] != player_game["source_coverage_manifest"]:
        raise ValueError("Identity and player-game runs use different coverage snapshots.")
    snap_path = h.verify(root, identity["files"]["snap_counts_identified"])
    unresolved_path = h.verify(root, identity["files"]["snap_counts_unresolved"])
    stats_path = h.verify(root, player_game["files"]["player_game"])
    snaps = pl.read_parquet(snap_path)
    unresolved = pl.read_parquet(unresolved_path)
    production = pl.read_parquet(stats_path)
    # The player-game output already contains snap fields; use its production rows as a
    # source only after removing the joined snap columns to avoid duplicate fields.
    stats = production.drop(
        [c for c in ("offense_snaps", "offense_pct", "defense_snaps", "defense_pct", "st_snaps", "st_pct", "identity_status") if c in production.columns]
    )
    table, unresolved = build_participation(snaps, stats, unresolved)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"participation_{run}"
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, frame in (("participation", table), ("unresolved_snaps", unresolved),):
        path = output / f"{name}.parquet"
        frame.write_parquet(path, compression="zstd")
        files[name] = h.record(root, path)
    status_counts = table.group_by("has_player_stats").len().sort("has_player_stats").to_dicts()
    report = output / "report.md"
    report.write_text(
        f"# Participation table\n\nSeason: {identity['season']}. Resolved participants: {table.height:,}.\n\n"
        f"Production-attached rows: {table.filter(pl.col('has_player_stats')).height:,}. "
        f"Snap-only rows: {table.filter(~pl.col('has_player_stats')).height:,}. "
        f"Unresolved snap rows: {unresolved.height:,}.\n\n"
        "Snap-only rows are valid participation records and must not be interpreted as zero production. "
        "Unresolved rows are retained outside the keyed table until identity evidence is available.\n",
        encoding="utf-8",
    )
    files["report"] = h.record(root, report)
    manifest = output / "participation_manifest.json"
    h.save_json(manifest, {
        "run_id": run, "season": identity["season"],
        "source_identity_manifest": h.record(root, identity_path),
        "source_player_game_manifest": h.record(root, player_game_path),
        "rows": table.height, "unresolved_rows": unresolved.height,
        "status_counts": status_counts, "files": files,
        "code": h.record(root, Path(__file__).resolve()),
        "packages": {"polars": version("polars")},
        "limitations": [
            "Snap-only rows have no player-stat row; missing production remains null.",
            "Resolved participation is limited to the supplied snap feed.",
            "No individual assignment or blocking/coverage quality is inferred from snaps.",
        ],
    })
    print(f"Resolved participants: {table.height:,}")
    print(f"Production-attached: {table.filter(pl.col('has_player_stats')).height:,}")
    print(f"Snap-only: {table.filter(~pl.col('has_player_stats')).height:,}")
    print(f"Unresolved: {unresolved.height:,}")
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()
