import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from gridiron_value import historical as h


def load_rows(root, manifest_path):
    manifest = h.read_json(manifest_path)
    if manifest.get("status") != "completed":
        raise ValueError("Historical batch must be completed.")
    source = h.verify(root, manifest["files"]["historical_pass_plus"])
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Historical table is empty.")
    seen = set()
    for row in rows:
        for field in ("season", "dropbacks", "games"):
            row[field] = int(row[field])
        for field in (
            "pass_plus", "total_epa", "opponent_adjusted_epa_per_dropback",
            "opponent_adjusted_epa_above_average",
        ):
            row[field] = float(row[field])
            if not math.isfinite(row[field]):
                raise ValueError(f"Nonfinite {field}.")
        key = (row["season"], row["dropback_player_id"])
        if not key[1] or key in seen or row["dropbacks"] <= 0:
            raise ValueError(f"Invalid or duplicate player-season: {key}")
        seen.add(key)
    return rows, source


def rank_rows(rows, minimum):
    selected = sorted(
        (r for r in rows if r["dropbacks"] >= minimum),
        key=lambda r: (r["season"], -r["pass_plus"], r["dropback_player_id"]),
    )
    output = []
    season, previous, rank, position = None, None, 0, 0
    for row in selected:
        if row["season"] != season:
            season, previous, position = row["season"], None, 0
        position += 1
        if row["pass_plus"] != previous:
            rank = position
        previous = row["pass_plus"]
        output.append({"rank": rank, **row})
    return output


def summarize_players(rows, minimum):
    groups = defaultdict(list)
    for row in rows:
        groups[row["dropback_player_id"]].append(row)
    output = []
    for player_id, seasons in sorted(groups.items()):
        seasons.sort(key=lambda r: r["season"])
        count = sum(r["dropbacks"] for r in seasons)
        output.append({
            "dropback_player_id": player_id,
            "player": seasons[-1]["player"],
            "first_observed_season": seasons[0]["season"],
            "last_observed_season": seasons[-1]["season"],
            "observed_seasons": len(seasons),
            "qualified_seasons": sum(r["dropbacks"] >= minimum for r in seasons),
            "qualified_seasons_above_100": sum(
                r["dropbacks"] >= minimum and r["pass_plus"] > 100
                for r in seasons
            ),
            "dropbacks": count,
            "dropback_weighted_season_pass_plus": sum(
                r["dropbacks"] * r["pass_plus"] for r in seasons
            ) / count,
            "pooled_adjusted_epa_per_dropback": sum(
                r["dropbacks"] * r["opponent_adjusted_epa_per_dropback"]
                for r in seasons
            ) / count,
            "sum_season_adjusted_epa_above_average": sum(
                r["opponent_adjusted_epa_above_average"] for r in seasons
            ),
        })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-dropbacks", type=int, default=200)
    parser.add_argument("--thresholds", nargs="+", type=int, default=[100, 200, 300, 400])
    args = parser.parse_args()
    if min([args.min_dropbacks, *args.thresholds]) < 1:
        parser.error("Dropback thresholds must be positive.")
    root = args.project_root.resolve()
    manifest_path = h.locate(root, args.historical_manifest)
    rows, source = load_rows(root, manifest_path)
    leaders = rank_rows(rows, args.min_dropbacks)
    if not leaders:
        parser.error("No player-seasons qualify at the selected display minimum.")
    thresholds = sorted(set([args.min_dropbacks, *args.thresholds]))
    sensitivity = []
    for threshold in thresholds:
        ranked = rank_rows(rows, threshold)
        for season in sorted({r["season"] for r in rows}):
            group = [r for r in ranked if r["season"] == season]
            sensitivity.append({
                "season": season, "min_dropbacks": threshold,
                "qualifiers": len(group),
                "qualified_dropbacks": sum(r["dropbacks"] for r in group),
                "leader_ids": "|".join(r["dropback_player_id"] for r in group if r["rank"] == 1),
                "leader_names": "|".join(r["player"] for r in group if r["rank"] == 1),
            })
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"historical_analysis_{batch}"
    output.mkdir(parents=True, exist_ok=False)
    tables = {
        "season_leaderboards": leaders,
        "player_history": sorted(rows, key=lambda r: (r["dropback_player_id"], r["season"])),
        "player_window_summary": summarize_players(rows, args.min_dropbacks),
        "threshold_summary": sensitivity,
        "threshold_leaderboards": [
            {"min_dropbacks": threshold, **r}
            for threshold in thresholds for r in rank_rows(rows, threshold)
        ],
    }
    files = {}
    for name, table in tables.items():
        path = output / f"{name}.csv"
        h.save_csv(path, table)
        files[name] = h.record(root, path)
    report = output / "report.md"
    report.write_text(
        "# Historical Pass+ analysis\n\n"
        f"Seasons: {min(r['season'] for r in rows)}–{max(r['season'] for r in rows)}. "
        f"Player-seasons: {len(rows):,}. Dropbacks: {sum(r['dropbacks'] for r in rows):,}.\n\n"
        f"Display minimum: {args.min_dropbacks}; qualifying player-seasons: {len(leaders)}.\n\n"
        "Scores and season references are unchanged. Ranks use unrounded scores; exact ties share rank.\n\n"
        "Player summaries include ALL observed seasons, including below-threshold seasons. "
        "They describe this dataset's window, not necessarily complete careers. "
        "The weighted season index averages season-relative scores; it is not a newly calibrated career Pass+. "
        "Pooled EPA rates span different scoring environments.\n\n"
        "Threshold tables describe eligibility and display-rank sensitivity, not statistical rank stability. "
        "Saved resampling ranges remain conditional diagnostics, not talent confidence intervals. "
        "This report does not establish forecasting performance.\n",
        encoding="utf-8",
    )
    files["report"] = h.record(root, report)
    h.save_json(output / "analysis_manifest.json", {
        "created_utc": batch, "source_manifest": h.record(root, manifest_path),
        "source_table": h.record(root, source),
        "analysis_code": h.record(root, Path(__file__).resolve()),
        "min_dropbacks": args.min_dropbacks, "thresholds": thresholds,
        "files": files,
    })
    print(f"Report: {report.relative_to(root)}")
    print(f"Player-seasons: {len(rows):,}; qualified: {len(leaders):,}")
    print(f"Manifest: {(output / 'analysis_manifest.json').relative_to(root)}")


if __name__ == "__main__":
    main()
