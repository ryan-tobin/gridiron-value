"""Audit passing-stat definitions against one preserved REG PBP snapshot."""

import argparse
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridiron_value import historical as h
from gridiron_value.cohort import COHORT_VERSION, build_cohort

KEYS = ["season", "season_type", "game_id", "team", "player_id"]
FLAGS = [
    "pass_attempt",
    "complete_pass",
    "sack",
    "qb_scramble",
    "qb_spike",
    "two_point_attempt",
]
SOURCE_URL = (
    "https://github.com/nflverse/nflfastR/blob/"
    "4fc75e88b308ed592e11700e580e9e5380bdc46b/R/calculate_stats.R"
)
CHECKS = {
    "attempts": ("attempts", "count_reconstruction"),
    "completions": ("completions", "count_reconstruction"),
    "sacks": ("sacks_suffered", "count_reconstruction"),
    "source_qb_epa": ("passing_epa", "numerator_reconstruction"),
    "source_epa": ("passing_epa", "definition_comparison"),
    "eligible_epa": ("passing_epa", "definition_comparison"),
}
NOTES = [
    "REG only; joins include season, season_type, game, team and stable player ID.",
    "Count candidates are PBP reconstructions, not an independent official stat-ID feed.",
    "Source EPA candidate: qb_epa on play_type pass/qb_spike, using passer_player_id.",
    "This source candidate includes sacks/spikes/two-point plays when in those play types.",
    "Current upstream source supports this candidate; its producing revision is not known for this snapshot.",
    "Eligible EPA reuses the existing cohort and its dropback actor; ordinary epa is retained.",
    "No missing source row is replaced with zero. Incomplete sums are withheld.",
    "Definition-comparison differences are expected and are not pipeline failures.",
    "Matching totals do not prove individual event completeness or validate future snapshots.",
    "No EPA rate, Pass+ score or opponent adjustment is generated.",
]


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def clean(frame, columns):
    return frame.with_columns(
        pl.col(c).cast(pl.String).str.strip_chars().replace("", None) for c in columns
    )


def groups(frame):
    result = defaultdict(list)
    for row in frame.iter_rows(named=True):
        key = tuple(row[c] for c in KEYS)
        if all(v is not None for v in key):
            result[key].append(row)
    return result


def total(rows, field):
    bad = sum(not finite(r[field]) for r in rows)
    return (None if bad else math.fsum(r[field] for r in rows), len(rows), bad)


def candidates(rows):
    source = [r for r in rows if r["source_passing"]]
    attempts = [r for r in source if r["attempt_candidate"]]
    sacks = [r for r in source if r["sack_candidate"]]

    def invalid(fields):
        return sum(any(r[f] not in (0, 1) for f in fields) for r in source)

    attempt_bad = invalid(["pass_attempt", "sack", "qb_scramble", "two_point_attempt"])
    complete_bad = attempt_bad + sum(r["complete_pass"] not in (0, 1) for r in attempts)
    sack_bad = invalid(["sack", "two_point_attempt"])
    completed = sum(r["complete_pass"] == 1 for r in attempts)
    return {
        "attempts": (None if attempt_bad else len(attempts), len(source), attempt_bad),
        "completions": (None if complete_bad else completed, len(source), complete_bad),
        "sacks": (None if sack_bad else len(sacks), len(source), sack_bad),
        "source_qb_epa": total(source, "qb_epa"),
        "source_epa": total(source, "epa"),
    }


