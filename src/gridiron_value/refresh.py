import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gridiron_value import historical as h

FEEDS = (
    "pbp",
    "player_stats",
    "rosters",
    "snap_counts",
    "schedules",
    "ngs_passing",
    "ngs_rushing",
    "ngs_receiving",
)


def run_stage(root, module, arguments, manifest_name, log):
    command = [
        sys.executable,
        "-m",
        f"gridiron_value.{module}",
        *map(str, arguments),
    ]

    candidates = []

    with (
        log.open("w", encoding="utf-8") as handle,
        subprocess.Popen(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as process,
    ):
        for line in process.stdout:
            print(line, end="", flush=True)
            handle.write(line)
            handle.flush()

            value = line.strip()

            if value.startswith("Manifest: "):
                value = value.removeprefix("Manifest: ")
            elif module != "current_dropbacks":
                continue

            if Path(value).name == manifest_name:
                candidates.append(h.locate(root, value))

        code = process.wait()

    if code:
        raise RuntimeError(f"{module} exited with code {code}; see {log}")

    if len(set(candidates)) != 1:
        raise ValueError(f"Expected one {manifest_name} in {log}")

    path = candidates[0]
    h.verify_manifest(root, h.record(root, path))

    return path


def compare_coverage(previous, current):
    if previous and previous["season"] != current["season"]:
        raise ValueError("Coverage comparison seasons differ.")

    old = previous.get("feeds", {})
    rows = []

    for name in sorted(set(old) | set(current["feeds"])):
        before = old.get(name, {})
        after = current["feeds"].get(name, {})

        old_rows = before.get("rows")
        new_rows = after.get("rows")

        rows.append(
            {
                "feed": name,
                "previous_status": before.get("status", "not_requested"),
                "current_status": after.get("status", "not_requested"),
                "previous_rows": old_rows,
                "current_rows": new_rows,
                "row_delta": (
                    new_rows - old_rows
                    if old_rows is not None and new_rows is not None
                    else None
                ),
                "previous_week_counts": before.get("week_counts"),
                "current_week_counts": after.get("week_counts"),
                "previous_retrieved_at": before.get("finished_at_utc"),
                "current_retrieved_at": after.get("finished_at_utc"),
            }
        )

    return rows


def check_coverage(root, state, season):
    if state["season"] != season:
        raise ValueError("Coverage season differs from the requested season.")

    for name in FEEDS:
        feed = state.get("feeds", {}).get(name, {})

        allowed = (
            {"available", "empty_for_season"}
            if name.startswith("ngs_")
            else {"available"}
        )

        if feed.get("status") not in allowed:
            raise ValueError(
                f"Required feed {name}: {feed.get('status', 'not_requested')}"
            )

        h.verify(root, feed["snapshot"])


def build(root, season, baseline=None, coverage=None, overrides=None):
    root = Path(root).resolve()

    if not (root / "pyproject.toml").exists():
        raise ValueError("Run from the repository root.")

    supplied = {
        name: h.locate(root, path)
        for name, path in (
            ("baseline", baseline),
            ("coverage", coverage),
            ("overrides", overrides),
        )
        if path is not None
    }

    references = {name: h.record(root, path) for name, path in supplied.items()}

    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"refresh_{run}"
    output.mkdir(parents=True, exist_ok=False)

    checkpoint = output / "refresh_manifest.json"

    state = {
        "run_id": run,
        "season": season,
        "status": "running",
        "inputs": references,
        "stages": {},
        "code_sha256": h.sha256(Path(__file__)),
    }

    h.save_json(checkpoint, state)
    print(f"Refresh checkpoint: {checkpoint}", flush=True)

    def stage(module, arguments, filename):
        state["active_stage"] = module
        h.save_json(checkpoint, state)

        path = run_stage(
            root,
            module,
            arguments,
            filename,
            output / f"{module}.log",
        )

        state["stages"][module] = h.record(root, path)
        h.save_json(checkpoint, state)

        return path

    try:
        coverage_path = supplied.get("coverage")

        if coverage_path is None:
            coverage_path = stage(
                "coverage_audit",
                ["--season", season, "--feeds", *FEEDS],
                "coverage_manifest.json",
            )
        else:
            state["stages"]["coverage_audit"] = h.record(root, coverage_path)
            h.save_json(checkpoint, state)

        current = h.read_json(coverage_path)
        previous = h.read_json(supplied["baseline"]) if "baseline" in supplied else {}

        changes = output / "coverage_changes.json"

        h.save_json(
            changes,
            {"feeds": compare_coverage(previous, current)},
        )

        state["coverage_changes"] = h.record(root, changes)
        h.save_json(checkpoint, state)

        check_coverage(root, current, season)

        identity_args = ["--coverage-manifest", coverage_path]

        if "overrides" in supplied:
            identity_args += [
                "--overrides",
                supplied["overrides"],
            ]

        identity = stage(
            "player_identity",
            identity_args,
            "identity_manifest.json",
        )

        identity_state = h.read_json(identity)

        state["identity_review"] = {
            key: identity_state.get(key)
            for key in (
                "status",
                "registry_error",
                "snap_status_counts",
                "unattributed_stats_rows",
                "duplicate_snap_key_groups",
            )
        }

        h.save_json(checkpoint, state)

        reconciliation = stage(
            "reconcile_passing",
            [
                "--coverage-manifest",
                coverage_path,
                "--season",
                season,
            ],
            "reconciliation_manifest.json",
        )

        audit = h.read_json(reconciliation)
        summary = pl.read_csv(h.verify(root, audit["files"]["summary"]))

        if summary.filter(
            (pl.col("purpose") != "definition_comparison")
            & (pl.col("status") != "match")
        ).height:
            raise ValueError(f"Passing reconstruction needs review: {reconciliation}")

        player_game = stage(
            "player_game",
            [
                "--coverage-manifest",
                coverage_path,
                "--identity-manifest",
                identity,
            ],
            "player_game_manifest.json",
        )

        participation = stage(
            "participation",
            [
                "--identity-manifest",
                identity,
                "--player-game-manifest",
                player_game,
            ],
            "participation_manifest.json",
        )

        metrics = stage(
            "position_metrics",
            [
                "--player-game-manifest",
                player_game,
                "--participation-manifest",
                participation,
            ],
            "position_metrics_manifest.json",
        )

        dropbacks = stage(
            "current_dropbacks",
            [
                "--coverage-manifest",
                coverage_path,
                "--season",
                season,
            ],
            "current_dropbacks_manifest.json",
        )

        site = stage(
            "rebuild_site",
            [
                "--position-metrics-manifest",
                metrics,
                "--participation-manifest",
                participation,
                "--current-dropbacks-manifest",
                dropbacks,
                "--season",
                season,
            ],
            "site_manifest.json",
        )

        for record in references.values():
            h.verify(root, record)

        state["status"] = (
            "completed_with_review"
            if identity_state["status"] != "completed"
            else "completed"
        )

        state["site_manifest"] = h.record(root, site)
        state["active_stage"] = None

    except (Exception, KeyboardInterrupt) as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        h.save_json(checkpoint, state)
        raise

    h.save_json(checkpoint, state)

    return checkpoint


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--baseline-coverage-manifest", type=Path)
    parser.add_argument(
        "--coverage-manifest",
        type=Path,
        help="Reuse a downloaded coverage snapshot.",
    )
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())

    args = parser.parse_args()

    path = build(
        args.project_root,
        args.season,
        args.baseline_coverage_manifest,
        args.coverage_manifest,
        args.overrides,
    )

    state = h.read_json(path)

    site_manifest = h.locate(
        args.project_root.resolve(),
        state["site_manifest"]["path"],
    )

    print(f"Refresh status: {state['status']}")
    print(f"Open: {site_manifest.parent / 'index.html'}")
    print(f"Manifest: {path}")


if __name__ == "__main__":
    main()
