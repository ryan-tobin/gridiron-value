"""Build a reproducible, cross-stage project progress report."""

import argparse
import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from gridiron_value import historical as h


STAGES = (
    ("historical_analysis", "Historical Pass+"),
    ("chronological", "Chronological validation"),
    ("coverage", "Current-season coverage"),
    ("identity", "Player identity"),
    ("player_game", "Player-game joins"),
    ("participation", "Participation"),
    ("position_metrics", "Position metrics"),
)


def _relative(root, path):
    path = Path(path)
    if not path.is_absolute():
        path = root / path
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _record(root, path):
    path = Path(path).resolve()
    try:
        return h.record(root, path)
    except ValueError:
        # Tests may execute the package from a source tree outside a temporary
        # project root. Source artifacts in a real run are always project-local.
        return {"path": str(path), "sha256": h.sha256(path)}


def _read_csv(root, record):
    if not record:
        return []
    path = h.verify(root, record)
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _manifest_summary(root, name, path, state):
    """Return one human-readable milestone row for a pipeline manifest."""
    row = {"stage": name, "label": dict(STAGES).get(name, name),
           "status": "available", "rows": "", "detail": "",
           "manifest": _relative(root, path)}
    if name == "historical_analysis":
        source = _read_csv(root, state.get("source_table"))
        leaders = _read_csv(root, state.get("files", {}).get("season_leaderboards"))
        row.update(
            status="completed",
            rows=str(len(source)),
            detail=(f"{min((int(r['season']) for r in source), default=0)}–"
                    f"{max((int(r['season']) for r in source), default=0)}; "
                    f"{len(leaders):,} qualifying display rows at "
                    f"{state.get('min_dropbacks', '?')} dropbacks"),
        )
    elif name == "chronological":
        rows = _read_csv(root, state.get("files", {}).get("season_scores"))
        seasons = sorted({r.get("season", "") for r in rows if r.get("season")})
        row.update(status="completed", rows=str(len(seasons)),
                   detail=f"{len(seasons)} seasons; first test week "
                   f"{state.get('settings', {}).get('first_test_week', '?')}")
    elif name == "coverage":
        feeds = state.get("feeds", {})
        available = sum(v.get("status") == "available" for v in feeds.values())
        row.update(status=state.get("status", "unknown"), rows=str(len(feeds)),
                   detail=f"{available}/{len(feeds)} feeds available for "
                   f"season {state.get('season', '?')}")
    elif name == "identity":
        counts = {r["identity_status"]: r["len"]
                  for r in state.get("snap_status_counts", [])}
        row.update(status=state.get("status", "unknown"),
                   rows=str(state.get("snap_input_rows", "")),
                   detail=(f"{counts.get('resolved', 0):,} resolved; "
                           f"{counts.get('resolved_override', 0):,} override; "
                           f"{sum(v for k, v in counts.items() if k not in ('resolved', 'resolved_override')):,} unresolved"))
    elif name == "player_game":
        row.update(status="completed", rows=str(state.get("rows", "")),
                   detail=(f"{state.get('unattributed_stats_rows', 0):,} unattributed stats; "
                           f"{state.get('unmatched_resolved_snap_rows', 0):,} unmatched resolved snaps"))
    elif name == "participation":
        row.update(status="completed", rows=str(state.get("rows", "")),
                   detail=(f"{state.get('rows', 0):,} resolved participants; "
                           f"{state.get('unresolved_rows', 0):,} unresolved"))
    elif name == "position_metrics":
        metric_rows = state.get("rows")
        if metric_rows is None:
            all_file = state.get("files", {}).get("all")
            if all_file:
                metric_rows = pl.read_parquet(h.verify(root, all_file)).height
        row.update(
            status="completed",
            rows="" if metric_rows is None else str(metric_rows),
            detail="QB/RB/WR/TE metric tables plus all-position output",
        )
    return row


def coverage_rows(state):
    """Flatten feed and ID coverage into a CSV-friendly table."""
    rows = []
    for feed, item in state.get("feeds", {}).items():
        rows.append({"kind": "feed", "name": feed,
                     "status": item.get("status", "unknown"),
                     "rows": item.get("rows", 0),
                     "match_fraction": "", "detail": ""})
    for item in state.get("identity_coverage", []):
        fraction = item.get("distinct_id_match_fraction")
        rows.append({"kind": "identity", "name": item.get("feed", ""),
                     "status": item.get("status", "unknown"), "rows": "",
                     "match_fraction": "" if fraction is None else fraction,
                     "detail": (f"{item.get('matched_ids', 0)}/"
                                f"{item.get('distinct_ids', 0)} distinct IDs matched")})
    return rows


