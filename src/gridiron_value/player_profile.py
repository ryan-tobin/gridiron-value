"""Build offline player profiles from verified metrics and participation snapshots."""

import argparse
import hashlib
import html
import math
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import polars as pl

from gridiron_value import historical as h

KEYS = ("season", "game_id", "team", "gsis_id")
SNAPS = ("offense_snaps", "defense_snaps", "st_snaps")
GROUPS = {
    "passing": (
        "attempts",
        "completions",
        "passing_yards",
        "passing_tds",
        "passing_interceptions",
        "sacks_suffered",
        "passing_epa",
    ),
    "rushing": ("carries", "rushing_yards", "rushing_tds", "rushing_epa"),
    "receiving": (
        "targets",
        "receptions",
        "receiving_yards",
        "receiving_tds",
        "receiving_yards_after_catch",
        "receiving_epa",
    ),
    "defense": (
        "def_tackles_solo",
        "def_tackle_assists",
        "def_sacks",
        "def_qb_hits",
        "def_interceptions",
        "def_pass_defended",
    ),
    "kicking": ("fg_att", "fg_made", "pat_att", "pat_made"),
    "punting": ("pt_att", "pt_yards", "pt_net_yards"),
    "returns": (
        "punt_returns",
        "punt_return_yards",
        "kickoff_returns",
        "kickoff_return_yards",
    ),
}
COUNTS = tuple(c for group in GROUPS.values() for c in group)
DEFENSIVE_POSITIONS = {
    "DE",
    "DT",
    "NT",
    "DL",
    "EDGE",
    "ED",
    "LB",
    "ILB",
    "OLB",
    "MLB",
    "CB",
    "DB",
    "S",
    "SAF",
    "FS",
    "SS",
}
POSITION_GROUPS = {
    "QB": {"passing", "rushing"},
    "RB": {"rushing", "receiving"},
    "HB": {"rushing", "receiving"},
    "FB": {"rushing", "receiving"},
    "WR": {"receiving"},
    "TE": {"receiving"},
    "K": {"kicking"},
    "PK": {"kicking"},
    "P": {"punting"},
    "KR": {"returns"},
    "PR": {"returns"},
    **{position: {"defense"} for position in DEFENSIVE_POSITIONS},
}
LABELS = {
    "offense_snaps": "Offensive snaps",
    "defense_snaps": "Defensive snaps",
    "st_snaps": "Special-teams snaps",
    "attempts": "Pass attempts",
    "passing_tds": "Passing TDs",
    "rushing_tds": "Rushing TDs",
    "receiving_tds": "Receiving TDs",
    "passing_td_rate": "Passing TD rate",
    "def_tackles_solo": "Solo tackles",
    "def_tackle_assists": "Tackle assists",
    "def_sacks": "Sacks",
    "def_qb_hits": "QB hits",
    "def_interceptions": "Interceptions",
    "def_pass_defended": "Passes defended",
    "fg_att": "Field-goal attempts",
    "fg_made": "Field goals made",
    "pat_att": "Extra-point attempts",
    "pat_made": "Extra points made",
    "pt_att": "Punts",
    "pt_yards": "Punt yards",
    "pt_net_yards": "Net punt yards",
}
# Each rate is a ratio of additive source fields; never average game rates.
RATES = {
    "completion_rate": ("completions", ("attempts",)),
    "pass_yards_per_attempt": ("passing_yards", ("attempts",)),
    "passing_td_rate": ("passing_tds", ("attempts",)),
    "interception_rate": ("passing_interceptions", ("attempts",)),
    "sack_rate_proxy": ("sacks_suffered", ("attempts", "sacks_suffered")),
    "rush_yards_per_carry": ("rushing_yards", ("carries",)),
    "rush_epa_per_carry": ("rushing_epa", ("carries",)),
    "catch_rate": ("receptions", ("targets",)),
    "receiving_yards_per_target": ("receiving_yards", ("targets",)),
    "receiving_epa_per_target": ("receiving_epa", ("targets",)),
    "yac_per_reception": ("receiving_yards_after_catch", ("receptions",)),
}
NOTES = [
    "Summaries cover observed player-game rows in these snapshots, not certified full-season coverage.",
    "Partial sums and rates are labeled observed only; missing production and snaps are never zero-filled.",
    "NGS and share fields are shown per game only; their season aggregation weights are not established here.",
    "Passing EPA is shown as supplied. EPA per pass attempt is omitted pending a numerator/denominator audit.",
    "Sack-rate proxy excludes scrambles. This profile does not calculate current-season Pass+ or peer percentiles.",
    "EPA retains teammate and scheme contributions. Snaps do not establish blocking or coverage quality.",
    "Unresolved snap identities and anonymous statistics cannot be assigned to a GSIS profile.",
]


