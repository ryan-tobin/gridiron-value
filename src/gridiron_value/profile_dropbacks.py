"""Attach verified eligible-dropback production to existing player profiles."""

import math

import polars as pl

from gridiron_value import historical as h

KEYS = ("season", "season_type", "game_id", "team", "gsis_id")
FIELDS = ("eligible_dropbacks", "total_epa", "epa_per_eligible_dropback")


def load(root, path, metrics, season, season_type):
    state = h.read_json(path)

    if season_type != "REG" or state.get("season_type") != "REG":
        raise ValueError("Dropback profiles currently support REG only.")

    if state.get("season") != season:
        raise ValueError("Dropback manifest season mismatch.")

    player_game = h.read_json(h.verify(root, metrics["source_player_game_manifest"]))
    coverage_record = player_game["source_coverage_manifest"]

    if state["source_coverage_manifest"] != coverage_record:
        raise ValueError("Dropbacks and profiles use different coverage snapshots.")

    coverage = h.read_json(h.verify(root, coverage_record))

    if state["source_snapshot"] != coverage["feeds"]["pbp"]["snapshot"]:
        raise ValueError("Dropback PBP snapshot mismatch.")

    record = state["files"]["player_game_totals"]

    frame = pl.read_csv(
        h.verify(root, record),
        schema_overrides={
            "game_id": pl.String,
            "team": pl.String,
            "gsis_id": pl.String,
        },
    )

    return frame, {
        "source_current_dropbacks_manifest": h.record(root, path),
        "source_coverage_manifest": coverage_record,
        "consumed_player_game_totals": record,
    }


def indexed(frame, season):
    missing = {*KEYS, *FIELDS} - set(frame.columns)

    if missing:
        raise ValueError(f"Missing dropback columns: {sorted(missing)}")

    result = {}

    for row in frame.to_dicts():
        if row["season"] != season or row["season_type"] != "REG":
            raise ValueError("Dropback table season/type mismatch.")

        for field in ("game_id", "team", "gsis_id"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"Missing dropback key: {field}")

            if row[field] != row[field].strip():
                raise ValueError(f"Whitespace in dropback key: {field}")

        key = tuple(row[field] for field in KEYS)

        if key in result:
            raise ValueError(f"Duplicate dropback key: {key}")

        values = [row[field] for field in FIELDS]

        if any(
            value is None or isinstance(value, bool) or not math.isfinite(float(value))
            for value in values
        ):
            raise ValueError("Dropback values must be finite and nonmissing.")

        count, total, rate = map(float, values)

        if count <= 0 or not count.is_integer():
            raise ValueError("Eligible dropbacks must be positive integers.")

        if not math.isclose(
            rate,
            total / count,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise ValueError("Dropback rate disagrees with its numerator/denominator.")

        result[key] = dict(zip(KEYS, key, strict=True)) | {
            "eligible_dropbacks": int(count),
            "total_epa": total,
            "epa_per_eligible_dropback": rate,
        }

    return result


def attach(profiles, frame, season, gsis_id=None):
    records = indexed(frame, season)

    if gsis_id is not None:
        records = {key: row for key, row in records.items() if key[-1] == gsis_id}

    matched, seen = set(), set()

    for profile in profiles:
        available = []

        for game in profile["games"]:
            key = tuple(game[field] for field in KEYS)

            if key in seen:
                raise ValueError(f"Duplicate profile game: {key}")

            seen.add(key)
            record = records.get(key)

            game["dropbacks"] = (
                {field: record[field] for field in FIELDS} if record else None
            )

            if record:
                matched.add(key)
                available.append(record)

        count = sum(row["eligible_dropbacks"] for row in available)
        total = sum(row["total_epa"] for row in available)

        profile["dropbacks"] = {
            "matched_games": len(available),
            "profile_games": len(profile["games"]),
            "eligible_dropbacks": count if available else None,
            "total_epa": total if available else None,
            "epa_per_eligible_dropback": total / count if count else None,
        }

    return {
        "gsis_id_filter": gsis_id,
        "input_rows": len(records),
        "matched_rows": len(matched),
        "unmatched_rows": [records[key] for key in sorted(records.keys() - matched)],
        "missing_rows_are_zero": False,
    }


def render_section(profile, table):
    summary = profile.get("dropbacks")

    if summary is None:
        return ""

    if not summary["matched_games"] and "QB" not in profile["positions"]:
        return ""

    body = "<h2>Eligible-dropback production</h2>"

    body += (
        f"<p>Matched dropback records: {summary['matched_games']} / "
        f"{summary['profile_games']} observed profile games. "
        "Values below cover matched records only. A missing record does not "
        "establish zero dropbacks or complete game coverage.</p>"
    )

    body += table(
        ["Metric", "Observed value"],
        [
            ("Eligible dropbacks", summary["eligible_dropbacks"]),
            ("Eligible-dropback EPA", summary["total_epa"]),
            (
                "EPA per eligible dropback",
                summary["epa_per_eligible_dropback"],
            ),
        ],
    )

    body += (
        "<p>EPA and counts use the same eligible plays, including sacks and "
        "scrambles. The combined rate is total EPA divided by total dropbacks.</p>"
    )

    rows = []

    for game in profile["games"]:
        values = game.get("dropbacks") or {}

        rows.append(
            (game["week"], game["game_id"], game["team"])
            + tuple(values.get(field) for field in FIELDS)
        )

    return body + table(
        [
            "Week",
            "Game",
            "Team",
            "Eligible dropbacks",
            "EPA",
            "EPA/dropback",
        ],
        rows,
    )


def validate(profiles, season):
    from copy import deepcopy

    present = any(
        "dropbacks" in profile or any("dropbacks" in game for game in profile["games"])
        for profile in profiles
    )

    if not present:
        return False

    records = []

    for profile in profiles:
        if "dropbacks" not in profile:
            raise ValueError("Incomplete dropback profile extension")

        for game in profile["games"]:
            if "dropbacks" not in game:
                raise ValueError("Missing game-level dropback extension")

            values = game["dropbacks"]

            if values is not None:
                if not isinstance(values, dict) or set(values) != set(FIELDS):
                    raise ValueError("Invalid game-level dropback fields.")

                records.append({**{key: game[key] for key in KEYS}, **values})

    schema = {
        "season": pl.Int64,
        "season_type": pl.String,
        "game_id": pl.String,
        "team": pl.String,
        "gsis_id": pl.String,
        "eligible_dropbacks": pl.Float64,
        "total_epa": pl.Float64,
        "epa_per_eligible_dropback": pl.Float64,
    }

    frame = pl.DataFrame(records) if records else pl.DataFrame(schema=schema)

    expected = deepcopy(profiles)
    attach(expected, frame, season)

    for actual, rebuilt in zip(profiles, expected, strict=True):
        if actual["dropbacks"] != rebuilt["dropbacks"]:
            raise ValueError("Dropback summary disagrees with game observatrions.")

    return True