def chronological_rows(root, state):
    """Create one row per season with offense-only versus defense-adjusted MSE."""
    table = _read_csv(root, state.get("files", {}).get("season_scores"))
    by_season = {}
    for row in table:
        season = int(row["season"])
        by_season.setdefault(season, {})[row["model"]] = float(row["mse"])
    output = []
    for season in sorted(by_season):
        values = by_season[season]
        offense = values.get("offense_only")
        defense = values.get("offense_defense")
        output.append({"season": season, "offense_only_mse": offense,
                       "offense_defense_mse": defense,
                       "defense_mse_gain": None if offense is None or defense is None
                       else offense - defense})
    return output


def _markdown_table(headers, rows):
    def normalize(value):
        return str(value).casefold().replace("_", " ").replace("-", " ")

    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]

    for row in rows:
        normalized = {normalize(key): value for key, value in row.items()}
        lines.append(
            "| "
            + " | ".join(
                str(normalized.get(normalize(header), ""))
                for header in headers
            )
            + " |"
        )

    return "\n".join(lines)


def _svg_bars(values, label_key, value_key, width=720, height=240):
    values = [v for v in values if v.get(value_key) is not None]
    if not values:
        return '<p class="muted">No chart data available.</p>'
    maximum = max(float(v[value_key]) for v in values) or 1.0
    row_height = max(22, (height - 20) / len(values))
    bars = []
    for index, value in enumerate(values):
        label = html.escape(str(value[label_key]))
        numeric = float(value[value_key])
        y = 8 + index * row_height
        bar_width = max(1, 520 * numeric / maximum)
        bars.append(
            f'<text x="0" y="{y + 15:.1f}" class="axis">{label}</text>'
            f'<rect x="120" y="{y:.1f}" width="{bar_width:.1f}" height="16" class="bar"/>'
            f'<text x="{130 + bar_width:.1f}" y="{y + 14:.1f}" class="value">{numeric:,.3f}</text>'
        )
    return (f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Bar chart of {html.escape(value_key)} by {html.escape(label_key)}">'
            + "".join(bars) + "</svg>")