def number(value):
    """Keep finite source numbers; missing and nonfinite observations stay null."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def keyed(frame, identity):
    required = {"season", "game_id", "team", identity, "week"}
    if required - set(frame.columns):
        raise ValueError(
            f"Missing profile keys: {sorted(required - set(frame.columns))}"
        )
    result = {}
    for original in frame.to_dicts():
        row = dict(original, gsis_id=original[identity])
        for name in ("game_id", "team", "gsis_id"):
            if row[name] is None or not str(row[name]).strip():
                raise ValueError(f"Missing profile key: {name}")
            row[name] = str(row[name]).strip()
        if row["season"] is None or row["week"] is None or row["week"] < 1:
            raise ValueError("Profiles require a season and positive game week.")
        row["season_type"] = row.get("season_type") or row.get("game_type")
        if row["season_type"] not in ("REG", "POST", "PRE"):
            raise ValueError("Missing or unsupported season type.")
        key = tuple(row[c] for c in KEYS)
        if key in result:
            raise ValueError(f"Duplicate player-game key: {key}")
        result[key] = row
    return result


def combine_games(metrics, participation):
    """Full union preserves production-only and snap-only player-game records."""
    production = keyed(metrics, "gsis_id")
    snaps = keyed(participation, "resolved_gsis_id")
    games = []
    for key in sorted(production.keys() | snaps.keys()):
        stat, snap = production.get(key), snaps.get(key)
        if snap and snap.get("identity_status") not in (
            "resolved",
            "resolved_override",
        ):
            raise ValueError("Participation includes unresolved identities.")
        if snap and bool(snap.get("has_player_stats")) != (stat is not None):
            raise ValueError(
                "Participation production flag disagrees with metric rows."
            )
        if stat and snap:
            for field in ("week", "season_type"):
                if stat[field] != snap[field]:
                    raise ValueError(f"Conflicting {field} for {key}")
        base = stat or snap
        row = {c: base[c] for c in (*KEYS, "week", "season_type")}
        row.update(
            name=base.get("player_display_name")
            or base.get("player")
            or base.get("player_name")
            or base["gsis_id"],
            position=base.get("position"),
            opponent=base.get("opponent_team") or base.get("opponent"),
            has_production=stat is not None,
            has_participation=snap is not None,
            identity_status=snap.get("identity_status") if snap else None,
        )
        row.update({c: number(stat.get(c)) if stat else None for c in COUNTS})
        row.update({c: number(snap.get(c)) if snap else None for c in SNAPS})
        # Keep rate/share/context fields at game grain, without reinterpreting weights.
        row["context"] = {
            c: number(value)
            for c, value in (stat or {}).items()
            if c.startswith("ngs_") or c in ("target_share", "air_yards_share", "wopr")
        }
        games.append(row)
    return games


def summarize(games):
    totals, rates = {}, {}
    for field in (*SNAPS, *COUNTS):
        observed = [g[field] for g in games if g[field] is not None]
        total = sum(observed) if observed else None
        totals[field] = {
            "value": total if len(observed) == len(games) else None,
            "observed_sum": total,
            "observed_games": len(observed),
            "total_games": len(games),
        }
    for name, (numerator, denominators) in RATES.items():
        paired = [
            g
            for g in games
            if all(g[c] is not None for c in (numerator, *denominators))
        ]
        den = sum(sum(g[c] for c in denominators) for g in paired)
        num = sum(g[numerator] for g in paired)
        value = num / den if den > 0 else None
        rates[name] = {
            "value": value if len(paired) == len(games) else None,
            "observed_value": value,
            "numerator": num if paired else None,
            "denominator": den if paired else None,
            "observed_games": len(paired),
            "total_games": len(games),
            "definition": f"sum({numerator}) / sum({' + '.join(denominators)})",
        }
    return totals, rates


def profiles_from_games(games, season, season_type="REG", gsis_id=None):
    groups = {}
    for game in games:
        if (
            game["season"] == season
            and game["season_type"] == season_type
            and (gsis_id is None or game["gsis_id"] == gsis_id)
        ):
            groups.setdefault(game["gsis_id"], []).append(game)
    if not groups:
        raise ValueError("No player-game rows match the requested season/type/ID.")
    profiles = []
    for identity, rows in sorted(groups.items()):
        rows.sort(key=lambda r: (r["week"], r["game_id"], r["team"]))
        totals, rates = summarize(rows)
        profiles.append(
            {
                "gsis_id": identity,
                "name": rows[-1]["name"],
                "season": season,
                "season_type": season_type,
                "teams": sorted({r["team"] for r in rows}),
                "positions": sorted({r["position"] for r in rows if r["position"]}),
                "games_observed": len(rows),
                "production_games": sum(r["has_production"] for r in rows),
                "participation_games": sum(r["has_participation"] for r in rows),
                "totals": totals,
                "rates": rates,
                "games": rows,
            }
        )
    return profiles


def display(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, float):
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    return str(value)


def label(field):
    if field in LABELS:
        return LABELS[field]
    return (
        field.replace("_", " ")
        .capitalize()
        .replace("epa", "EPA")
        .replace("ngs", "NGS")
        .replace("yac", "YAC")
    )


def rate_display(name, value):
    if value is None:
        return None
    if name.endswith("_rate") or name == "sack_rate_proxy":
        return f"{100 * value:.1f}%"
    return display(value)


def table(headers, rows):
    def cell(value):
        return html.escape(display(value))

    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{cell(v)}</th>" for v in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{cell(v)}</td>" for v in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def page(title, body):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} | Gridiron Value</title>
<style>
:root{{color-scheme:light dark}}body{{margin:0;background:light-dark(#f5f7fa,#111923);
color:light-dark(#182433,#e5ecf4);font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1120px;margin:auto;padding:32px 20px}}a{{color:light-dark(#175aa3,#82baff)}}
h1{{margin-bottom:8px}}h2{{margin-top:32px}}table{{border-collapse:collapse;width:100%}}
th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid light-dark(#d6dde6,#344253)}}
td{{font-variant-numeric:tabular-nums}}.scroll{{overflow-x:auto}}input{{font:inherit;
padding:10px;max-width:100%;box-sizing:border-box}}details{{margin:12px 0}}
summary{{cursor:pointer}}.meta{{color:light-dark(#526174,#b0bdcd)}}
</style></head><body><main>{body}</main></body></html>"""


