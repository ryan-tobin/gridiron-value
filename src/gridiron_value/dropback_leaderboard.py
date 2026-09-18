"""Build a descriptive leaderboard from verified eligible-dropback profiles."""

import argparse
import html
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridiron_value import historical as h
from gridiron_value import leaderboards as lb
from gridiron_value import player_profile as pp
from gridiron_value import profile_dropbacks as pd

SCHEMA = {
    "rank": pl.Int64,
    "gsis_id": pl.String,
    "player": pl.String,
    "teams": pl.String,
    "positions": pl.String,
    "matched_games": pl.Int64,
    "profile_games": pl.Int64,
    "eligible_dropbacks": pl.Int64,
    "total_epa": pl.Float64,
    "epa_per_eligible_dropback": pl.Float64,
    "qualified": pl.Boolean,
    "status": pl.String,
}

NOTES = [
    "Observed regular-season production from fixed snapshots; not a full-season completeness claim.",
    "Qualification thresholds are provisional display rules, not validated reliability cutoffs.",
    "Ranking requires a dropback match for every observed profile game; missing matches are not zero-filled.",
    "EPA per eligible dropback is summed eligible EPA divided by summed eligible dropbacks.",
    "EPA retains teammate and scheme contributions. No opponent adjustment, Pass+, or talent estimate is produced.",
    "Ranks use unrounded rates. Exactly equal rates share a competition rank; displayed rounded values may look tied.",
]


def compare(profiles, min_dropbacks=100, min_games=3):
    if any(
        type(value) is not int or value < 1
        for value in (min_dropbacks, min_games)
    ):
        raise ValueError(
            "Minimum dropbacks and games must be positive integers."
        )

    if not profiles or not pd.validate(profiles, profiles[0]["season"]):
        raise ValueError("Profiles need the dropback integration first.")

    rows = []

    for profile in profiles:
        summary = profile["dropbacks"]
        reasons = []

        if summary["matched_games"] == 0:
            reasons.append("no matched dropbacks")
        else:
            if summary["matched_games"] != summary["profile_games"]:
                reasons.append("missing game matches")

            if summary["eligible_dropbacks"] < min_dropbacks:
                reasons.append("below minimum dropbacks")

            if summary["matched_games"] < min_games:
                reasons.append("below minimum games")

        rows.append(
            {
                "rank": None,
                "gsis_id": profile["gsis_id"],
                "player": profile["name"],
                "teams": " | ".join(profile["teams"]),
                "positions": " | ".join(profile["positions"]),
                "matched_games": summary["matched_games"],
                "profile_games": summary["profile_games"],
                "eligible_dropbacks": summary["eligible_dropbacks"],
                "total_epa": summary["total_epa"],
                "epa_per_eligible_dropback": (
                    summary["epa_per_eligible_dropback"]
                ),
                "qualified": not reasons,
                "status": "; ".join(reasons) if reasons else "qualified",
            }
        )

    rows.sort(
        key=lambda row: (
            -row["epa_per_eligible_dropback"]
            if row["epa_per_eligible_dropback"] is not None
            else float("inf"),
            row["gsis_id"],
        )
    )

    previous, rank = None, None

    for index, row in enumerate(
        (row for row in rows if row["qualified"]),
        1,
    ):
        rate = row["epa_per_eligible_dropback"]

        if index == 1 or rate != previous:
            rank = index

        row["rank"] = rank
        previous = rate

    return rows


def verified_profiles(root, path, season):
    profiles, record = lb.load_profiles(root, path, season, "REG")
    state = h.read_json(path)
    integration = state.get("dropback_integration")

    if not integration:
        raise ValueError(
            "Rebuild profiles with --current-dropbacks-manifest first."
        )

    dropbacks_path = h.verify(
        root,
        integration["source_current_dropbacks_manifest"],
    )

    metrics = h.read_json(
        h.verify(root, state["source_position_metrics_manifest"])
    )

    frame, lineage = pd.load(
        root,
        dropbacks_path,
        metrics,
        season,
        "REG",
    )

    if any(
        integration.get(key) != value
        for key, value in lineage.items()
    ):
        raise ValueError(
            "Saved dropback lineage disagrees with source files."
        )

    rebuilt = deepcopy(profiles)
    audit = pd.attach(rebuilt, frame, season)

    if audit != integration["audit"]:
        raise ValueError(
            "Saved dropback audit disagrees with source rows."
        )

    for actual, expected in zip(profiles, rebuilt, strict=True):
        if actual.get("dropbacks") != expected["dropbacks"] or any(
            left.get("dropbacks") != right["dropbacks"]
            for left, right in zip(
                actual["games"],
                expected["games"],
                strict=True,
            )
        ):
            raise ValueError(
                "Profile dropbacks disagree with the source table."
            )

    return profiles, state, record


