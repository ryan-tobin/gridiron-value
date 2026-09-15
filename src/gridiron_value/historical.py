"""Run the existing Pass+ CLIs against explicitly selected raw snapshots."""

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

SETTINGS = {
    "offense_alpha": 100.0,
    "defense_alpha": 1000.0,
    "folds": 5,
    "min_dropbacks": 100,
    "resamples": 10_000,
    "seed": 2026,
}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def locate(root: Path, path: str | Path) -> Path:
    result = (root / path).resolve()
    result.relative_to(root)
    return result


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def record(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)}


def verify(root: Path, info: dict) -> Path:
    path = locate(root, info["path"])
    if sha256(path) != info["sha256"]:
        raise ValueError(f"Checksum mismatch: {path}")
    return path


def verify_manifest(root: Path, info: dict) -> dict:
    manifest = read_json(verify(root, info))
    for artifact in manifest.get("files", {}).values():
        verify(root, artifact)
    if "report" in manifest:
        verify(root, manifest["report"])
    return manifest


def environment() -> dict:
    return {
        "python": platform.python_version(),
        "packages": {
            name: version(name)
            for name in ("nflreadpy", "numpy", "polars", "scipy", "scikit-learn")
        },
        "code_sha256": {
            path.name: sha256(path)
            for path in sorted(Path(__file__).parent.glob("*.py"))
        },
    }


def select_inputs(root: Path, paths: list[Path]) -> list[dict]:
    inputs = []
    seasons = set()
    for path in paths:
        path = locate(root, path)
        manifest = read_json(path)
        season = manifest["season_requested"]
        if season in seasons:
            raise ValueError(f"Supply exactly one raw snapshot for {season}.")
        if not 1999 <= season < datetime.now(timezone.utc).year:
            raise ValueError(f"Unsupported season: {season}")
        verify(root, manifest["files"]["raw"])
        inputs.append({"season": season, "manifest": record(root, path)})
        seasons.add(season)
    return sorted(inputs, key=lambda item: item["season"])


