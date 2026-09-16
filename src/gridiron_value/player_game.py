"""Build auditable player-game tables from the coverage and identity snapshots."""

import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import polars as pl

from gridiron_value import historical as h


NGS_COLUMNS = {
    "passing": ["avg_time_to_throw", "expected_completion_percentage", "completion_percentage_above_expectation"],
    "rushing": ["expected_rush_yards", "rush_yards_over_expected", "rush_yards_over_expected_per_att"],
    "receiving": ["avg_separation", "avg_expected_yac", "avg_yac_above_expectation"],
}


def _read_snapshot(root, audit, name, season):
    entry = audit["feeds"].get(name)
    if not entry or entry["status"] not in ("available", "empty_for_season"):
        raise ValueError(f"Feed unavailable: {name}")
    frame = pl.read_parquet(h.verify(root, entry["snapshot"]))
    if "season" in frame.columns:
        frame = frame.filter(pl.col("season") == season)
    return frame, entry["snapshot"]


def _numeric_columns(frame, exclude):
    return [c for c in frame.columns if c not in exclude and frame[c].dtype.is_numeric()]


def build_player_game(stats, snaps, ngs):
    """Join using stable IDs and game/week keys; return table plus audit tables."""
    required = {"player_id", "game_id", "team", "week", "season"}
    if not required.issubset(stats.columns):
        raise ValueError(f"Player stats missing: {sorted(required - set(stats.columns))}")
    stats, anonymous = stats.filter(pl.col("player_id").is_not_null()), stats.filter(pl.col("player_id").is_null())
    stats = stats.with_columns(pl.col("player_id").cast(pl.String).str.strip_chars().alias("gsis_id"))
    if stats.select("season", "week", "game_id", "gsis_id", "team").unique().height != stats.height:
        raise ValueError("Player stats contain duplicate player-game keys.")
    snap_keys = ["season", "game_id", "team", "resolved_gsis_id"]
    snaps = snaps.filter(pl.col("identity_status").is_in(["resolved", "resolved_override"]))
    if snaps.select(*snap_keys).unique().height != snaps.height:
        raise ValueError("Resolved snaps contain duplicate player-game keys.")
    snap_cols = [c for c in ("resolved_gsis_id", "offense_snaps", "offense_pct", "defense_snaps", "defense_pct", "st_snaps", "st_pct", "identity_status") if c in snaps.columns]
    joined = stats.join(snaps.select(snap_keys + [c for c in snap_cols if c not in snap_keys]), left_on=["season", "game_id", "team", "gsis_id"], right_on=snap_keys, how="left", suffix="_snap")
    unmatched_snaps = snaps.join(stats.select(["season", "game_id", "team", "gsis_id"]).unique(), left_on=snap_keys, right_on=["season", "game_id", "team", "gsis_id"], how="anti")
    for kind, frame in ngs.items():
        if frame.is_empty():
            continue
        key = ["season", "week", "team_abbr", "player_gsis_id"]
        if not set(key).issubset(frame.columns):
            raise ValueError(f"NGS {kind} missing join fields.")
        value_cols = [c for c in NGS_COLUMNS[kind] if c in frame.columns]
        frame = frame.filter(pl.col("week") != 0).select(key + value_cols).rename({c: f"ngs_{kind}_{c}" for c in value_cols})
        if frame.select(key).unique().height != frame.height:
            raise ValueError(f"NGS {kind} has duplicate player-week keys.")
        joined = joined.join(frame, left_on=["season", "week", "team", "gsis_id"], right_on=key, how="left")
    return joined, anonymous, unmatched_snaps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-manifest", type=Path, required=True)
    parser.add_argument("--identity-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.project_root.resolve()
    audit_path = h.locate(root, args.coverage_manifest)
    identity_path = h.locate(root, args.identity_manifest)
    audit, identity = h.read_json(audit_path), h.read_json(identity_path)
    if identity["source_coverage_manifest"] != h.record(root, audit_path):
        raise ValueError("Identity and coverage manifests refer to different snapshots.")
    season = audit["season"]
    stats, stats_info = _read_snapshot(root, audit, "player_stats", season)
    snap_path = h.verify(root, identity["files"]["snap_counts_identified"])
    snaps = pl.read_parquet(snap_path).filter(pl.col("season") == season)
    ngs, sources = {}, {"player_stats": stats_info, "snaps": identity["files"]["snap_counts_identified"]}
    for kind in NGS_COLUMNS:
        frame, info = _read_snapshot(root, audit, f"ngs_{kind}", season)
        ngs[kind], sources[f"ngs_{kind}"] = frame, info
    joined, anonymous, unmatched = build_player_game(stats, snaps, ngs)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"player_game_{run}"
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, frame in (("player_game", joined), ("unattributed_stats", anonymous), ("unmatched_resolved_snaps", unmatched)):
        path = output / f"{name}.parquet"
        frame.write_parquet(path, compression="zstd")
        files[name] = h.record(root, path)
    report = output / "report.md"
    report.write_text(f"# Player-game table\n\nSeason: {season}. Rows: {joined.height:,}.\n\n"
                      "Player statistics are the base. Resolved snaps and Week 1 NGS fields are left-null when no verified key join exists. "
                      "Week 0 NGS aggregate rows were excluded. Anonymous player-stat rows and unmatched snap rows are preserved separately.\n",
                      encoding="utf-8")
    files["report"] = h.record(root, report)
    manifest = output / "player_game_manifest.json"
    h.save_json(manifest, {"run_id": run, "season": season, "source_coverage_manifest": h.record(root, audit_path), "source_identity_manifest": h.record(root, identity_path), "sources": sources, "rows": joined.height, "unattributed_stats_rows": anonymous.height, "unmatched_resolved_snap_rows": unmatched.height, "files": files, "code": h.record(root, Path(__file__).resolve()), "packages": {n: version(n) for n in ("polars",)}, "limitations": ["Stats are current snapshot values, not a certified live feed.", "Missing snaps remain null and are not interpreted as zero.", "NGS week 0 aggregates are not joined to player-game rows."]})
    print(f"Player-game rows: {joined.height:,}")
    print(f"Unattributed stats: {anonymous.height}")
    print(f"Unmatched resolved snaps: {unmatched.height}")
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()
