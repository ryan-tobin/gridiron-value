"""Build descriptive, snapshot-bound regular-season dropbacks and situation splits."""

import argparse
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridiron_value import historical as h
from gridiron_value.cohort import COHORT_VERSION, build_cohort

CONTEXT = {
    "down": "Down 1–4; other or missing values are unknown.",
    "distance": "Yards to go: 0–3 short, >3–7 medium, >7 long; invalid is unknown.",
    "field_position": "Yards to opponent goal: 0–20 red zone, >20–50 opponent half, >50–100 own half.",
    "score_state": "Possession-team pre-play score differential: trailing, tied, leading.",
}
LIMITATIONS = [
    "Observed snapshot production only; no live-feed or full-schedule completeness claim.",
    "EPA retains teammate and scheme contributions; no individual talent interpretation.",
    "REG only. POST/PRE are excluded by the existing versioned cohort contract.",
    "No Pass+ score, opponent adjustment, peer percentile, or passing EPA per attempt.",
    "Situation tables partition eligible dropbacks separately by dimension; do not add dimensions together.",
    "Unknown situation values remain an explicit bucket; small samples are descriptive, not reliability-qualified.",
]


def labels(frame):
    """Missing context is unknown, never zero or an inferred situation."""

    def col(name):
        return (
            pl.col(name).cast(pl.Float64, strict=False)
            if name in frame.columns
            else pl.lit(None, dtype=pl.Float64)
        )

    down, distance, field, score = (
        col(n) for n in ("down", "ydstogo", "yardline_100", "score_differential")
    )
    return frame.with_columns(
        pl.when(down.is_in([1, 2, 3, 4]))
        .then(down.cast(pl.Int64, strict=False).cast(pl.String))
        .otherwise(pl.lit("unknown"))
        .alias("situation_down"),
        pl.when(~distance.is_finite() | (distance < 0) | distance.is_null())
        .then(pl.lit("unknown"))
        .when(distance <= 3)
        .then(pl.lit("short"))
        .when(distance <= 7)
        .then(pl.lit("medium"))
        .otherwise(pl.lit("long"))
        .alias("situation_distance"),
        pl.when(~field.is_finite() | (field < 0) | (field > 100) | field.is_null())
        .then(pl.lit("unknown"))
        .when(field <= 20)
        .then(pl.lit("red_zone"))
        .when(field <= 50)
        .then(pl.lit("opponent_half_outside_red_zone"))
        .otherwise(pl.lit("own_half"))
        .alias("situation_field_position"),
        pl.when(~score.is_finite() | score.is_null())
        .then(pl.lit("unknown"))
        .when(score < 0)
        .then(pl.lit("trailing"))
        .when(score > 0)
        .then(pl.lit("leading"))
        .otherwise(pl.lit("tied"))
        .alias("situation_score_state"),
    )


def aggregate(frame, keys):
    return (
        frame.group_by(keys)
        .agg(
            pl.len().alias("eligible_dropbacks"),
            pl.col("epa").sum().alias("total_epa"),
            pl.col("epa").mean().alias("epa_per_eligible_dropback"),
            pl.col("game_id").n_unique().alias("games_observed"),
            (pl.col("epa") > 0).mean().alias("positive_epa_rate"),
        )
        .sort(keys)
    )


def summarize(cohort):
    frame = labels(cohort)
    tables = {}
    for scope, entity in (("player", "dropback_player_id"), ("team", "posteam")):
        keys = ["season", "season_type", entity]
        tables[scope + "_totals"] = aggregate(frame, keys)
        splits = []
        for dimension in CONTEXT:
            tagged = frame.with_columns(
                pl.col("situation_" + dimension).alias("bucket")
            )
            splits.append(
                aggregate(tagged, keys + ["bucket"]).with_columns(
                    pl.lit(dimension).alias("dimension")
                )
            )
        tables[scope + "_situations"] = pl.concat(splits).sort(
            keys + ["dimension", "bucket"]
        )
    return tables


