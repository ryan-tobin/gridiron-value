"""Describe game-resampling variability in adjusted dropback production."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl

from gridiron_value.data import file_sha256, write_json


def resample_rates(
    epa_totals: np.ndarray,
    dropbacks: np.ndarray,
    n_resamples: int = 10_000,
    seed: int = 2026,
) -> np.ndarray:
    """Resample paired game totals and counts, then calculate weighted rates."""
    epa_totals = np.asarray(epa_totals, dtype=float)
    dropbacks = np.asarray(dropbacks, dtype=float)

    if (
        epa_totals.ndim != 1
        or dropbacks.ndim != 1
        or epa_totals.shape != dropbacks.shape
        or epa_totals.size < 2
    ):
        raise ValueError("Provide matching one-dimensional arrays for 2+ games.")

    if (
        not np.isfinite(epa_totals).all()
        or not np.isfinite(dropbacks).all()
        or (dropbacks <= 0).any()
    ):
        raise ValueError("Totals must be finite and dropback counts positive.")

    if n_resamples < 1:
        raise ValueError("n_resamples must be positive.")

    rng = np.random.default_rng(seed)
    draws = rng.integers(
        0,
        epa_totals.size,
        size=(n_resamples, epa_totals.size),
    )

    return epa_totals[draws].sum(axis=1) / dropbacks[draws].sum(axis=1)


def player_variability(
    scored: pl.DataFrame,
    min_dropbacks: int = 100,
    n_resamples: int = 10_000,
    seed: int = 2026,
) -> pl.DataFrame:
    """Summarize observed rates and conditional game-resampling ranges."""
    games = (
        scored.group_by("season", "dropback_player_id", "game_id")
        .agg(
            pl.col("dropback_player_name").drop_nulls().first().alias("player"),
            pl.len().alias("dropbacks"),
            pl.col("epa").sum().alias("raw_epa"),
            pl.col("opponent_adjusted_epa").sum().alias("adjusted_epa"),
        )
        .sort("season", "dropback_player_id", "game_id")
    )

    records = []

    for (season, player_id), player_games in games.group_by(
        "season",
        "dropback_player_id",
        maintain_order=True,
    ):
        counts = player_games["dropbacks"].to_numpy()
        totals = player_games["adjusted_epa"].to_numpy()
        total_dropbacks = int(counts.sum())

        if total_dropbacks < min_dropbacks:
            continue

        names = player_games["player"].drop_nulls().to_list()
        low = None
        high = None

        if player_games.height >= 2:
            # Stable seed per player, independent of report ordering.
            identifier = f"{season}:{player_id}".encode("utf-8")
            player_seed = seed + int.from_bytes(
                hashlib.sha256(identifier).digest()[:8],
                byteorder="big",
            )

            rates = resample_rates(
                totals,
                counts,
                n_resamples=n_resamples,
                seed=player_seed,
            )
            low, high = (float(value) for value in np.quantile(rates, [0.025, 0.975]))

        records.append(
            {
                "season": season,
                "dropback_player_id": player_id,
                "player": names[0] if names else player_id,
                "games_with_dropbacks": player_games.height,
                "dropbacks": total_dropbacks,
                "raw_epa_db": (float(player_games["raw_epa"].sum()) / total_dropbacks),
                "adjusted_epa_db": float(totals.sum()) / total_dropbacks,
                "resampled_rate_p025": low,
                "resampled_rate_p975": high,
                "status": (
                    "conditional_game_resampling"
                    if player_games.height >= 2
                    else "insufficient_games"
                ),
            }
        )

    if not records:
        raise ValueError("No players meet the requested dropback threshold.")

    return pl.DataFrame(records).sort(
        ["adjusted_epa_db", "dropback_player_id"],
        descending=[True, False],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assess conditional game-resampling variability."
    )
    parser.add_argument("--schedule-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--min-dropbacks", type=int, default=100)
    parser.add_argument("--resamples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    if args.min_dropbacks < 1 or args.resamples < 1 or args.seed < 0:
        parser.error("Threshold and resamples must be positive; seed nonnegative.")

    root = args.project_root.resolve()
    source = json.loads(args.schedule_manifest.read_text(encoding="utf-8"))
    scored_info = source["files"]["scored_plays"]
    scored_path = root / scored_info["path"]

    if file_sha256(scored_path) != scored_info["sha256"]:
        raise ValueError("Scored-play checksum does not match.")

    scored = pl.read_parquet(scored_path)

    if scored.height != source["dropbacks"]:
        raise ValueError("Scored-play count does not match the manifest.")

    result = player_variability(
        scored,
        min_dropbacks=args.min_dropbacks,
        n_resamples=args.resamples,
        seed=args.seed,
    )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    metadata_dir = root / "data" / "metadata" / run_id
    report_dir = root / "reports" / "tables" / run_id
    metadata_dir.mkdir(parents=True, exist_ok=False)
    report_dir.mkdir(parents=True, exist_ok=False)

    report_path = report_dir / "player_game_variability.csv"
    manifest_path = metadata_dir / "variability_manifest.json"
    result.write_csv(report_path)

    write_json(
        manifest_path,
        {
            "run_id": run_id,
            "source_schedule_run_id": source["run_id"],
            "source_scored_plays_sha256": scored_info["sha256"],
            "min_dropbacks": args.min_dropbacks,
            "resamples": args.resamples,
            "seed": args.seed,
            "code_sha256": file_sha256(Path(__file__)),
            "packages": {name: version(name) for name in ("numpy", "polars")},
            "report": {
                "path": report_path.relative_to(root).as_posix(),
                "sha256": file_sha256(report_path),
            },
            "interpretation": [
                "Resamples each player's observed games with replacement.",
                "Pairs game EPA totals with their dropback counts.",
                "Schedule adjustments remain fixed.",
                "Ranges describe conditional resampled-rate variability.",
                "Ranges do not quantify uncertainty in realized season totals.",
                "No model-refitting or isolated-player-skill uncertainty.",
                "Individual ranges do not constitute pairwise ranking tests.",
            ],
        },
    )

    displayed = result.select(
        "player",
        "games_with_dropbacks",
        "dropbacks",
        "adjusted_epa_db",
        "resampled_rate_p025",
        "resampled_rate_p975",
    )

    print("Scored-play checksum verified.")
    print("\nObserved rates and central 95% game-resampling ranges:")

    with pl.Config(tbl_rows=15, tbl_width_chars=150, float_precision=4):
        print(displayed.head(15))

    print(f"\nReport: {report_path.relative_to(root)}")
    print(f"Manifest: {manifest_path.relative_to(root)}")


if __name__ == "__main__":
    main()
