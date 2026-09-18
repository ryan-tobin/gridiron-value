import argparse
import csv
import html
import os
from datetime import UTC, datetime
from pathlib import Path

from gridiron_value import historical as h
from gridiron_value import leaderboards as lb
from gridiron_value import player_profile as pp
from gridiron_value import stat_catalog as sc

BOARDS = {
    "passing": ("passing_yards", "passing_tds", "completions"),
    "rushing": ("rushing_yards", "rushing_tds", "rushing_first_downs", "carries"),
    "receiving": ("receiving_yards", "receiving_tds", "receptions", "targets"),
    "defense": (
        "def_sacks",
        "def_interceptions",
        "def_pass_defended",
        "def_tackles_solo",
        "def_tackles_for_loss",
        "def_fumbles_forced",
    ),
    "kicking": ("fg_made", "pat_made"),
    "punting": ("pt_att", "pt_yards", "pt_net_yards", "pt_inside_20"),
    "returns": ("punt_return_yards", "kickoff_return_yards", "special_teams_tds"),
    "fumble_recoveries": ("fumble_recovery_own", "fumble_recovery_opp"),
}

FIELDS = (
    "group",
    "metric",
    "gsis_id",
    "name",
    "positions",
    "teams",
    "rank",
    "observed_total",
    "available_games",
    "profile_games",
    "status",
)

NOTES = [
    "Totals describe observed profile games, not certified full-season coverage.",
    "A category includes relevant positions and players with recorded activity in that category.",
    "Ranks require an available value in every observed profile game. Partial totals are listed separately without a rank.",
    "No minimum attempts, snaps, or games is imposed on counting totals. Zero and negative recorded totals remain valid values.",
    "Ties use unrounded totals and competition ranks: 1, 1, 3. Alphabetical ordering within ties does not break the tie.",
    "Higher means a larger recorded total, not necessarily better performance. Punt volume, for example, is not a skill rating.",
]


def compare(profiles):
    rows = []

    for profile in profiles:
        totals, _ = pp.summarize(profile["games"])

        if totals != profile["totals"]:
            raise ValueError("Profile totals disagree with the underlying games")

        for group in pp.profile_groups(profile):
            for metric in BOARDS.get(group, ()):
                item = totals[metric]

                status = (
                    "unavailable"
                    if item["observed_games"] == 0
                    else "partial"
                    if item["observed_games"] < item["total_games"]
                    else "ranked"
                )

                rows.append(
                    {
                        "group": group,
                        "metric": metric,
                        "gsis_id": profile["gsis_id"],
                        "name": profile["name"],
                        "positions": " | ".join(profile["positions"]),
                        "teams": " | ".join(profile["teams"]),
                        "rank": None,
                        "observed_total": item["observed_sum"],
                        "available_games": item["observed_games"],
                        "profile_games": item["total_games"],
                        "status": status,
                    }
                )

    for metrics in BOARDS.values():
        for metric in metrics:
            ranked = sorted(
                (r for r in rows if r["metric"] == metric and r["status"] == "ranked"),
                key=lambda r: (
                    -r["observed_total"],
                    r["name"].casefold(),
                    r["gsis_id"],
                ),
            )

            previous, rank = None, None

            for place, row in enumerate(ranked, 1):
                if place == 1 or row["observed_total"] != previous:
                    rank = place

                row["rank"] = rank
                previous = row["observed_total"]

    return rows


def href(path, output):
    return html.escape(Path(os.path.relpath(path, output)).as_posix(), quote=True)


def render_table(rows, links, output):
    headers = (
        "Rank",
        "Player",
        "Position",
        "Team(s)",
        "Observed total",
        "Game coverage",
    )

    body = '<div class="scroll"><table><thead><tr>'
    body += "".join(f"<th>{title}</th>" for title in headers)
    body += "</tr></thead><tbody>"

    for row in rows:
        link = href(links[row["gsis_id"]], output)
        player = f'<a href="{link}">{html.escape(row["name"])}</a>'

        cells = (
            "-" if row["rank"] is None else row["rank"],
            row["positions"],
            row["teams"],
            row["observed_total"],
            f"{row['available_games']}/{row['profile_games']}",
        )

        escaped = [html.escape(pp.display(value)) for value in cells]
        escaped.insert(1, player)

        body += "<tr>" + "".join(f"<td>{cell}</td>" for cell in escaped) + "</tr>"

    return body + "</tbody></table></div>"


