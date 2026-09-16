import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import polars as pl

from gridiron_value import historical as h


def safe_ratio(numerator, denominator):
    """Return null when the denominator is zero or missing."""
    return pl.when(denominator.is_not_null() & (denominator > 0)).then(numerator / denominator).otherwise(None)


def derive_metrics(frame):
    required = {"season", "week", "game_id", "gsis_id", "position"}
    if not required.issubset(frame.columns):
        raise ValueError(f"Player-game table missing: {sorted(required - set(frame.columns))}")
    if frame.select("season", "week", "game_id", "gsis_id").unique().height != frame.height:
        raise ValueError("Player-game table contains duplicate player-game rows.")
    def col(name, dtype=pl.Float64):
        return pl.col(name).cast(dtype) if name in frame.columns else pl.lit(None, dtype=dtype)
    attempts = col("attempts")
    sacks = col("sacks_suffered")
    carries = col("carries")
    targets = col("targets")
    receptions = col("receptions")
    snap_total = col("offense_snaps") + col("defense_snaps") + col("st_snaps")
    role = (
        pl.when(pl.col("position").is_in(["QB"])).then(pl.lit("QB"))
        .when(pl.col("position").is_in(["RB", "FB"])).then(pl.lit("RB"))
        .when(pl.col("position").is_in(["WR"])).then(pl.lit("WR"))
        .when(pl.col("position").is_in(["TE"])).then(pl.lit("TE"))
        .otherwise(pl.lit("OTHER"))
        .alias("metric_role")
    )
    out = frame.with_columns(
        role,
        snap_total.alias("total_snaps"),
        (attempts + sacks).alias("dropbacks_proxy"),
        safe_ratio(col("passing_epa"), attempts).alias("pass_epa_per_attempt"),
        safe_ratio(col("passing_yards"), attempts).alias("pass_yards_per_attempt"),
        safe_ratio(col("completions"), attempts).alias("completion_rate"),
        safe_ratio(col("passing_tds"), attempts).alias("passing_td_rate"),
        safe_ratio(col("passing_interceptions"), attempts).alias("interception_rate"),
        safe_ratio(sacks, attempts + sacks).alias("sack_rate_proxy"),
        safe_ratio(col("rushing_epa"), carries).alias("rush_epa_per_carry"),
        safe_ratio(col("rushing_yards"), carries).alias("rush_yards_per_carry"),
        safe_ratio(col("rushing_20"), carries).alias("explosive_run_rate"),
        safe_ratio(col("receiving_epa"), targets).alias("receiving_epa_per_target"),
        safe_ratio(col("receiving_yards"), targets).alias("receiving_yards_per_target"),
        safe_ratio(receptions, targets).alias("catch_rate"),
        safe_ratio(col("receiving_yards_after_catch"), receptions).alias("yac_per_reception"),
        safe_ratio(col("receiving_20"), receptions).alias("explosive_reception_rate"),
    )
    # NGS and share fields are already rates or context-adjusted fields; expose them
    # under stable platform names without reinterpreting their definitions.
    aliases = {
        "target_share": "target_share",
        "air_yards_share": "air_yards_share",
        "ngs_passing_completion_percentage_above_expectation": "cpoe_ngs",
        "ngs_rushing_rush_yards_over_expected_per_att": "ryoe_per_carry_ngs",
        "ngs_receiving_avg_yac_above_expectation": "yac_above_expectation_ngs",
        "ngs_receiving_avg_separation": "avg_separation_ngs",
    }
    additions = [pl.col(source).alias(target) for source, target in aliases.items() if source in out.columns]
    return out.with_columns(*additions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player-game-manifest", type=Path, required=True)
    parser.add_argument("--participation-manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.project_root.resolve()
    player_game_path = h.locate(root, args.player_game_manifest)
    participation_path = h.locate(root, args.participation_manifest)
    player_game = h.read_json(player_game_path)
    participation = h.read_json(participation_path)
    if participation["source_player_game_manifest"] != h.record(root, player_game_path):
        raise ValueError("Player-game and participation runs use different player-game inputs.")
    source = h.verify(root, player_game["files"]["player_game"])
    frame = pl.read_parquet(source)
    metrics = derive_metrics(frame)
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"position_metrics_{run}"
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for role in ("QB", "RB", "WR", "TE"):
        table = metrics.filter(pl.col("metric_role") == role)
        path = output / f"{role.lower()}_metrics.parquet"
        table.write_parquet(path, compression="zstd")
        files[role.lower()] = h.record(root, path)
    all_path = output / "position_metrics.parquet"
    metrics.write_parquet(all_path, compression="zstd")
    files["all"] = h.record(root, all_path)
    report = output / "report.md"
    report.write_text(
        "# Position-specific metrics\n\n"
        f"Rows: {metrics.height:,}. Roles: "
        + ", ".join(f"{role}={metrics.filter(pl.col('metric_role') == role).height:,}" for role in ("QB", "RB", "WR", "TE", "OTHER"))
        + "\n\n"
        "Rates are calculated from the available player-game statistics. A null rate means its denominator was zero or unavailable. "
        "`dropbacks_proxy` is attempts plus sacks and is not the finalized Pass+ eligible-dropback definition because scrambles are not separately recovered here. "
        "EPA-based rates retain shared contributions and are not isolated individual talent measures. "
        "NGS fields preserve the source metric definitions.\n",
        encoding="utf-8",
    )
    files["report"] = h.record(root, report)
    manifest = output / "position_metrics_manifest.json"
    h.save_json(manifest, {
        "run_id": run, "season": int(metrics["season"][0]),
        "source_player_game_manifest": h.record(root, player_game_path),
        "source_participation_manifest": h.record(root, participation_path),
        "files": files, "code": h.record(root, Path(__file__).resolve()),
        "packages": {"polars": version("polars")},
        "metric_definitions": {
            "sack_rate_proxy": "sacks_suffered / (attempts + sacks_suffered)",
            "explosive_run_rate": "rushing_20 / carries",
            "explosive_reception_rate": "receiving_20 / receptions",
            "dropbacks_proxy": "attempts + sacks_suffered; not finalized Pass+ eligibility",
        },
        "limitations": [
            "No current-season Pass+ score is produced by this module.",
            "EPA-based metrics are play outcomes with shared player, blocking and scheme contributions.",
            "Minimum workload qualification and peer percentiles are deferred until the metric contract is finalized.",
        ],
    })
    print(f"Metric rows: {metrics.height:,}")
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()