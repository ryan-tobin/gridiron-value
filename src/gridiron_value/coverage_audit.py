import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import nflreadpy as nfl
import polars as pl
from nflreadpy.config import update_config

from gridiron_value import historical as h


FEEDS = {
    "pbp": ("load_pbp", {}),
    "player_stats": ("load_player_stats", {}),
    "team_stats": ("load_team_stats", {}),
    "rosters": ("load_rosters", {}),
    "schedules": ("load_schedules", {}),
    "snap_counts": ("load_snap_counts", {}),
    "ngs_passing": ("load_nextgen_stats", {"stat_type": "passing"}),
    "ngs_rushing": ("load_nextgen_stats", {"stat_type": "rushing"}),
    "ngs_receiving": ("load_nextgen_stats", {"stat_type": "receiving"}),
    "ftn_charting": ("load_ftn_charting", {}),
}
ID_FIELDS = {
    "player_stats": ("player_id", "gsis_id"),
    "snap_counts": ("pfr_player_id", "pfr_id"),
    "ngs_passing": ("player_gsis_id", "gsis_id"),
    "ngs_rushing": ("player_gsis_id", "gsis_id"),
    "ngs_receiving": ("player_gsis_id", "gsis_id"),
}


def select_season(frame, season):
    if "season" not in frame.columns:
        raise ValueError("Cannot verify requested season: season column absent.")
    return frame.filter(pl.col("season").cast(pl.Int64, strict=True) == season)


def valid_ids(frame, column):
    return {str(v).strip() for v in frame[column].drop_nulls().to_list() if str(v).strip()}


def profile(frame):
    result = {"rows": frame.height, "columns": len(frame.columns)}
    # Separate regular/postseason and aggregate week=0 rows when provided.
    kind = next((c for c in ("season_type", "game_type") if c in frame.columns), None)
    keys = ([kind] if kind else []) + (["week"] if "week" in frame.columns else [])
    result["week_counts"] = frame.group_by(keys).len().sort(keys).to_dicts() if keys else []
    for c in ("game_id", "nflverse_game_id", "team", "team_abbr", "posteam"):
        if c in frame.columns:
            result[f"unique_{c}"] = len(valid_ids(frame, c))
    for c in ("game_date", "gameday"):
        if c in frame.columns:
            dates = frame[c].drop_nulls().cast(pl.String)
            result[f"max_{c}"] = dates.max() if len(dates) else None
    result["fields"] = [
        {"column": c, "dtype": str(frame[c].dtype),
         "nulls": frame[c].null_count(),
         "nonfinite": int((~frame[c].is_finite()).fill_null(False).sum())
         if frame[c].dtype.is_float() else 0}
        for c in frame.columns
    ]
    return result