def reconcile(pbp, stats, season):
    required_pbp = set(FLAGS + ["qb_epa", "passer_player_id", "posteam"])
    required_stats = set(
        KEYS + ["attempts", "completions", "sacks_suffered", "passing_epa"]
    )
    for name, frame, required in (
        ("pbp", pbp, required_pbp | {"season", "season_type"}),
        ("player_stats", stats, required_stats),
    ):
        if required - set(frame.columns):
            raise ValueError(
                f"{name} missing columns: {sorted(required - set(frame.columns))}"
            )
        if (
            frame.is_empty()
            or frame["season"].null_count()
            or frame["season"].unique().to_list() != [season]
        ):
            raise ValueError(f"{name} must contain only season {season}.")
        if frame["season_type"].null_count():
            raise ValueError(f"{name} has missing season types.")

    cohort, exclusions = build_cohort(pbp)
    raw = pbp.filter(pl.col("season_type") == "REG")
    stats = clean(
        stats.filter(pl.col("season_type") == "REG"), ["game_id", "team", "player_id"]
    )
    if stats.is_empty():
        raise ValueError("No REG player-stat rows.")

    keyed = stats.filter(pl.all_horizontal(pl.col(c).is_not_null() for c in KEYS))
    if keyed.select(KEYS).unique().height != keyed.height:
        raise ValueError("Duplicate player-stat keys.")

    ledger = clean(raw, ["game_id", "posteam", "passer_player_id"])
    source = pl.col("play_type").is_in(["pass", "qb_spike"])
    ledger = (
        ledger.with_columns(
            source.fill_null(False).alias("source_passing"),
            (
                source
                & (pl.col("pass_attempt") == 1)
                & (pl.col("sack") == 0)
                & (pl.col("qb_scramble") == 0)
                & (pl.col("two_point_attempt") == 0)
            )
            .fill_null(False)
            .alias("attempt_candidate"),
            (source & (pl.col("sack") == 1) & (pl.col("two_point_attempt") == 0))
            .fill_null(False)
            .alias("sack_candidate"),
        )
        .join(
            cohort.select("game_id", "play_id", "dropback_player_id").with_columns(
                pl.lit(True).alias("eligible")
            ),
            on=["game_id", "play_id"],
            how="left",
            validate="1:1",
        )
        .with_columns(pl.col("eligible").fill_null(False))
    )
    passing = ledger.filter(pl.col("source_passing")).rename(
        {"posteam": "team", "passer_player_id": "player_id"}
    )

    component = (
        pl.when(
            ~pl.all_horizontal(
                pl.col(c).is_in([0, 1]).fill_null(False)
                for c in ("two_point_attempt", "sack", "qb_spike")
            )
        )
        .then(pl.lit("unknown_flags"))
        .when((pl.col("sack") == 1) & (pl.col("qb_spike") == 1))
        .then(pl.lit("conflicting_flags"))
        .when(pl.col("two_point_attempt") == 1)
        .then(pl.lit("two_point"))
        .when(pl.col("sack") == 1)
        .then(pl.lit("sack"))
        .when(pl.col("qb_spike") == 1)
        .then(pl.lit("spike"))
        .otherwise(pl.lit("other_pass"))
    )
    components = (
        passing.with_columns(component.alias("component"))
        .group_by(KEYS + ["component"])
        .agg(
            pl.len().alias("plays"),
            pl.col("attempt_candidate").sum().alias("attempt_candidates"),
            (pl.col("penalty") == 1).sum().alias("penalty_flagged"),
            *[
                expr
                for field in ("epa", "qb_epa")
                for expr in (
                    (~pl.col(field).is_finite().fill_null(False))
                    .sum()
                    .alias(field + "_invalid"),
                    pl.when(pl.col(field).is_finite().fill_null(False).all())
                    .then(pl.col(field).sum())
                    .otherwise(None)
                    .alias(field + "_sum"),
                )
            ],
        )
        .sort(KEYS + ["component"])
    )

    source_groups = groups(passing)
    eligible_groups = groups(
        cohort.rename({"posteam": "team", "dropback_player_id": "player_id"})
    )
    stat_groups = groups(keyed)
    active = {
        key
        for key, rows in stat_groups.items()
        if rows[0]["passing_epa"] is not None
        or any(
            rows[0][c] not in (0, None)
            for c in ("attempts", "completions", "sacks_suffered")
        )
    }

    comparisons = []
    for key in sorted(active | set(source_groups) | set(eligible_groups)):
        stat = stat_groups.get(key, [None])[0]
        values = candidates(source_groups.get(key, []))
        values["eligible_epa"] = total(eligible_groups.get(key, []), "epa")

        for candidate, (field, purpose) in CHECKS.items():
            available = key in (
                eligible_groups if candidate == "eligible_epa" else source_groups
            )
            value, n, bad = values[candidate]
            observed = stat[field] if stat is not None else None
            delta = (
                value - observed
                if available and finite(value) and finite(observed)
                else None
            )
            status = (
                "missing_stats_row"
                if stat is None
                else "missing_pbp_group"
                if not available
                else "incomplete_pbp"
                if bad
                else "missing_or_nonfinite_stat"
                if not finite(observed)
                else "match"
                if abs(delta) <= 1e-8
                else "difference"
            )
            comparisons.append(
                {
                    **dict(zip(KEYS, key)),
                    "candidate": candidate,
                    "purpose": purpose,
                    "stat_field": field,
                    "stat_value": observed if finite(observed) else None,
                    "pbp_value": value if available else None,
                    "pbp_rows": n if available else None,
                    "invalid_pbp_values": bad if available else None,
                    "pbp_minus_stat": delta,
                    "status": status,
                }
            )

    comparisons = pl.DataFrame(comparisons, infer_schema_length=None)

    # Keep every relevant play, including excluded and unattributed events.
    relevant = ledger.filter(
        pl.col("source_passing")
        | pl.col("eligible")
        | (pl.col("qb_dropback") == 1)
        | (pl.col("pass_attempt") == 1)
        | pl.col("passer_player_id").is_not_null()
    )
    ledger_columns = [
        "season",
        "season_type",
        "game_id",
        "play_id",
        "posteam",
        "passer_player_id",
        "passer_id",
        "dropback_player_id",
        "play_type",
        "qb_dropback",
        "qb_kneel",
        "penalty",
        "epa",
        "qb_epa",
        *FLAGS,
        "source_passing",
        "attempt_candidate",
        "sack_candidate",
        "eligible",
        "desc",
    ]
    return {
        "comparisons": comparisons,
        "summary": comparisons.group_by("candidate", "purpose", "status")
        .len()
        .sort("candidate", "status"),
        "play_ledger": relevant.select(ledger_columns),
        "source_components": components,
        "unattributed_stats": stats.filter(
            ~pl.all_horizontal(pl.col(c).is_not_null() for c in KEYS)
        ),
        "unattributed_passing_plays": passing.filter(
            ~pl.all_horizontal(pl.col(c).is_not_null() for c in KEYS)
        ),
        "exclusions": pl.DataFrame(exclusions),
    }


