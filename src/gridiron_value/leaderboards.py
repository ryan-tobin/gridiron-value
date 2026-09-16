"""Build qualified snapshot leaderboards and offline profiles with peer comparisons."""

import argparse
import csv
import hashlib
import html
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from gridiron_value import historical as h
from gridiron_value import player_profile as pp

DEFAULTS = {
    "attempts": 20,
    "attempts_plus_sacks": 20,
    "carries": 10,
    "targets": 5,
    "receptions": 5,
    "minimum_peers": 5,
}
ROLES = {"QB": "QB", "RB": "RB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}
PASSING = (
    "completion_rate",
    "pass_yards_per_attempt",
    "passing_td_rate",
    "interception_rate",
    "sack_rate_proxy",
)
RUSHING = ("rush_yards_per_carry", "rush_epa_per_carry")
RECEIVING = (
    "catch_rate",
    "receiving_yards_per_target",
    "receiving_epa_per_target",
    "yac_per_reception",
)
ROLE_METRICS = {
    "QB": PASSING + RUSHING,
    "RB": RUSHING + RECEIVING,
    "WR": RECEIVING,
    "TE": RECEIVING,
}
LOWER_BETTER = {"interception_rate", "sack_rate_proxy"}
NOTES = [
    "Comparisons describe qualified players in this snapshot, not a certified league-wide population or a talent rating.",
    "Qualification thresholds are provisional display filters, not established reliability cutoffs. They do not scale with weeks played.",
    "Each metric has its own cohort. Rates require paired numerator and denominator coverage for every observed game; missing values are never zero-filled.",
    "Percentile = 100 × (number of worse peers + half the number of equal peers) / qualifying cohort size, including the player. Higher always means better in the stated direction.",
    "Ties use unrounded values and competition ranks (1, 1, 3). An all-tied cohort receives the 50th percentile. Endpoints need not be 0 or 100.",
    "Percentiles are withheld below the minimum peer count. Descriptive ranks remain available for qualified players.",
    "RB includes RB, HB and FB. WR and TE are separate. Players spanning different role groups, or unsupported positions, receive no peer comparison.",
    "Observed games can differ across players. Opponent, scheme and teammate effects are not adjusted. NGS aggregates, defensive efficiency and current-season Pass+ are not ranked here.",
]
REASONS = {
    "qualified": "Qualified",
    "small_cohort": "Qualified; too few peers for a percentile",
    "incomplete": "Incomplete observed-game coverage",
    "unavailable": "Rate unavailable",
    "below_minimum": "Below workload minimum",
}


def qualification(settings=None):
    result = dict(DEFAULTS)
    if settings is not None:
        if not isinstance(settings, dict) or set(settings) - set(DEFAULTS):
            raise ValueError(
                "Qualification config contains unknown keys or is not an object."
            )
        result.update(settings)
    for key, value in result.items():
        if type(value) is not int or value < (2 if key == "minimum_peers" else 1):
            raise ValueError(
                f"{key} must be a positive integer (minimum_peers must be at least 2)."
            )
    return result


def role(profile):
    positions = profile["positions"]
    groups = {ROLES.get(p.upper()) for p in positions}
    return next(iter(groups)) if len(groups) == 1 and None not in groups else None


def opportunity(metric):
    return (
        "attempts_plus_sacks" if metric == "sack_rate_proxy" else pp.RATES[metric][1][0]
    )


def finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def compare(profiles, settings=None):
    """Return one auditable row per supported player/metric, including exclusions."""
    settings = qualification(settings)
    rows = []
    for profile in profiles:
        group = role(profile)
        for metric in ROLE_METRICS.get(group, ()):
            item = profile["rates"][metric]
            value, denominator = item.get("value"), item.get("denominator")
            minimum = settings[opportunity(metric)]
            complete = (
                item["observed_games"]
                == item["total_games"]
                == profile["games_observed"]
            )
            status = (
                "incomplete"
                if not complete
                else "below_minimum"
                if finite(denominator) and denominator < minimum
                else "unavailable"
                if not finite(value) or not finite(denominator)
                else "qualified"
            )
            rows.append(
                {
                    "gsis_id": profile["gsis_id"],
                    "name": profile["name"],
                    "teams": "|".join(profile["teams"]),
                    "role": group,
                    "season": profile["season"],
                    "season_type": profile["season_type"],
                    "metric": metric,
                    "value": value if finite(value) else None,
                    "denominator": denominator if finite(denominator) else None,
                    "opportunity": opportunity(metric),
                    "minimum": minimum,
                    "games_observed": profile["games_observed"],
                    "games_with_metric": item["observed_games"],
                    "direction": "lower" if metric in LOWER_BETTER else "higher",
                    "status": status,
                    "rank": None,
                    "percentile": None,
                    "peer_count": 0,
                }
            )
    for group, metrics in ROLE_METRICS.items():
        for metric in metrics:
            cohort = [r for r in rows if r["role"] == group and r["metric"] == metric]
            qualified = [r for r in cohort if r["status"] == "qualified"]
            n = len(qualified)
            for row in cohort:
                row["peer_count"] = n
            direction = -1 if metric in LOWER_BETTER else 1
            for row in qualified:
                value = direction * row["value"]
                worse = sum(direction * r["value"] < value for r in qualified)
                better = sum(direction * r["value"] > value for r in qualified)
                equal = n - worse - better
                row["rank"] = 1 + better
                if n >= settings["minimum_peers"]:
                    row["percentile"] = 100 * (worse + equal / 2) / n
                else:
                    row["status"] = "small_cohort"
    return rows


def latest(root, season, season_type):
    """Select the newest full matching profile run; do not fall back on checksum failure."""
    candidates = []
    for path in (root / "reports" / "tables").glob(
        "player_profiles_*/profile_manifest.json"
    ):
        state = h.read_json(path)
        if (
            state.get("season") == season
            and state.get("season_type") == season_type
            and "gsis_id_filter" in state
            and state["gsis_id_filter"] is None
        ):
            candidates.append((state["run_id"], path))
    if not candidates:
        raise ValueError(
            "No full player-profile run matches this season and season type. Generate profiles first."
        )
    return max(candidates)[1]


def load_profiles(root, manifest, season, season_type):
    state = h.read_json(manifest)
    if state.get("season") != season or state.get("season_type") != season_type:
        raise ValueError("Profile manifest season/type does not match request.")
    if "gsis_id_filter" not in state or state["gsis_id_filter"] is not None:
        raise ValueError(
            "Leaderboards require a full profile run without a GSIS filter."
        )
    record = state["files"]["profiles.json"]
    payload = h.read_json(h.verify(root, record))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported profile schema version.")
    profiles = payload["profiles"]
    ids = [p["gsis_id"] for p in profiles]
    if (
        not profiles
        or len(profiles) != state.get("profile_count")
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Profile count or unique player IDs are invalid.")
    for profile in profiles:
        if profile["season"] != season or profile["season_type"] != season_type:
            raise ValueError("Profile season/type does not match manifest.")
        games = profile["games"]
        if not games or profile["games_observed"] != len(games):
            raise ValueError("Observed game count does not match profile games.")
        keys = []
        for game in games:
            if (
                game["gsis_id"] != profile["gsis_id"]
                or game["season"] != season
                or game["season_type"] != season_type
                or game["week"] < 1
            ):
                raise ValueError(
                    "Profile contains games from a different player or season/type."
                )
            keys.append((game["game_id"], game["team"]))
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate player-game-team keys in profile.")
        # Verify rates against paired game observations, not just a cached rate field.
        if pp.summarize(games)[1] != profile["rates"]:
            raise ValueError(
                "Profile rates disagree with the underlying game observations."
            )
    return profiles, record


def player_filename(profile):
    return "player_" + hashlib.sha256(profile["gsis_id"].encode()).hexdigest() + ".html"


def peer_section(profile, rows, settings):
    body = '<h2>Peer comparisons</h2><p><a href="leaderboards.html">View position leaderboards</a></p>'
    if not rows:
        return (
            body
            + "<p>No comparison group is available for these observed positions.</p>"
        )
    body += (
        f"<p>{rows[0]['role']} snapshot peers; at least {settings['minimum_peers']} qualifying players "
        "needed for a percentile. Higher percentiles indicate better observed results in the stated direction.</p>"
    )
    for row in rows:
        metric = html.escape(pp.label(row["metric"]))
        description = html.escape(REASONS[row["status"]])
        body += f"<div><strong>{metric}</strong> · {row['direction']} is better<br>"
        body += html.escape(
            f"Value: {pp.display(pp.rate_display(row['metric'], row['value']))}; "
            f"opportunities: {pp.display(row['denominator'])} / minimum {row['minimum']} "
            f"({row['opportunity'].replace('_', ' ')}); qualified peers: {row['peer_count']}. "
        )
        if row["percentile"] is not None:
            percentile = row["percentile"]
            body += (
                f'<br><meter min="0" max="100" value="{percentile}" '
                f'aria-label="{metric} peer percentile" style="width:220px;max-width:100%">'
                f"{percentile:.1f}</meter> {percentile:.1f} percentile · "
            )
        if row["rank"] is not None:
            body += f"Rank {row['rank']} of {row['peer_count']} · "
        body += f"{description}</div><br>"
    return body


def build(root, manifest, season, season_type="REG", settings=None, config_record=None):
    root = Path(root).resolve()
    manifest = h.locate(root, manifest)
    settings = qualification(settings)
    profiles, source_file = load_profiles(root, manifest, season, season_type)
    rows = compare(profiles, settings)
    weeks = sorted({g["week"] for p in profiles for g in p["games"]})
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"leaderboards_{run}"
    output.mkdir(parents=True, exist_ok=False)
    notice = (
        "Early-season snapshot. " if season_type == "REG" and max(weeks) <= 4 else ""
    )
    notice += f"Observed weeks {min(weeks)}–{max(weeks)}; coverage may differ by player. Descriptive results, not talent ratings."
    links = []
    for profile in sorted(profiles, key=lambda p: (p["name"].casefold(), p["gsis_id"])):
        filename = player_filename(profile)
        peers = [r for r in rows if r["gsis_id"] == profile["gsis_id"]]
        peer_html = f'<p class="meta">{html.escape(notice)}</p>' + peer_section(
            profile, peers, settings
        )
        peer_html += "<details><summary>Peer comparison methodology</summary><ul>"
        peer_html += (
            "".join(f"<li>{html.escape(n)}</li>" for n in NOTES) + "</ul></details>"
        )
        (output / filename).write_text(
            pp.render_profile(profile, peer_html), encoding="utf-8"
        )
        title = f"{profile['name']} · {profile['gsis_id']} · {', '.join(profile['teams'])} · {', '.join(profile['positions'])}"
        links.append(f'<li><a href="{filename}">{html.escape(title)}</a></li>')
    index = f"<h1>Player directory · {season} {season_type}</h1><p>{html.escape(notice)}</p>"
    index += '<p><a href="leaderboards.html">Position leaderboards</a></p>'
    index += '<label for="query">Find a player, ID, team or position</label><br><input type="search" id="query"><ul id="players">'
    index += (
        "".join(links)
        + '</ul><script>document.getElementById("query").addEventListener("input",function(){const q=this.value.toLowerCase();document.querySelectorAll("#players li").forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script>'
    )
    (output / "index.html").write_text(
        pp.page("Player directory", index), encoding="utf-8"
    )
    board = '<a href="index.html">← Player directory</a>'
    board += f"<h1>Position leaderboards · {season} {season_type}</h1><p>{html.escape(notice)}</p>"
    board += (
        '<nav aria-label="Position groups">'
        + " · ".join(f'<a href="#{r}">{r}</a>' for r in ROLE_METRICS)
        + "</nav>"
    )
    cohort_counts = []
    for group, metrics in ROLE_METRICS.items():
        board += f'<h2 id="{group}">{group}</h2>'
        for metric in metrics:
            relevant = [r for r in rows if r["role"] == group and r["metric"] == metric]
            qualified = sorted(
                (r for r in relevant if r["rank"] is not None),
                key=lambda r: (r["rank"], r["name"], r["gsis_id"]),
            )
            counts = dict(Counter(r["status"] for r in relevant))
            cohort_counts.append(
                {
                    "role": group,
                    "metric": metric,
                    "qualified": len(qualified),
                    "status_counts": counts,
                }
            )
            direction = "lower" if metric in LOWER_BETTER else "higher"
            board += f"<h3>{html.escape(pp.label(metric))}</h3><p>{direction.capitalize()} is better. Minimum {settings[opportunity(metric)]} {opportunity(metric).replace('_', ' ')}; {len(qualified)} qualified of {len(relevant)} observed {group} players. Percentiles require {settings['minimum_peers']} peers.</p>"
            if not qualified:
                board += "<p>No players qualify for this metric in this snapshot.</p>"
                continue
            board += (
                '<div class="scroll"><table><thead><tr>'
                + "".join(
                    f"<th>{v}</th>"
                    for v in (
                        "Rank",
                        "Player",
                        "Team",
                        "Value",
                        "Opportunities",
                        "Games",
                        "Peer percentile",
                    )
                )
                + "</tr></thead><tbody>"
            )
            for row in qualified:
                filename = player_filename(row)
                cells = (
                    row["teams"],
                    pp.rate_display(metric, row["value"]),
                    row["denominator"],
                    row["games_observed"],
                    f"{row['percentile']:.1f}"
                    if row["percentile"] is not None
                    else "Withheld: too few peers",
                )
                board += f'<tr><td>{row["rank"]}</td><td><a href="{filename}">{html.escape(row["name"])}</a></td>'
                board += (
                    "".join(f"<td>{html.escape(pp.display(v))}</td>" for v in cells)
                    + "</tr>"
                )
            board += "</tbody></table></div>"
    board += (
        "<h2>Methodology</h2><ul>"
        + "".join(f"<li>{html.escape(n)}</li>" for n in NOTES)
        + "</ul>"
    )
    (output / "leaderboards.html").write_text(
        pp.page("Position leaderboards", board), encoding="utf-8"
    )
    fields = (
        list(rows[0])
        if rows
        else ["gsis_id", "role", "metric", "status", "rank", "percentile"]
    )
    with (output / "comparisons.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    unsupported = [
        {
            "gsis_id": p["gsis_id"],
            "positions": p["positions"],
            "reason": "Unsupported or mixed comparison roles",
        }
        for p in profiles
        if role(p) is None
    ]
    h.save_json(
        output / "comparisons.json",
        {
            "schema_version": 1,
            "qualification": settings,
            "comparisons": rows,
            "cohorts": cohort_counts,
            "uncompared_profiles": unsupported,
            "limitations": NOTES,
        },
    )
    files = {p.name: h.record(root, p) for p in sorted(output.iterdir())}
    h.save_json(
        output / "leaderboard_manifest.json",
        {
            "run_id": run,
            "season": season,
            "season_type": season_type,
            "observed_weeks": weeks,
            "profile_count": len(profiles),
            "comparison_rows": len(rows),
            "uncompared_profile_count": len(unsupported),
            "qualification": settings,
            "qualification_config": config_record,
            "source_profile_manifest": h.record(root, manifest),
            "consumed_profiles": source_file,
            "cohorts": cohort_counts,
            "limitations": NOTES,
            "files": files,
            "code_sha256": h.sha256(Path(__file__)),
            "profile_code_sha256": h.sha256(Path(pp.__file__)),
        },
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--profile-manifest", type=Path)
    sources.add_argument(
        "--latest",
        action="store_true",
        help="Use the newest full profile run matching season and season type.",
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--season-type", choices=("REG", "POST", "PRE"), default="REG")
    parser.add_argument(
        "--qualification-config",
        type=Path,
        help="JSON object overriding provisional display thresholds.",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest = (
        latest(root, args.season, args.season_type)
        if args.latest
        else h.locate(root, args.profile_manifest)
    )
    settings, config_record = None, None
    if args.qualification_config:
        path = h.locate(root, args.qualification_config)
        settings, config_record = h.read_json(path), h.record(root, path)
    print(f"Source profiles: {manifest}")
    output = build(
        root, manifest, args.season, args.season_type, settings, config_record
    )
    print(f"Leaderboards: {output / 'leaderboards.html'}")
    print(f"Player directory: {output / 'index.html'}")
    print(f"Manifest: {output / 'leaderboard_manifest.json'}")


if __name__ == "__main__":
    main()
