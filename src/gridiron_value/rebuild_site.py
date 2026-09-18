"""Rebuild offline profiles and leaderboards from three explicit manifests."""

import argparse
import html
import os
from datetime import UTC, datetime
from pathlib import Path

from gridiron_value import dropback_leaderboard as db
from gridiron_value import historical as h
from gridiron_value import leaderboards as lb
from gridiron_value import player_profile as pp


def build(
    root,
    metrics,
    participation,
    dropbacks,
    season,
    min_dropbacks=100,
    min_games=3,
):
    root = Path(root).resolve()

    if any(
        type(value) is not int or value < 1
        for value in (min_dropbacks, min_games)
    ):
        raise ValueError("Display thresholds must be positive integers.")

    paths = {
        name: h.locate(root, path)
        for name, path in (
            ("position_metrics", metrics),
            ("participation", participation),
            ("current_dropbacks", dropbacks),
        )
    }

    inputs = {
        name: h.record(root, path)
        for name, path in paths.items()
    }

    print("Building player profiles...")

    profiles = pp.build(
        root,
        paths["position_metrics"],
        paths["participation"],
        season,
        dropbacks_path=paths["current_dropbacks"],
    )

    profile_manifest = profiles / "profile_manifest.json"

    print("Building eligible-dropback leaderboard...")

    dropback_output = db.build(
        root,
        profile_manifest,
        season,
        min_dropbacks,
        min_games,
    )

    print("Building position leaderboards...")

    position_output = lb.build(
        root,
        profile_manifest,
        season,
        "REG",
    )

    manifests = {
        "profiles": h.record(root, profile_manifest),
        "dropback_leaderboard": h.record(
            root,
            dropback_output / "dropback_leaderboard_manifest.json",
        ),
        "position_leaderboards": h.record(
            root,
            position_output / "leaderboard_manifest.json",
        ),
    }

    for record in manifests.values():
        h.verify_manifest(root, record)

    for record in inputs.values():
        h.verify(root, record)

    run = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"site_build_{run}"

    destinations = {
        "Player directory": position_output / "index.html",
        "Position leaderboards": position_output / "leaderboards.html",
        "Eligible-dropback leaderboard": dropback_output / "index.html",
    }

    pages = {
        name: h.record(root, path)
        for name, path in destinations.items()
    }

    body = f"<h1>Gridiron Value · {season} REG</h1>"
    body += "<p>Observed production from the supplied snapshots.</p><ul>"

    for title, path in destinations.items():
        href = html.escape(
            Path(os.path.relpath(path, output)).as_posix(),
            quote=True,
        )
        body += f'<li><a href="{href}">{html.escape(title)}</a></li>'

    body += "</ul>"
    body += (
        f"<p>Dropback ranking display thresholds: {min_dropbacks} "
        f"eligible dropbacks and {min_games} matched games, with matches "
        "for every observed profile game. These are provisional "
        "display rules.</p>"
    )

    output.mkdir(parents=True, exist_ok=False)

    index = output / "index.html"
    index.write_text(
        pp.page("Gridiron Value", body),
        encoding="utf-8",
    )

    h.save_json(
        output / "site_manifest.json",
        {
            "schema_version": 1,
            "run_id": run,
            "season": season,
            "season_type": "REG",
            "status": "complete",
            "inputs": inputs,
            "stage_manifests": manifests,
            "linked_pages": pages,
            "display_thresholds": {
                "min_dropbacks": min_dropbacks,
                "min_games": min_games,
            },
            "files": {
                "index.html": h.record(root, index),
            },
            "code_sha256": h.sha256(Path(__file__)),
        },
    )

    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--position-metrics-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--participation-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--current-dropbacks-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--min-dropbacks", type=int, default=100)
    parser.add_argument("--min-games", type=int, default=3)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())

    args = parser.parse_args()

    output = build(
        args.project_root,
        args.position_metrics_manifest,
        args.participation_manifest,
        args.current_dropbacks_manifest,
        args.season,
        args.min_dropbacks,
        args.min_games,
    )

    print(f"Open: {output / 'index.html'}")
    print(f"Manifest: {output / 'site_manifest.json'}")


if __name__ == "__main__":
    main()