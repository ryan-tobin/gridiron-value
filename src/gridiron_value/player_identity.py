"""Resolve snap IDs using explicit source mappings; retain every source row."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import nflreadpy as nfl
import polars as pl
from nflreadpy.config import update_config

from gridiron_value import historical as h


def clean(value):
    return str(value).strip() if value is not None else ""


def resolve_snaps(snaps, sources):
    """Resolve only single-valued PFR→GSIS links agreed across all sources."""
    if "pfr_player_id" not in snaps.columns:
        raise ValueError("Snap source lacks pfr_player_id.")
    reserved = {"resolved_gsis_id", "identity_status", "identity_sources", "candidate_gsis_ids"}
    if reserved & set(snaps.columns):
        raise ValueError("Snap table already contains identity output columns.")
    candidates = defaultdict(lambda: defaultdict(set))
    for name, frame in sources.items():
        if not {"pfr_id", "gsis_id"}.issubset(frame.columns):
            raise ValueError(f"{name} lacks explicit pfr_id/gsis_id mappings.")
        for pfr, gsis in frame.select("pfr_id", "gsis_id").iter_rows():
            pfr, gsis = clean(pfr), clean(gsis)
            if pfr and gsis:
                candidates[pfr][gsis].add(name)
    ids, statuses, provenance, alternatives = [], [], [], []
    for value in snaps["pfr_player_id"]:
        pfr = clean(value)
        options = candidates.get(pfr, {})
        status = ("missing_pfr_id" if not pfr else
                  "unmapped" if not options else
                  "conflict" if len(options) > 1 else "resolved")
        ids.append(next(iter(options)) if status == "resolved" else None)
        statuses.append(status)
        provenance.append("|".join(sorted({s for group in options.values() for s in group})))
        alternatives.append("|".join(sorted(options)))
    return snaps.with_columns(
        pl.Series("resolved_gsis_id", ids, dtype=pl.String),
        pl.Series("identity_status", statuses, dtype=pl.String),
        pl.Series("identity_sources", provenance, dtype=pl.String),
        pl.Series("candidate_gsis_ids", alternatives, dtype=pl.String),
    )


def split_stats(frame):
    if "player_id" not in frame.columns:
        raise ValueError("Player stats lack player_id.")
    valid = pl.col("player_id").cast(pl.String).str.strip_chars().fill_null("") != ""
    return frame.filter(valid), frame.filter(~valid)

def apply_overrides(frame, overrides):
    """Apply reviewed overrides without deleting the original evidence."""
    rows = frame.to_dicts()
    seen = set()

    for item in overrides:
        keys = ("season", "game_id", "team", "pfr_player_id")
        key = tuple(item[k] for k in keys)

        if key in seen:
            raise ValueError("Duplicate override key.")
        seen.add(key)

        if not clean(item.get("reason")) or not item.get("evidence"):
            raise ValueError("Override requires reason and evidence.")

        matches = [
            row for row in rows
            if tuple(row[k] for k in keys) == key
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Override must match exactly one snap row: {key}"
            )

        row = matches[0]
        candidates = set(row["candidate_gsis_ids"].split("|"))

        if (
            row["identity_status"] != "conflict"
            or candidates != set(item["expected_candidates"])
        ):
            raise ValueError(
                "Override conflict evidence changed; review it again."
            )

        if item["resolved_gsis_id"] not in candidates:
            raise ValueError(
                "Override must select an existing candidate."
            )

        row["resolved_gsis_id"] = item["resolved_gsis_id"]
        row["identity_status"] = "resolved_override"
        row["override_reason"] = item["reason"]

    for row in rows:
        row.setdefault("override_reason", None)

    return pl.DataFrame(
        rows,
        schema={**frame.schema, "override_reason": pl.String},
    )

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--players-snapshot",
        type=Path,
        help="Reuse an existing player registry Parquet.",
    )
    parser.add_argument(
        "--reuse-identity-manifest",
        type=Path,
        help="Verify and reuse the registry from a previous identity run.",
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        help="JSON array of reviewed exact-record conflict overrides.",
    )
    args = parser.parse_args()

    if args.players_snapshot and args.reuse_identity_manifest:
        parser.error("Choose only one registry reuse option.")
    root = args.project_root.resolve()
    manifest_path = h.locate(root, args.coverage_manifest)
    audit = h.read_json(manifest_path)
    season = audit["season"]
    frames, source_records = {}, {}
    for name in ("rosters", "snap_counts", "player_stats"):
        entry = audit["feeds"][name]
        if entry["status"] != "available":
            raise ValueError(f"Required feed unavailable: {name}")
        info = entry["snapshot"]
        frames[name] = pl.read_parquet(h.verify(root, info)).filter(pl.col("season") == season)
        source_records[name] = info
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"player_identity_{run}"
    output.mkdir(parents=True, exist_ok=False)
    registry_error = None
    sources = {"rosters": frames["rosters"]}
    if args.reuse_identity_manifest:
        prior_path = h.locate(root, args.reuse_identity_manifest)
        prior = h.read_json(prior_path)

        if prior["source_coverage_manifest"] != h.record(root, manifest_path):
            raise ValueError(
                "Previous identity run used a different coverage snapshot."
            )

        info = prior["sources"]["players"]
        sources["players"] = pl.read_parquet(h.verify(root, info))
        source_records["players"] = info
        source_records["prior_identity_manifest"] = h.record(root, prior_path)
    elif args.players_snapshot:
        registry_path = h.locate(root, args.players_snapshot)
        sources["players"] = pl.read_parquet(registry_path)
        source_records["players"] = h.record(root, registry_path)
    else:
        update_config(cache_mode="off", timeout=30)
        try:
            registry = nfl.load_players()
        except Exception as error:
            registry_error = f"{type(error).__name__}: {str(error)[:1200]}"
            print("Player registry download failed; proceeding with roster mappings only.")
        else:
            registry_path = root / "data" / "raw" / f"player_identity_{run}" / "players.parquet"
            registry_path.parent.mkdir(parents=True, exist_ok=False)
            registry.write_parquet(registry_path, compression="zstd")
            sources["players"] = registry
            source_records["players"] = h.record(root, registry_path)
    mapped = resolve_snaps(frames["snap_counts"], sources)


    overrides = []
    if args.overrides:
        override_path = h.locate(root, args.overrides)
        overrides = h.read_json(override_path)
        mapped = apply_overrides(mapped, overrides)
        source_records["overrides"] = h.record(root, override_path)

    stats, anonymous = split_stats(frames["player_stats"])

    resolved = pl.col("identity_status").is_in(
        ["resolved", "resolved_override"]
    )
    unresolved = mapped.filter(~resolved)

    keys = ["season", "game_id", "team", "resolved_gsis_id"]
    duplicates = (
        mapped.filter(resolved)
        .group_by(keys)
        .len()
        .filter(pl.col("len") > 1)
    )
    files = {}
    for name, table in {
        "snap_counts_identified": mapped, "snap_counts_unresolved": unresolved,
        "player_stats_identified": stats, "player_stats_unattributed": anonymous,
        "duplicate_snap_keys": duplicates,
    }.items():
        path = output / f"{name}.parquet"
        table.write_parquet(path, compression="zstd")
        files[name] = h.record(root, path)
    counts = mapped.group_by("identity_status").len().sort("identity_status").to_dicts()
    state = {
        "run_id": run, "season": season,
        "status": "needs_review" if registry_error or unresolved.height or anonymous.height or duplicates.height else "completed",
        "source_coverage_manifest": h.record(root, manifest_path),
        "sources": source_records, "registry_error": registry_error,
        "snap_status_counts": counts, "snap_input_rows": mapped.height,
        "unattributed_stats_rows": anonymous.height, "duplicate_snap_key_groups": duplicates.height,
        "files": files, "code": h.record(root, Path(__file__).resolve()),
        "packages": {n: version(n) for n in ("nflreadpy", "polars")},
        "limitations": [
            "Identity mapping only: current registry/rosters do not establish historical team membership.",
            "Conflicts remain unresolved unless an exact-record reviewed override is supplied; no name matching.",
            "All source rows preserved; anonymous statistics are not assumed to be empty or team totals.",
            "Row coverage is not a position-specific or snap-weighted coverage measure.",
        ],
        "reviewed_overrides": overrides,
    }
    manifest = output / "identity_manifest.json"
    h.save_json(manifest, state)
    print(f"Snap rows preserved: {mapped.height:,}")
    for row in counts:
        print(f"  {row['identity_status']}: {row['len']:,}")
    print(f"Unattributed player-stat rows preserved separately: {anonymous.height}")
    print(f"Duplicate resolved snap-key groups: {duplicates.height}")
    if unresolved.height:
        print("Unresolved rows by position:")
        print(unresolved.group_by("position").len().sort("len", descending=True).to_dicts())
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()