def run_stage(root: Path, stage: str, arguments: list[str], log: Path) -> dict:
    print(f"  {stage}...", flush=True)
    command = [sys.executable, "-m", f"gridiron_value.{stage}", *arguments]
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            command,
            cwd=root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    output = log.read_text(encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{stage} failed; see {log}\n{output[-4000:]}")
    paths = [
        line.removeprefix("Manifest: ")
        for line in output.splitlines()
        if line.startswith("Manifest: ")
    ]
    if len(paths) != 1:
        raise ValueError(f"Expected one printed manifest from {stage}: {log}")
    info = record(root, locate(root, paths[0]))
    verify_manifest(root, info)
    return info


def run_season(root: Path, source: dict, output_dir: Path) -> dict:
    season = source["season"]
    logs = output_dir / str(season)
    logs.mkdir(exist_ok=True)
    stages = {}
    arguments = ["--manifest", source["manifest"]["path"]]
    for stage in ("cohort", "schedule", "variability", "reporting"):
        if stage == "schedule":
            arguments = ["--cohort-manifest", stages["cohort"]["path"]]
        elif stage in ("variability", "reporting"):
            arguments = ["--schedule-manifest", stages["schedule"]["path"]]
            if stage == "variability":
                arguments += [
                    "--resamples",
                    str(SETTINGS["resamples"]),
                    "--seed",
                    str(SETTINGS["seed"]),
                ]
            else:
                arguments += ["--variability-manifest", stages["variability"]["path"]]
        arguments += [
            "--project-root",
            str(root),
            "--min-dropbacks",
            str(SETTINGS["min_dropbacks"]),
        ]
        stages[stage] = run_stage(root, stage, arguments, logs / f"{stage}.log")
        if stage == "schedule":
            schedule = read_json(root / stages[stage]["path"])
            for name in ("offense_alpha", "defense_alpha", "folds"):
                if schedule[name] != SETTINGS[name]:
                    raise ValueError(f"Schedule setting changed: {name}")
    return {"season": season, "stages": stages}


def collect(root: Path, completed: list[dict]) -> tuple[list[dict], list[dict]]:
    players = []
    references = []
    keys = set()
    seasons = set()
    columns = None
    for entry in completed:
        season = entry["season"]
        if season in seasons:
            raise ValueError(f"Duplicate completed season: {season}")
        seasons.add(season)
        manifests = {
            stage: verify_manifest(root, info)
            for stage, info in entry["stages"].items()
        }
        report = manifests["reporting"]
        reference = report["reference"]
        if reference["season"] != season:
            raise ValueError("Report season does not match the selected snapshot.")
        with (root / report["report"]["path"]).open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if columns is not None and reader.fieldnames != columns:
                raise ValueError("Historical report schemas differ.")
            columns = reader.fieldnames
            rows = list(reader)
        if len(rows) != reference["participants"]:
            raise ValueError("Participant count does not match the reference.")
        count = sum(int(row["dropbacks"]) for row in rows)
        if (
            count != reference["dropbacks"]
            or count != manifests["schedule"]["dropbacks"]
        ):
            raise ValueError("Dropback counts do not reconcile.")
        source = entry["stages"]["reporting"]["path"]
        for row in rows:
            key = (int(row["season"]), row["dropback_player_id"])
            if key[0] != season or not key[1] or key in keys:
                raise ValueError(f"Invalid or duplicate player-season: {key}")
            keys.add(key)
            row.update(
                {
                    "reference_mean_adjusted_epa_db": reference[
                        "league_mean_adjusted_epa_per_dropback"
                    ],
                    "reference_sd_player_rates": reference[
                        "weighted_sd_of_player_rates"
                    ],
                    "source_pass_plus_manifest": source,
                }
            )
            players.append(row)
        references.append({**reference, "source_pass_plus_manifest": source})
    players.sort(
        key=lambda row: (
            int(row["season"]),
            -float(row["pass_plus"]),
            row["dropback_player_id"],
        )
    )
    references.sort(key=lambda row: row["season"])
    return players, references


def save_csv(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def execute(root: Path, state: dict, checkpoint: Path) -> None:
    output_dir = locate(root, state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for source in state["inputs"]:
            manifest = read_json(verify(root, source["manifest"]))
            if manifest["season_requested"] != source["season"]:
                raise ValueError("Checkpoint and raw manifest seasons differ.")
            verify(root, manifest["files"]["raw"])
        if state["completed"]:
            collect(root, state["completed"])
        done = {entry["season"] for entry in state["completed"]}
        if not done.issubset({source["season"] for source in state["inputs"]}):
            raise ValueError("Checkpoint contains an unrequested season.")
        for source in state["inputs"]:
            season = source["season"]
            if season in done:
                print(f"{season}: verified completed checkpoint; skipping.")
                continue
            print(f"\nSeason {season}...", flush=True)
            entry = run_season(root, source, output_dir)
            collect(root, [entry])
            state["completed"].append(entry)
            state["status"] = "running"
            state.pop("error", None)
            save_json(checkpoint, state)
            print(f"{season}: checkpoint saved.", flush=True)
        players, references = collect(root, state["completed"])
        state["files"] = {}
        for name, rows in (
            ("historical_pass_plus", players),
            ("season_references", references),
        ):
            path = output_dir / f"{name}.csv"
            save_csv(path, rows)
            state["files"][name] = record(root, path)
            print(f"Report: {path.relative_to(root)}")
        state["status"] = "completed"
        state.pop("error", None)
        save_json(checkpoint, state)
    except (Exception, KeyboardInterrupt) as error:
        state["status"] = "failed"
        state["error"] = f"{type(error).__name__}: {error}"
        save_json(checkpoint, state)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--raw-manifests", nargs="+", type=Path)
    mode.add_argument("--resume", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not (root / "pyproject.toml").is_file():
        parser.error("Run from the project root or supply --project-root.")
    if args.resume:
        checkpoint = locate(root, args.resume)
        state = read_json(checkpoint)
        if state["settings"] != SETTINGS or state["environment"] != environment():
            raise ValueError(
                "Code, settings, or environment changed; start a new batch."
            )
    else:
        inputs = select_inputs(root, args.raw_manifests)
        batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / "data" / "metadata" / batch
        directory.mkdir(parents=True, exist_ok=False)
        checkpoint = directory / "historical_manifest.json"
        state = {
            "batch_id": batch,
            "settings": SETTINGS,
            "environment": environment(),
            "inputs": inputs,
            "completed": [],
            "status": "running",
            "output_dir": f"reports/tables/{batch}",
        }
        save_json(checkpoint, state)
    print(f"Checkpoint: {checkpoint.relative_to(root)}", flush=True)
    execute(root, state, checkpoint)


if __name__ == "__main__":
    main()