def render(rows, links):
    columns = [
        ("Rank", "rank"),
        ("Player", "player"),
        ("Team(s)", "teams"),
        ("Position(s)", "positions"),
        ("Matched games", "matched_games"),
        ("Profile games", "profile_games"),
        ("Dropbacks", "eligible_dropbacks"),
        ("Eligible EPA", "total_epa"),
        ("EPA/dropback", "epa_per_eligible_dropback"),
        ("Status", "status"),
    ]

    header = "".join(
        f"<th>{title}</th>"
        for title, _ in columns
    )

    body = []

    for row in rows:
        cells = []

        for _, field in columns:
            value = (
                "—"
                if field == "rank" and row[field] is None
                else pp.display(row[field])
            )

            value = html.escape(value)

            if field == "player":
                href = html.escape(
                    links[row["gsis_id"]],
                    quote=True,
                )
                value = f'<a href="{href}">{value}</a>'

            cells.append(f"<td>{value}</td>")

        body.append("<tr>" + "".join(cells) + "</tr>")

    return (
        '<div class="scroll"><table><thead><tr>'
        + header
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def build(root, path, season, min_dropbacks=100, min_games=3):
    root = Path(root).resolve()
    path = h.locate(root, path)

    profiles, source, record = verified_profiles(root, path, season)
    rows = compare(profiles, min_dropbacks, min_games)

    observed = [
        row for row in rows
        if row["matched_games"] > 0
    ]
    qualified = [
        row for row in rows
        if row["qualified"]
    ]

    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"dropback_leaderboard_{run}"

    links, pages = {}, {}

    for profile in profiles:
        if not profile["dropbacks"]["matched_games"]:
            continue

        page_record = source["files"][lb.player_filename(profile)]
        page_path = h.verify(root, page_record)

        links[profile["gsis_id"]] = Path(
            os.path.relpath(page_path, output)
        ).as_posix()

        pages[profile["gsis_id"]] = page_record

    body = f"<h1>Eligible-dropback leaderboard · {season} REG</h1>"

    body += (
        f"<p>Display thresholds: {min_dropbacks} dropbacks and "
        f"{min_games} matched games, with matches for every "
        "observed profile game.</p>"
    )

    body += (
        "<ul>"
        + "".join(
            f"<li>{html.escape(note)}</li>"
            for note in NOTES
        )
        + "</ul>"
    )

    body += "<h2>Qualified players</h2>"
    body += (
        render(qualified, links)
        if qualified
        else "<p>No players meet these display thresholds.</p>"
    )

    body += "<h2>All players with matched dropbacks</h2>"
    body += (
        render(observed, links)
        if observed
        else "<p>No matched dropback records.</p>"
    )

    body += (
        f"<p>{len(rows) - len(observed)} profiles have no matched "
        "dropback records. These are retained in coverage.csv "
        "without assigning zero production.</p>"
    )

    output.mkdir(parents=True, exist_ok=False)

    for name, data in (
        ("qualified", qualified),
        ("observed", observed),
        ("coverage", rows),
    ):
        pl.DataFrame(data, schema=SCHEMA).write_csv(
            output / f"{name}.csv"
        )

    (output / "index.html").write_text(
        pp.page("Dropback leaderboard", body),
        encoding="utf-8",
    )

    h.save_json(
        output / "dropback_leaderboard_manifest.json",
        {
            "schema_version": 1,
            "run_id": run,
            "season": season,
            "season_type": "REG",
            "source_profile_manifest": h.record(root, path),
            "consumed_profiles": record,
            "dropback_integration": source["dropback_integration"],
            "linked_profile_pages": pages,
            "display_thresholds": {
                "min_dropbacks": min_dropbacks,
                "min_games": min_games,
            },
            "profile_count": len(rows),
            "observed_count": len(observed),
            "qualified_count": len(qualified),
            "limitations": NOTES,
            "files": {
                file.name: h.record(root, file)
                for file in sorted(output.iterdir())
            },
            "code_sha256": {
                name: h.sha256(Path(__file__).with_name(name))
                for name in (
                    "dropback_leaderboard.py",
                    "profile_dropbacks.py",
                    "player_profile.py",
                    "leaderboards.py",
                    "historical.py",
                )
            },
            "packages": {"polars": pl.__version__},
        },
    )

    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--profile-manifest", type=Path)
    sources.add_argument("--latest", action="store_true")

    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--min-dropbacks", type=int, default=100)
    parser.add_argument("--min-games", type=int, default=3)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())

    args = parser.parse_args()
    root = args.project_root.resolve()

    path = (
        lb.latest(root, args.season, "REG")
        if args.latest
        else args.profile_manifest
    )

    output = build(
        root,
        path,
        args.season,
        args.min_dropbacks,
        args.min_games,
    )

    state = h.read_json(
        output / "dropback_leaderboard_manifest.json"
    )

    print(f"Observed participants: {state['observed_count']}")
    print(f"Qualified participants: {state['qualified_count']}")
    print(f"Leaderboard: {output / 'index.html'}")
    print(
        f"Manifest: {output / 'dropback_leaderboard_manifest.json'}"
    )


if __name__ == "__main__":
    main()