def build(root, manifest, season, season_type="REG", limit=25):
    if type(limit) is not int or limit < 1:
        raise ValueError("Display limit must be a positive integer.")

    root = Path(root).resolve()
    manifest = h.locate(root, manifest)
    source_record = h.record(root, manifest)

    profiles, profile_record = lb.load_profiles(root, manifest, season, season_type)
    rows = compare(profiles)

    state = h.read_json(manifest)
    links, pages = {}, {}
    names = {p["gsis_id"]: lb.player_filename(p) for p in profiles}

    for name in ("index.html", *names.values()):
        record = state["files"].get(name)

        if record is None:
            raise ValueError(f"Profile manifest lacks linked page: {name}")

        page = h.verify(root, record)

        if page != (manifest.parent / name).resolve():
            raise ValueError("Linked profile page has an unexpected location.")

        pages[name] = record

    for identity, name in names.items():
        links[identity] = manifest.parent / name

    h.verify(root, source_record)

    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"counting_leaderboards_{run}"

    title = f"Counting-stat leaderboards · {season} {season_type}"
    directory = href(manifest.parent / "index.html", output)

    body = f'<a href="{directory}">← Player directory</a><h1>{html.escape(title)}</h1>'

    weeks = sorted({g["week"] for p in profiles for g in p["games"]})
    body += f"<p>Observed weeks: {' , '.join(map(str, weeks))}.</p>"
    body += "<ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in NOTES) + "</ul>"

    body += (
        f"<p>Showing the top {limit} places, including ties. "
        "All candidate rows, including partial and unavailable totals, are in "
        '<a href="counting_leaderboards.csv">the CSV download</a>.</p>'
    )

    body += (
        '<nav aria-label="Stat categories">'
        + " · ".join(
            f'<a href="#{group}">{html.escape(pp.label(group))}</a>' for group in BOARDS
        )
        + "</nav>"
    )

    for group, metrics in BOARDS.items():
        body += f'<h2 id="{group}">{html.escape(pp.label(group))}</h2>'

        for metric in metrics:
            candidates = [r for r in rows if r["metric"] == metric]

            ranked = sorted(
                (r for r in candidates if r["rank"] is not None),
                key=lambda r: (r["rank"], r["name"].casefold(), r["gsis_id"]),
            )

            partial = sorted(
                (r for r in candidates if r["status"] == "partial"),
                key=lambda r: (r["name"].casefold(), r["gsis_id"]),
            )

            body += (
                f"<details><summary>{html.escape(pp.label(metric))}"
                f" · {len(ranked)} ranked</summary>"
            )

            visible = [r for r in ranked if r["rank"] <= limit]

            body += (
                render_table(visible, links, output)
                if visible
                else "<p>No complete observed-game totals available.</p>"
            )

            if partial:
                body += "<h3>Partial totals · unranked</h3>"
                body += (
                    f"<p>Showing {min(limit, len(partial))} of {len(partial)}"
                    "players alphabetically; all are in the CSV.</p>"
                )
                body += render_table(partial[:limit], links, output)

            unavailable = sum(r["status"] == "unavailable" for r in candidates)

            body += (
                f"<p>{unavailable} category participants have no available value."
                "</p></details>"
            )

    output.mkdir(parents=True, exist_ok=False)
    (output / "index.html").write_text(pp.page(title, body), encoding="utf-8")

    with (output / "counting_leaderboards.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    h.save_json(
        output / "counting_leaderboard_manifest.json",
        {
            "run_id": run,
            "season": season,
            "season_type": season_type,
            "source_profile_manifest": source_record,
            "consumed_profiles": profile_record,
            "linked_profile_pages": pages,
            "profile_count": len(profiles),
            "rows": len(rows),
            "observed_weeks": weeks,
            "display_limit": limit,
            "boards": BOARDS,
            "limitations": NOTES,
            "files": {p.name: h.record(root, p) for p in sorted(output.iterdir())},
            "code_sha256": {
                Path(module.__file__).name: h.sha256(Path(module.__file__))
                for module in (pp, lb, sc)
            }
            | {Path(__file__).name: h.sha256(Path(__file__))},
        },
    )

    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--season-type",
        choices=("REG", "POST", "PRE"),
        default="REG",
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    output = build(
        args.project_root,
        args.profile_manifest,
        args.season,
        args.season_type,
        args.limit,
    )

    print(f"Leaderboards: {output / 'index.html'}")
    print(f"Manifest: {output / 'counting_leaderboard_manifest.json'}")


if __name__ == "__main__":
    main()