def render_html(milestones, coverage, chronological, generated):
    available = [r for r in coverage if r["kind"] == "feed"]
    chart = _svg_bars(available, "name", "rows", height=max(80, 30 * len(available)))
    gain_values = [{"season": r["season"], "gain": r["defense_mse_gain"]}
                   for r in chronological if r["defense_mse_gain"] is not None]
    gain_chart = _svg_bars(gain_values, "season", "gain",
                           height=max(80, 30 * len(gain_values)))
    milestone_rows = "".join(
        f"<tr><td>{html.escape(str(r['label']))}</td>"
        f"<td>{html.escape(str(r['status']))}</td>"
        f"<td>{html.escape(str(r['rows']))}</td>"
        f"<td>{html.escape(str(r['detail']))}</td></tr>" for r in milestones
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gridiron Value project status</title>
<style>
:root {{ color-scheme: light dark; --bg: light-dark(#f7f8fa,#11151c); --panel: light-dark(#fff,#1b222c); --text: light-dark(#17202a,#eef2f7); --muted: light-dark(#5e6a78,#aab5c3); --line: light-dark(#dfe4ea,#33404f); --accent: #3d8bfd; }}
body {{ margin: 0; padding: 32px; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, sans-serif; }}
main {{ max-width: 1080px; margin: auto; }} h1 {{ margin-bottom: 4px; }} h2 {{ margin-top: 32px; }} .muted {{ color: var(--muted); }}
table {{ width: 100%; border-collapse: collapse; background: var(--panel); }} th,td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); }} th {{ font-weight: 600; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(320px,1fr)); gap: 24px; }} .chart {{ background: var(--panel); padding: 16px; overflow-x: auto; }}
svg {{ width: 100%; height: auto; min-width: 360px; }} .axis,.value {{ fill: var(--text); font-size: 12px; }} .bar {{ fill: var(--accent); }}
</style></head><body><main>
<h1>Gridiron Value project status</h1><p class="muted">Generated {html.escape(generated)}. All values are sourced from timestamped pipeline manifests.</p>
<h2>Milestones</h2><table><thead><tr><th>Stage</th><th>Status</th><th>Rows / units</th><th>Detail</th></tr></thead><tbody>{milestone_rows}</tbody></table>
<div class="grid"><section><h2>Feed coverage</h2><div class="chart">{chart}</div></section><section><h2>Chronological defense MSE gain</h2><div class="chart">{gain_chart}</div></section></div>
</main></body></html>"""


def build_status(root, manifest_paths, output_dir=None):
    if not manifest_paths:
        raise ValueError("At least one pipeline manifest is required.")
    loaded = {}
    source_records = {}
    for name, path in manifest_paths.items():
        located = h.locate(root, path)
        loaded[name] = h.read_json(located)
        source_records[name] = _record(root, located)
    milestones = [_manifest_summary(root, name, manifest_paths[name], loaded[name])
                  for name, _ in STAGES if name in loaded]
    coverage = coverage_rows(loaded["coverage"]) if "coverage" in loaded else []
    chronological = (chronological_rows(root, loaded["chronological"])
                     if "chronological" in loaded else [])
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path(output_dir) if output_dir else root / "reports" / "tables" / f"project_status_{run}"
    output = output if output.is_absolute() else root / output
    output.mkdir(parents=True, exist_ok=False)
    milestone_file = output / "milestones.csv"
    coverage_file = output / "coverage_summary.csv"
    chrono_file = output / "chronological_validation.csv"
    for path, rows in ((milestone_file, milestones), (coverage_file, coverage),
                       (chrono_file, chronological)):
        if rows:
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
        else:
            path.write_text("", encoding="utf-8")
    report_lines = ["# Gridiron Value project status", "",
                    f"Generated: {datetime.now(timezone.utc).isoformat()}", "",
                    "All values below are sourced from the supplied timestamped manifests.", "",
                    "## Milestones", "",
                    _markdown_table(["Stage", "Status", "Rows / units", "Detail"],
                                    [{"Stage": r["label"], "Status": r["status"],
                                      "Rows / units": r["rows"], "Detail": r["detail"]}
                                     for r in milestones]), ""]
    if "coverage" in loaded:
        report_lines += [
            "## Coverage",
            "",
            _markdown_table(
                ["Kind", "Name", "Status", "Rows", "Match fraction", "Detail"],
                coverage,
            ),
            "",
        ]
    if chronological:
        report_lines += ["## Chronological validation", "",
                         _markdown_table(["Season", "Offense-only MSE", "Offense + defense MSE", "Defense MSE gain"],
                                         [{"Season": r["season"],
                                           "Offense-only MSE": f"{r['offense_only_mse']:.6f}",
                                           "Offense + defense MSE": f"{r['offense_defense_mse']:.6f}",
                                           "Defense MSE gain": f"{r['defense_mse_gain']:+.6f}"}
                                          for r in chronological]), ""]
    report_lines += ["## Source manifests", "",
                     _markdown_table(["Stage", "Manifest"],
                                     [{"Stage": name, "Manifest": record["path"]}
                                      for name, record in source_records.items()]), ""]
    report = output / "report.md"
    report.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    page = output / "dashboard.html"
    page.write_text(render_html(milestones, coverage, chronological,
                                datetime.now(timezone.utc).isoformat()), encoding="utf-8")
    files = {name: _record(root, path) for name, path in (
        ("report", report), ("dashboard", page), ("milestones", milestone_file),
        ("coverage", coverage_file), ("chronological", chrono_file))}
    state = {"run_id": run, "status": "completed", "sources": source_records,
             "files": files, "code": _record(root, Path(__file__).resolve()),
             "milestones": milestones}
    manifest = output / "project_status_manifest.json"
    h.save_json(manifest, state)
    return manifest, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--coverage-manifest", type=Path)
    parser.add_argument("--historical-analysis-manifest", type=Path)
    parser.add_argument("--chronological-manifest", type=Path)
    parser.add_argument("--identity-manifest", type=Path)
    parser.add_argument("--player-game-manifest", type=Path)
    parser.add_argument("--participation-manifest", type=Path)
    parser.add_argument("--position-metrics-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    paths = {name: value for name, value in {
        "coverage": args.coverage_manifest,
        "historical_analysis": args.historical_analysis_manifest,
        "chronological": args.chronological_manifest,
        "identity": args.identity_manifest,
        "player_game": args.player_game_manifest,
        "participation": args.participation_manifest,
        "position_metrics": args.position_metrics_manifest,
    }.items() if value is not None}
    try:
        manifest, _ = build_status(args.project_root.resolve(), paths, args.output_dir)
    except (ValueError, FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(f"Report: {manifest.parent / 'report.md'}")
    print(f"Dashboard: {manifest.parent / 'dashboard.html'}")
    print(f"Manifest: {manifest}")


if __name__ == "__main__":
    main()
