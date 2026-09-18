import html
import math
from pathlib import Path

import polars as pl

from gridiron_value import current_dropbacks as cd
from gridiron_value import historical as h
from gridiron_value import profile_dropbacks as pd

TITLES = {
    "down": "Down",
    "distance": "Distance to first down",
    "field_position": "Field position",
    "score_state": "Score state before the play",
}


def attach(profiles, cohort, season):
    if not pd.validate(profiles, season):
        raise ValueError("Situation splits require matched dropback summaries.")

    frame = cd.labels(
        cohort.rename(
            {
                "posteam": "team",
                "dropback_player_id": "gsis_id",
            }
        )
    )

    columns = [
        *pd.KEYS,
        "play_id",
        "epa",
        *["situation_" + key for key in TITLES],
    ]

    groups, seen = {}, set()

    for row in frame.select(columns).to_dicts():
        if row["season"] != season or row["season_type"] != "REG":
            raise ValueError("Situation cohort season/type mismatch,")

        if any(row[key] is None for key in (*pd.KEYS, "play_id", "epa")):
            raise ValueError("Missing situation play identity or EPA.")

        if not math.isfinite(float(row["epa"])):
            raise ValueError("nonfinite situation EPA.")

        play = (
            row["season"],
            row["game_id"],
            row["play_id"],
        )

        if play in seen:
            raise ValueError("Duplicate situation play.")

        seen.add(play)

        key = tuple(row[field] for field in pd.KEYS)
        groups.setdefault(key, []).append(row)

    matched = 0

    for profile in profiles:
        selected = []

        for game in profile["games"]:
            expected = game["dropbacks"]

            if expected is None:
                continue

            key = tuple(game[field] for field in pd.KEYS)
            rows = groups.get(key, [])
            total = math.fsum(row["epa"] for row in rows)

            if len(rows) != expected["eligible_dropbacks"] or not math.isclose(
                total,
                expected["total_epa"],
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError("Situation plays disagree with matched game totals.")

            selected.extend(rows)

        profile["situations"] = None

        if not selected:
            continue

        matched += len(selected)
        plays = pl.DataFrame(selected)
        profile["situations"] = {}

        for dimension in TITLES:
            bucket = "situation_" + dimension

            profile["situations"][dimension] = (
                cd.aggregate(plays, bucket).rename({bucket: "bucket"}).to_dicts()
            )

    return {
        "scope": "matched_profile_dropbacks",
        "source_plays": frame.height,
        "matched_plays": matched,
        "plays_outside_selected_matches": frame.height - matched,
    }


def load_and_attach(root, manifest_path, profiles, season):
    state = h.read_json(manifest_path)
    record = state["files"]["cohort"]
    cohort = pl.read_parquet(h.verify(root, record))

    return {
        "consumed_cohort": record,
        "audit": attach(profiles, cohort, season),
        "definitions": cd.CONTEXT,
        "code_sha256": {
            "profile_situations.py": h.sha256(Path(__file__)),
            "current_dropbacks.py": h.sha256(Path(cd.__file__)),
        },
    }


def render_section(profile, table):
    splits = profile.get("situations")

    if not splits:
        return ""

    body = "<h2>Situational splits</h2>"
    body += (
        "<p>Each section partitions this player's matched eligible dropbacks. "
        "Do not add sections together. Missing context appears as unknown. "
        "Small samples are descriptive; these rates are not adjusted for "
        "opponent or differences in situation difficulty.</p>"
    )

    for dimension, title in TITLES.items():
        body += f"<details><summary>{title}</summary>"
        body += f"<p>{html.escape(cd.CONTEXT[dimension])}</p>"

        body += table(
            [
                "Situation",
                "Dropbacks",
                "Games",
                "EPA",
                "EPA/dropback",
                "Positive EPA",
            ],
            [
                (
                    row["bucket"].replace("_", " ").capitalize(),
                    row["eligible_dropbacks"],
                    row["games_observed"],
                    row["total_epa"],
                    row["epa_per_eligible_dropback"],
                    f"{100 * row['positive_epa_rate']:.1f}%",
                )
                for row in splits[dimension]
            ],
        )

        body += "</details>"

    return body