def profile_groups(profile):
    """Show position-relevant groups plus any group with actual game activity.

    Check games, not sums: positive and negative outcomes can cancel out.
    This selects presentation fields only; it never changes stored metrics.
    """
    selected = set()
    for position in profile["positions"]:
        selected.update(POSITION_GROUPS.get(position.upper(), set()))
    for group, fields in GROUPS.items():
        if any(
            game.get(field) not in (None, 0)
            for game in profile["games"]
            for field in fields
        ):
            selected.add(group)
    return [group for group in GROUPS if group in selected]


def game_count(count):
    return f"{count} {'game' if count == 1 else 'games'}"


def coverage_status(item):
    if not item["observed_games"]:
        return "Unavailable"
    return "Complete" if item["observed_games"] == item["total_games"] else "Partial"


def production_table(profile, fields):
    return table(
        ["Field", "Observed sum", "Game coverage", "Status"],
        [
            (
                label(name),
                profile["totals"][name]["observed_sum"],
                f"{profile['totals'][name]['observed_games']}/{profile['games_observed']}",
                coverage_status(profile["totals"][name]),
            )
            for name in fields
        ],
    )


def render_profile(profile):
    groups = profile_groups(profile)
    visible_fields = {field for group in groups for field in GROUPS[group]}
    relevant_rates = {
        name: item
        for name, item in profile["rates"].items()
        if RATES[name][0] in visible_fields
    }
    title = f"{profile['name']} · {profile['season']} {profile['season_type']}"
    body = '<a href="index.html">← Player directory</a>'
    body += f"<h1>{html.escape(title)}</h1>"
    body += (
        '<p class="meta">'
        + html.escape(
            f"{profile['gsis_id']} | {', '.join(profile['teams'])} | "
            f"{', '.join(profile['positions'])} | {game_count(profile['games_observed'])} observed"
        )
        + "</p>"
    )
    body += (
        f"<p>Production available: {game_count(profile['production_games'])}. "
        f"Participation available: {game_count(profile['participation_games'])}.</p>"
    )
    body += (
        "<p>Values summarize observed games only. Game coverage shows games with "
        "an available value / observed games. Missing values are shown as unavailable.</p>"
    )
    body += "<h2>Workload</h2>" + production_table(profile, SNAPS)
    for group in groups:
        body += f"<h2>{label(group)}</h2>" + production_table(profile, GROUPS[group])
    if not groups:
        body += "<p>No position-specific production group is available for this profile.</p>"
    body += "<h2>Efficiency</h2>"
    if relevant_rates:
        body += table(
            ["Metric", "Observed rate", "Denominator", "Game coverage", "Status"],
            [
                (
                    label(name),
                    rate_display(name, item["observed_value"]),
                    item["denominator"],
                    f"{item['observed_games']}/{item['total_games']}",
                    "No recorded opportunities"
                    if item["denominator"] == 0 and coverage_status(item) == "Complete"
                    else coverage_status(item),
                )
                for name, item in relevant_rates.items()
            ],
        )
    else:
        body += (
            "<p>Defensive efficiency metrics are not implemented yet. Defensive snap "
            "counts alone do not identify pass-rush or coverage opportunities.</p>"
            if "defense" in groups
            else "<p>No efficiency metrics are implemented for the roles shown in this profile.</p>"
        )
    body += "<h2>Game log</h2>" + table(
        [
            "Week",
            "Game",
            "Team",
            "Opponent",
            "Production",
            "Offense snaps",
            "Defense snaps",
            "ST snaps",
        ],
        [
            (
                g["week"],
                g["game_id"],
                g["team"],
                g["opponent"],
                "Available" if g["has_production"] else "Unavailable",
                *(g[c] for c in SNAPS),
            )
            for g in profile["games"]
        ],
    )
    for game in profile["games"]:
        body += (
            "<details><summary>"
            + html.escape(
                f"Week {game['week']} · {game['game_id']} · statistics and context"
            )
            + "</summary>"
        )
        body += (
            table(
                ["Field", "Value"],
                [(label(c), game[c]) for c in COUNTS if game[c] is not None]
                + [(label(c), value) for c, value in game["context"].items()],
            )
            + "</details>"
        )
    if relevant_rates:
        body += (
            "<details><summary>Rate definitions</summary>"
            + table(
                ["Metric", "Calculation"],
                [
                    (label(name), item["definition"])
                    for name, item in relevant_rates.items()
                ],
            )
            + "</details>"
        )
    ngs_fields = {
        field
        for game in profile["games"]
        for field, value in game["context"].items()
        if field.startswith("ngs_") and value is not None
    }
    if ngs_fields:
        body += "<details><summary>Next Gen Stats units</summary>"
        if any(field.startswith("ngs_passing_") for field in ngs_fields):
            body += "<p>Completion percentages use percent units; CPOE uses percentage points. Time to throw uses seconds.</p>"
        if any(
            field.startswith(("ngs_rushing_", "ngs_receiving_")) for field in ngs_fields
        ):
            body += "<p>Rushing and receiving distances use yards.</p>"
        body += "</details>"
    body += (
        "<details><summary>Coverage and methodology</summary><ul>"
        + "".join(f"<li>{html.escape(note)}</li>" for note in NOTES)
        + "</ul></details>"
    )
    return page(title, body)