def build(root, coverage_path, season):
    root = Path(root).resolve()
    coverage_path = h.locate(root, coverage_path)
    manifest = h.read_json(coverage_path)
    if manifest.get("season") != season:
        raise ValueError("Coverage manifest season does not match request.")
    feed = manifest.get("feeds", {}).get("pbp", {})
    if feed.get("status") != "available":
        raise ValueError("PBP feed is unavailable.")
    raw_path = h.verify(root, feed["snapshot"])
    raw = pl.read_parquet(raw_path)
    if (
        "season" not in raw.columns
        or raw["season"].null_count()
        or raw["season"].unique().to_list() != [season]
    ):
        raise ValueError("PBP snapshot must contain only the requested season.")
    cohort, exclusions = build_cohort(raw)
    required = {"week", "game_date", "posteam", "defteam"}
    if required - set(cohort.columns):
        raise ValueError("Missing week/date/team fields.")
    if cohort.select(sorted(required)).null_count().sum_horizontal().item():
        raise ValueError("Missing week/date/team values in eligible cohort.")
    if cohort.filter(
        (pl.col("week") < 1) | (pl.col("week") != pl.col("week").floor())
    ).height:
        raise ValueError("Eligible weeks must be positive integers.")
    for team in ("posteam", "defteam"):
        if cohort.filter(pl.col(team).cast(pl.String).str.strip_chars() == "").height:
            raise ValueError("Blank team identifier.")
    dates = cohort.with_columns(pl.col("game_date").cast(pl.String).str.to_date())
    if (
        dates.group_by("game_id")
        .agg(
            pl.col("week").n_unique().alias("weeks"),
            pl.col("game_date").n_unique().alias("dates"),
        )
        .filter((pl.col("weeks") != 1) | (pl.col("dates") != 1))
        .height
    ):
        raise ValueError("A game has conflicting weeks or dates.")
    tables = summarize(cohort)
    # Partition event categories without treating overlapping or missing flags as zero.
    sack, scramble = pl.col("sack"), pl.col("qb_scramble")
    event = (
        pl.when((sack == 1) & (scramble == 1))
        .then(pl.lit("conflicting_flags"))
        .when(
            sack.is_null()
            | scramble.is_null()
            | ~sack.is_in([0, 1])
            | ~scramble.is_in([0, 1])
        )
        .then(pl.lit("unknown_flags"))
        .when(sack == 1)
        .then(pl.lit("sack"))
        .when(scramble == 1)
        .then(pl.lit("scramble"))
        .otherwise(pl.lit("other_eligible_dropback"))
    )
    tables["event_audit"] = aggregate(
        cohort.with_columns(event.alias("event")), ["season", "season_type", "event"]
    )
    tables["exclusions"] = pl.DataFrame(exclusions)
    tables["identity_audit"] = (
        cohort.group_by("identity_source").len().sort("identity_source")
    )
    weeks = sorted(cohort["week"].unique().to_list())
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"current_dropbacks_{run}"
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    target = output / "dropbacks.parquet"
    cohort.write_parquet(target, compression="zstd")
    files["cohort"] = h.record(root, target)
    for name, table in tables.items():
        target = output / f"{name}.csv"
        table.write_csv(target)
        files[name] = h.record(root, target)
    report = output / "report.md"
    report.write_text(
        "# Current-season eligible dropbacks\n\n"
        f"Season {season}, REG, observed weeks {weeks}. {cohort.height:,} eligible dropbacks in {cohort['game_id'].n_unique()} games.\n\n"
        "Rates use EPA and counts from exactly the same eligible plays. Other eligible dropbacks are not certified official pass attempts. "
        "The event audit partitions the cohort; it does not reconcile the player-stat feed's passing EPA numerator. "
        "Passing EPA per attempt remains withheld.\n\n"
        + "\n".join("- " + x for x in LIMITATIONS)
        + "\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in CONTEXT.items())
        + "\n",
        encoding="utf-8",
    )
    files["report"] = h.record(root, report)
    state = {
        "schema_version": 1,
        "run_id": run,
        "season": season,
        "season_type": "REG",
        "source_coverage_manifest": h.record(root, coverage_path),
        "source_snapshot": feed["snapshot"],
        "source_retrieved_at": feed.get("finished_at_utc"),
        "cohort_version": COHORT_VERSION,
        "rows": cohort.height,
        "observed_weeks": weeks,
        "games_observed": cohort["game_id"].n_unique(),
        "chronological_policy": {
            "first_test_week": 5,
            "has_test_week": any(w >= 5 for w in weeks),
            "status": "not_evaluated",
            "reason": "Descriptive adapter only; no model fit or Pass+ score.",
        },
        "situation_definitions": CONTEXT,
        "limitations": LIMITATIONS,
        "files": files,
        "code": {
            n: h.record(root, Path(__file__).with_name(n))
            for n in ("current_dropbacks.py", "cohort.py", "historical.py")
        },
        "packages": {"polars": pl.__version__},
    }
    path = output / "current_dropbacks_manifest.json"
    h.save_json(path, state)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-manifest", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(build(args.project_root, args.coverage_manifest, args.season))


if __name__ == "__main__":
    main()
