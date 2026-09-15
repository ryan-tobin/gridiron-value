import argparse
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np
import polars as pl

from gridiron_value import historical as h
from gridiron_value.adjustment import MODEL_NAMES, make_team_model, summarize_errors


def evaluate_weeks(frame, first_test_week=5):
    """Fit on weeks strictly before each test week; never fit on test outcomes."""
    required = ["season", "week", "game_date", "game_id", "play_id",
                "posteam", "defteam", "epa"]
    if set(required) - set(frame.columns):
        raise ValueError("Missing chronological input columns.")
    frame = frame.select(required)
    if frame.is_empty() or any(frame.null_count().row(0)):
        raise ValueError("Empty input or null chronological fields.")
    if frame["season"].n_unique() != 1:
        raise ValueError("Evaluate one season at a time.")
    if first_test_week < 2:
        raise ValueError("First test week must be at least 2.")
    if not frame["week"].dtype.is_integer() or frame["week"].min() < 1:
        raise ValueError("Weeks must be positive integers.")
    if frame.select("game_id", "play_id").unique().height != frame.height:
        raise ValueError("Duplicate play keys.")
    if not np.isfinite(frame["epa"].to_numpy()).all():
        raise ValueError("EPA must be finite.")
    if any(not str(t).strip() for t in frame.select("posteam", "defteam").to_numpy().ravel()):
        raise ValueError("Blank team label.")
    # Parse dates explicitly rather than relying on lexical ordering.
    frame = frame.with_columns(pl.col("game_date").cast(pl.String).str.to_date())
    games = frame.group_by("game_id").agg(
        pl.col("week").n_unique().alias("weeks"),
        pl.col("game_date").n_unique().alias("dates"),
    )
    if games.filter((pl.col("weeks") != 1) | (pl.col("dates") != 1)).height:
        raise ValueError("A game spans multiple weeks or dates.")
    frame = frame.sort("week", "game_id", "play_id")
    predictions, audit = [], []
    for week in sorted(frame["week"].unique().to_list()):
        if week < first_test_week:
            continue
        train = frame.filter(pl.col("week") < week)
        test = frame.filter(pl.col("week") == week)
        if train.is_empty():
            raise ValueError("No earlier training plays.")
        if train["game_date"].max() >= test["game_date"].min():
            raise ValueError("Training dates overlap or follow the test window.")
        x_train = train.select("posteam", "defteam").to_numpy()
        x_test = test.select("posteam", "defteam").to_numpy()
        target = train["epa"].to_numpy()
        columns = [pl.Series("pred_league_mean", np.full(test.height, target.mean()))]
        for name, defense in (("offense_only", False), ("offense_defense", True)):
            model = make_team_model(100.0, defense, defense_alpha=1000.0)
            model.fit(x_train, target)
            values = model.predict(x_test)
            if not np.isfinite(values).all():
                raise ValueError("Nonfinite predictions.")
            columns.append(pl.Series(f"pred_{name}", values))
        predictions.append(test.with_columns(*columns))
        known_offenses = set(train["posteam"])
        known_defenses = set(train["defteam"])
        audit.append({
            "season": int(frame["season"][0]), "test_week": week,
            "train_dropbacks": train.height, "test_dropbacks": test.height,
            "train_games": train["game_id"].n_unique(),
            "test_games": test["game_id"].n_unique(),
            "train_last_date": str(train["game_date"].max()),
            "test_first_date": str(test["game_date"].min()),
            "unseen_offense_plays": sum(t not in known_offenses for t in test["posteam"]),
            "unseen_defense_plays": sum(t not in known_defenses for t in test["defteam"]),
        })
    if not predictions:
        raise ValueError("No test weeks at the selected cutoff.")
    return pl.concat(predictions), pl.DataFrame(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-manifest", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--seasons", nargs="+", type=int)
    parser.add_argument("--first-test-week", type=int, default=5)
    args = parser.parse_args()
    root = args.project_root.resolve()
    path = h.locate(root, args.historical_manifest)
    batch = h.read_json(path)
    if batch.get("status") != "completed":
        raise ValueError("Historical batch must be completed.")
    entries = batch["completed"]
    available = [entry["season"] for entry in entries]
    if len(set(available)) != len(available):
        raise ValueError("Duplicate historical seasons.")
    chosen = set(args.seasons or available)
    if chosen - set(available):
        raise ValueError("Requested seasons are absent from the historical batch.")
    all_predictions, audits, scores, sources = [], [], [], []
    for entry in sorted(entries, key=lambda item: item["season"]):
        season = entry["season"]
        if season not in chosen:
            continue
        cohort_info = entry["stages"]["cohort"]
        cohort_manifest = h.read_json(h.verify(root, cohort_info))
        cohort_file = cohort_manifest["files"]["cohort"]
        frame = pl.read_parquet(h.verify(root, cohort_file))
        if frame["season"].unique().to_list() != [season]:
            raise ValueError("Cohort season differs from batch entry.")
        pred, audit = evaluate_weeks(frame, args.first_test_week)
        summary = summarize_errors(pred)
        scores.append(summary.with_columns(pl.lit(season).alias("season"),
                                          pl.lit(pred.height).alias("dropbacks")))
        lookup = dict(zip(summary["model"], summary["mse"]))
        gain = lookup["offense_only"] - lookup["offense_defense"]
        print(f"{season}: {pred.height:,} test dropbacks; defense MSE gain {gain:+.6f}", flush=True)
        all_predictions.append(pred)
        audits.append(audit)
        sources.append({"season": season, "manifest": cohort_info, "cohort": cohort_file})
    predictions = pl.concat(all_predictions)
    game_errors = predictions.group_by("season", "week", "game_id").agg(
        pl.len().alias("dropbacks"),
        *[((pl.col("epa") - pl.col(f"pred_{name}")) ** 2).mean().alias(f"{name}_mse")
          for name in MODEL_NAMES],
    ).sort("season", "week", "game_id")
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = root / "reports" / "tables" / f"chronological_{run}"
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    tables = {"season_scores": pl.concat(scores), "pooled_scores": summarize_errors(predictions),
              "window_audit": pl.concat(audits), "game_errors": game_errors}
    for name, table in tables.items():
        target = output / f"{name}.csv"
        table.write_csv(target)
        files[name] = h.record(root, target)
    target = output / "predictions.parquet"
    predictions.write_parquet(target, compression="zstd")
    files["predictions"] = h.record(root, target)
    manifest = output / "chronological_manifest.json"
    h.save_json(manifest, {
        "run_id": run, "source_historical_manifest": h.record(root, path),
        "sources": sources, "settings": {"offense_alpha": 100, "defense_alpha": 1000,
        "first_test_week": args.first_test_week, "seasons": sorted(chosen)},
        "evaluation": "Expanding earlier weeks within each season; model reset each season",
        "error_weighting": "Equal weight per eligible test dropback",
        "environment": {"packages": {name: version(name) for name in ("numpy", "polars", "scikit-learn")}},
        "code": {name: h.record(root, Path(__file__).parent / name)
                 for name in ("chronological.py", "adjustment.py", "historical.py")},
        "files": files,
        "limitations": [
            "Historical backtest on previously inspected seasons, not independent prospective validation.",
            "Revised upstream EPA is a fixed target; its model training and historical availability are not reconstructed.",
            "Earlier-week cutoff verified by dates; no claim of live feed availability or correction latency.",
            "Fixed penalties inherited from retrospective research; no tuning in this run.",
            "Weeks before the cutoff are training only; no preseason prior or cross-season carryover.",
            "Forecast error comparisons do not validate causal opponent corrections or individual talent attribution.",
        ],
    })
    print(f"Manifest: {manifest.relative_to(root)}")


if __name__ == "__main__":
    main()