def build(
    root, metrics_path, participation_path, season, season_type="REG", gsis_id=None
):
    root = Path(root).resolve()
    metrics_path, participation_path = (
        h.locate(root, p) for p in (metrics_path, participation_path)
    )
    metrics, participation = h.read_json(metrics_path), h.read_json(participation_path)
    if metrics["source_participation_manifest"] != h.record(root, participation_path):
        raise ValueError("Metrics refer to a different participation manifest.")
    if (
        metrics["source_player_game_manifest"]
        != participation["source_player_game_manifest"]
    ):
        raise ValueError(
            "Metrics and participation refer to different player-game inputs."
        )
    if metrics["season"] != season or participation["season"] != season:
        raise ValueError("Requested season does not match input manifests.")
    consumed = {
        "metrics": metrics["files"]["all"],
        "participation": participation["files"]["participation"],
    }
    frames = {k: pl.read_parquet(h.verify(root, rec)) for k, rec in consumed.items()}
    for frame in frames.values():
        if frame["season"].null_count() or set(frame["season"].to_list()) - {season}:
            raise ValueError("Table season does not match manifest.")
    profiles = profiles_from_games(
        combine_games(**frames), season, season_type, gsis_id
    )
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"player_profiles_{run}"
    output.mkdir(parents=True, exist_ok=False)
    links = []
    for profile in sorted(profiles, key=lambda p: (p["name"].casefold(), p["gsis_id"])):
        filename = (
            "player_"
            + hashlib.sha256(profile["gsis_id"].encode()).hexdigest()
            + ".html"
        )
        (output / filename).write_text(render_profile(profile), encoding="utf-8")
        label = f"{profile['name']} · {profile['gsis_id']} · {', '.join(profile['teams'])} · {', '.join(profile['positions'])}"
        links.append(f'<li><a href="{filename}">{html.escape(label)}</a></li>')
    index = f"<h1>Player profiles · {season} {season_type}</h1><p>{len(profiles):,} players in the supplied snapshots.</p>"
    index += (
        '<label for="query">Find a player, ID, team, or position</label><br><input id="query" type="search"><ul id="players">'
        + "".join(links)
        + "</ul>"
    )
    index += """<script>document.getElementById('query').addEventListener('input', function () {
const query = this.value.toLowerCase();
document.querySelectorAll('#players li').forEach(row => { row.hidden = !row.textContent.toLowerCase().includes(query); });
});</script>"""
    (output / "index.html").write_text(
        page("Player directory", index), encoding="utf-8"
    )
    h.save_json(
        output / "profiles.json",
        {
            "profiles": profiles,
            "limitations": NOTES,
            "rate_definitions": RATES,
            "schema_version": 1,
        },
    )
    h.save_json(
        output / "profile_manifest.json",
        {
            "run_id": run,
            "season": season,
            "season_type": season_type,
            "gsis_id_filter": gsis_id,
            "profile_count": len(profiles),
            "source_position_metrics_manifest": h.record(root, metrics_path),
            "source_participation_manifest": h.record(root, participation_path),
            "consumed_files": consumed,
            "limitations": NOTES,
            "files": {p.name: h.record(root, p) for p in sorted(output.iterdir())},
            "code_sha256": h.sha256(Path(__file__)),
            "packages": {"polars": version("polars")},
        },
    )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--position-metrics-manifest", required=True, type=Path)
    parser.add_argument("--participation-manifest", required=True, type=Path)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--season-type", choices=("REG", "POST", "PRE"), default="REG")
    parser.add_argument("--gsis-id", help="Omit to generate the full player directory.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    output = build(
        args.project_root,
        args.position_metrics_manifest,
        args.participation_manifest,
        args.season,
        args.season_type,
        args.gsis_id,
    )
    print(f"Player directory: {output / 'index.html'}")
    print(f"Manifest: {output / 'profile_manifest.json'}")


if __name__ == "__main__":
    main()