def build(root, coverage_path, season):
    root = Path(root).resolve()
    coverage_path = h.locate(root, coverage_path)
    coverage = h.read_json(coverage_path)
    if coverage.get("season") != season:
        raise ValueError("Coverage manifest season differs from request.")

    frames, sources = {}, {}
    for name in ("pbp", "player_stats"):
        feed = coverage["feeds"][name]
        if feed["status"] != "available":
            raise ValueError(f"Unavailable feed: {name}")
        frames[name] = pl.read_parquet(h.verify(root, feed["snapshot"]))
        sources[name] = feed["snapshot"]

    tables = reconcile(frames["pbp"], frames["player_stats"], season)
    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"reconcile_passing_{run}"
    output.mkdir(parents=True, exist_ok=False)

    files = {}
    for name, table in tables.items():
        path = output / f"{name}.csv"
        table.write_csv(path)
        files[name] = h.record(root, path)

    path = output / "reconciliation_manifest.json"
    h.save_json(
        path,
        {
            "run_id": run,
            "audit_version": "0.1.0",
            "season": season,
            "season_type": "REG",
            "source_coverage_manifest": h.record(root, coverage_path),
            "sources": sources,
            "source_retrieval_times": {
                n: coverage["feeds"][n].get("finished_at_utc") for n in sources
            },
            "input_rows": {n: f.height for n, f in frames.items()},
            "input_reg_rows": {
                n: f.filter(pl.col("season_type") == "REG").height
                for n, f in frames.items()
            },
            "cohort_version": COHORT_VERSION,
            "absolute_tolerance": 1e-8,
            "upstream_reference": SOURCE_URL,
            "notes": NOTES,
            "files": files,
            "code": {
                n: h.record(root, Path(__file__).with_name(n))
                for n in ("reconcile_passing.py", "cohort.py", "historical.py")
            },
            "packages": {"polars": pl.__version__},
        },
    )

    with pl.Config(tbl_rows=30, tbl_width_chars=120):
        print(tables["summary"])
    print(f"Unattributed stats: {tables['unattributed_stats'].height}")
    print(f"Unattributed passing plays: {tables['unattributed_passing_plays'].height}")
    print(f"Manifest: {path.relative_to(root)}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-manifest", type=Path, required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    build(args.project_root, args.coverage_manifest, args.season)


if __name__ == "__main__":
    main()