def identity_coverage(frames):
    roster = frames.get("rosters")
    result = []
    for name, (source_id, roster_id) in ID_FIELDS.items():
        frame = frames.get(name)
        row = {"feed": name, "source_id": source_id, "roster_id": roster_id}
        if frame is None or frame.is_empty() or roster is None or roster.is_empty():
            result.append({**row, "status": "not_evaluated", "reason": "Missing or empty feed/roster"})
            continue
        if source_id not in frame.columns or roster_id not in roster.columns:
            result.append({**row, "status": "not_evaluated", "reason": "Required ID column absent"})
            continue
        ids, known = valid_ids(frame, source_id), valid_ids(roster, roster_id)
        nonempty = frame[source_id].cast(pl.String).str.strip_chars().fill_null("") != ""
        duplicates = roster.filter(pl.col(roster_id).is_not_null()).group_by(roster_id).len()
        result.append({**row, "status": "evaluated", "distinct_ids": len(ids),
                       "missing_id_rows": frame.height - int(nonempty.sum()),
                       "matched_ids": len(ids & known), "unmatched_ids": sorted(ids - known),
                       "distinct_id_match_fraction": len(ids & known) / len(ids) if ids else None,
                       "roster_ids_with_multiple_rows": duplicates.filter(pl.col("len") > 1).height})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--feeds", nargs="+", choices=sorted(FEEDS), default=list(FEEDS))
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").exists():
        parser.error("Run from the repository root.")
    update_config(cache_mode="off", timeout=30)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"coverage_{run}"
    snapshots = root / "data" / "raw" / f"coverage_{run}"
    output.mkdir(parents=True, exist_ok=False)
    snapshots.mkdir(parents=True, exist_ok=False)
    frames, results = {}, {}
    manifest = output / "coverage_manifest.json"
    state = {"season": args.season, "run_id": run, "status": "running",
             "source": "nflverse via nflreadpy; loader cache disabled",
             "packages": {n: version(n) for n in ("nflreadpy", "polars")},
             "code": h.record(root, Path(__file__).resolve()), "feeds": results}
    h.save_json(manifest, state)
    for name in dict.fromkeys(args.feeds):
        loader, kwargs = FEEDS[name]
        print(f"Checking {name}...", flush=True)
        entry = {"loader": loader, "arguments": {"seasons": args.season, **kwargs},
                 "requested_at_utc": datetime.now(timezone.utc).isoformat()}
        try:
            loaded = getattr(nfl, loader)(args.season, **kwargs)
            # Preserve the complete returned frame, even if its season is wrong.
            path = snapshots / f"{name}.parquet"
            loaded.write_parquet(path, compression="zstd")
            entry["snapshot"] = h.record(root, path)
            entry["loaded_rows"] = loaded.height
            frame = select_season(loaded, args.season)
            frames[name] = frame
            entry.update(profile(frame))
            entry["status"] = "available" if frame.height else "empty_for_season"
        except Exception as error:
            entry.update(status="error", error_type=type(error).__name__, error=str(error)[:1200])
        entry["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        results[name] = entry
        h.save_json(manifest, state)
        print(f"  {entry['status']}: {entry.get('rows', 0):,} requested-season rows", flush=True)
    joins = identity_coverage(frames)
    state["identity_coverage"] = joins
    pbp, schedules = frames.get("pbp"), frames.get("schedules")
    if pbp is not None and schedules is not None and all(
        c in schedules.columns for c in ("game_id", "home_score", "away_score")
    ) and "game_id" in pbp.columns:
        scored = schedules.filter(pl.col("home_score").is_not_null() & pl.col("away_score").is_not_null())
        state["scored_schedule_games_missing_from_pbp"] = sorted(valid_ids(scored, "game_id") - valid_ids(pbp, "game_id"))
    lines = ["# Season data coverage audit", "", f"Requested season: {args.season}", "",
             "| Feed | Status | Season rows |", "|---|---|---:|"]
    for name, result in results.items():
        lines.append(f"| {name} | {result['status']} | {result.get('rows', 0):,} |")
    lines += ["", "## Identity coverage", ""]
    for row in joins:
        lines.append(f"- {row['feed']}: " + (
            f"{row['matched_ids']}/{row['distinct_ids']} distinct IDs found in rosters; "
            f"{row['missing_id_rows']} rows missing IDs."
            if row["status"] == "evaluated" else row["reason"]))
    lines += ["", "## Interpretation", "",
              "Available means rows were retrieved, not that the feed is complete or timely.",
              "Week counts, field null/nonfinite counts, ID mismatches and errors are in the manifest.",
              "Retrieval times are local observations, not upstream publication timestamps.",
              "Schedule dates may include future games. Non-null scores are not a verified final-game status.",
              "Roster ID membership is not a validated row-level join; multi-team roster rows require handling.",
              "No real-time latency or metric validity is established by this audit.",
              "nflverse documents postgame updates; participation since 2023 arrives after the season.",
              "FTN data attribution: FTN Data via nflverse (CC-BY-SA 4.0).", "",
              "Source schedule: https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html"]
    report = output / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    state.update(status="completed_with_errors" if any(r["status"] == "error" for r in results.values()) else "completed",
                 report=h.record(root, report))
    h.save_json(manifest, state)
    print(f"Report: {report.relative_to(root)}")
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()