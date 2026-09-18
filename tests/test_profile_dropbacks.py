import polars as pl
import pytest

from gridiron_value import historical as h
from gridiron_value import player_profile as pp
from gridiron_value import profile_dropbacks as pd


def totals():
    return pl.DataFrame(
        {
            "season": [2026, 2026],
            "season_type": ["REG", "REG"],
            "game_id": ["g1", "g2"],
            "team": ["PHI", "NYJ"],
            "gsis_id": ["p1", "p1"],
            "eligible_dropbacks": [10, 30],
            "total_epa": [5.0, -3.0],
            "epa_per_eligible_dropback": [0.5, -0.1],
        }
    )


def profiles():
    return [
        {
            "positions": ["QB"],
            "games": [
                {**row, "week": index + 1}
                for index, row in enumerate(totals().select(pd.KEYS).to_dicts())
            ],
        }
    ]


def test_weighting_trade_keys_and_missing_records():
    result = profiles()
    audit = pd.attach(result, totals(), 2026)

    assert audit["matched_rows"] == 2
    assert result[0]["dropbacks"]["eligible_dropbacks"] == 40
    assert result[0]["dropbacks"]["epa_per_eligible_dropback"] == (pytest.approx(0.05))

    frame = totals().with_columns(
        pl.when(pl.col("game_id") == "g2")
        .then(pl.lit("PHI"))
        .otherwise(pl.col("team"))
        .alias("team")
    )

    audit = pd.attach(result, frame, 2026)

    assert audit["matched_rows"] == 1
    assert len(audit["unmatched_rows"]) == 1
    assert result[0]["games"][1]["dropbacks"] is None
    assert result[0]["dropbacks"]["matched_games"] == 1
    assert result[0]["dropbacks"]["epa_per_eligible_dropback"] == 0.5

    pd.attach(result, totals().head(0), 2026)

    assert result[0]["dropbacks"]["eligible_dropbacks"] is None
    assert result[0]["dropbacks"]["total_epa"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("eligible_dropbacks", 0),
        ("eligible_dropbacks", 1.5),
        ("total_epa", float("nan")),
        ("total_epa", None),
        ("epa_per_eligible_dropback", 99.0),
        ("team", ""),
        ("season", 2025),
        ("season_type", "POST"),
    ],
)
def test_invalid_dropback_table_rejected(field, value):
    frame = totals().with_columns(pl.lit(value).alias(field))

    with pytest.raises(ValueError):
        pd.attach(profiles(), frame, 2026)


def test_duplicate_dropback_records_rejected():
    frame = pl.concat([totals(), totals().head(1)])

    with pytest.raises(ValueError, match="Duplicate"):
        pd.attach(profiles(), frame, 2026)


@pytest.fixture
def pipeline(tmp_path):
    root = tmp_path

    stats = (
        totals()
        .select(pd.KEYS)
        .with_columns(
            pl.Series("week", [1, 2]),
            pl.lit("QB").alias("position"),
            pl.lit("Example QB").alias("player_display_name"),
            pl.Series("attempts", [10, 30]),
            pl.Series("completions", [8, 12]),
        )
    )

    snaps = stats.rename({"gsis_id": "resolved_gsis_id"}).with_columns(
        pl.lit("resolved").alias("identity_status"),
        pl.lit(True).alias("has_player_stats"),
    )

    for name, frame in (("stats", stats), ("snaps", snaps)):
        frame.write_parquet(root / f"{name}.parquet")

    totals().write_csv(root / "totals.csv")

    plays = []

    for row in totals().to_dicts():
        for play_id in range(row["eligible_dropbacks"]):
            plays.append(
                {
                    "season": row["season"],
                    "season_type": row["season_type"],
                    "game_id": row["game_id"],
                    "play_id": play_id,
                    "posteam": row["team"],
                    "dropback_player_id": row["gsis_id"],
                    "epa": row["epa_per_eligible_dropback"],
                }
            )

    pl.DataFrame(plays).write_parquet(root / "cohort.parquet")

    # This adapter compares the stored PBP reference; it consumes the CSV.
    snapshot = {
        "path": "pbp.parquet",
        "sha256": "source-reference",
    }

    h.save_json(
        root / "coverage.json",
        {"feeds": {"pbp": {"snapshot": snapshot}}},
    )

    coverage = h.record(root, root / "coverage.json")

    h.save_json(
        root / "player_game.json",
        {"source_coverage_manifest": coverage},
    )

    source = h.record(root, root / "player_game.json")

    h.save_json(
        root / "participation.json",
        {
            "season": 2026,
            "source_player_game_manifest": source,
            "files": {"participation": h.record(root, root / "snaps.parquet")},
        },
    )

    h.save_json(
        root / "metrics.json",
        {
            "season": 2026,
            "source_player_game_manifest": source,
            "source_participation_manifest": h.record(
                root, root / "participation.json"
            ),
            "files": {"all": h.record(root, root / "stats.parquet")},
        },
    )

    h.save_json(
        root / "dropbacks.json",
        {
            "season": 2026,
            "season_type": "REG",
            "source_coverage_manifest": coverage,
            "source_snapshot": snapshot,
            "files": {
                "player_game_totals": h.record(root, root / "totals.csv"),
                "cohort": h.record(root, root / "cohort.parquet"),
            },
        },
    )

    return root


def run(root):
    return pp.build(
        root,
        root / "metrics.json",
        root / "participation.json",
        2026,
        dropbacks_path=root / "dropbacks.json",
    )


def test_profile_build_and_leaderboard_compatibility(pipeline):
    from gridiron_value.leaderboards import load_profiles

    output = run(pipeline)
    manifest = h.read_json(output / "profile_manifest.json")

    assert manifest["dropback_integration"]["audit"]["matched_rows"] == 2

    for record in manifest["files"].values():
        h.verify(pipeline, record)

    loaded, _ = load_profiles(
        pipeline,
        output / "profile_manifest.json",
        2026,
        "REG",
    )

    assert loaded[0]["dropbacks"]["epa_per_eligible_dropback"] == (pytest.approx(0.05))

    markup = next(output.glob("player_*.html")).read_text()

    assert "Eligible-dropback production" in markup
    assert "Matched dropback records: 2 / 2" in markup


@pytest.mark.parametrize("failure", ["checksum", "lineage"])
def test_bad_inputs_fail_before_output(pipeline, failure):
    if failure == "checksum":
        with (pipeline / "totals.csv").open("a") as handle:
            handle.write("changed\n")
    else:
        path = pipeline / "dropbacks.json"
        state = h.read_json(path)

        state["source_coverage_manifest"] = {
            "path": "other.json",
            "sha256": "other",
        }

        h.save_json(path, state)

    with pytest.raises(ValueError):
        run(pipeline)

    assert not (pipeline / "reports").exists()
