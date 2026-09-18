import math

import polars as pl
import pytest
import test_profile_dropbacks as fixtures

from gridiron_value import historical as h
from gridiron_value import player_profile as pp
from gridiron_value import profile_dropbacks as pd
from gridiron_value import profile_situations as ps

pipeline = fixtures.pipeline


def inputs(root):
    output = fixtures.run(root)
    profiles = h.read_json(output / "profiles.json")["profiles"]
    cohort = pl.read_parquet(root / "cohort.parquet")

    return profiles, cohort


def test_dimensions_conserve_matched_production_and_render(pipeline):
    profiles, cohort = inputs(pipeline)

    cohort = cohort.with_columns(
        pl.when(pl.col("play_id") < 5).then(1).otherwise(3).alias("down")
    )

    audit = ps.attach(profiles, cohort, 2026)

    assert audit["matched_plays"] == 40

    for rows in profiles[0]["situations"].values():
        assert sum(row["eligible_dropbacks"] for row in rows) == 40

        assert math.fsum(row["total_epa"] for row in rows) == pytest.approx(2.0)

        assert sum(
            row["eligible_dropbacks"] * row["positive_epa_rate"] for row in rows
        ) == pytest.approx(10)

    down = {row["bucket"]: row for row in profiles[0]["situations"]["down"]}

    assert down["1"]["epa_per_eligible_dropback"] == pytest.approx(0.2)
    assert down["3"]["epa_per_eligible_dropback"] == pytest.approx(0.0)

    assert profiles[0]["situations"]["distance"][0]["bucket"] == "unknown"

    markup = pp.render_profile(profiles[0])

    assert "Situational splits" in markup
    assert "25.0%" in markup
    assert "Unknown" in markup


def test_only_matched_profile_games_are_used(pipeline):
    profiles, cohort = inputs(pipeline)

    pd.attach(
        profiles,
        fixtures.totals().head(1),
        2026,
    )

    audit = ps.attach(profiles, cohort, 2026)

    assert audit["matched_plays"] == 10
    assert audit["plays_outside_selected_matches"] == 30

    for rows in profiles[0]["situations"].values():
        assert sum(row["eligible_dropbacks"] for row in rows) == 10

        assert math.fsum(row["total_epa"] for row in rows) == pytest.approx(5.0)


@pytest.mark.parametrize(
    "change",
    ["duplicate", "missing", "wrong_team", "epa"],
)
def test_invalid_or_mismatched_plays_fail(pipeline, change):
    profiles, cohort = inputs(pipeline)

    if change == "duplicate":
        cohort = pl.concat([cohort, cohort.head(1)])
    elif change == "missing":
        cohort = cohort.slice(1)
    elif change == "wrong_team":
        cohort = cohort.with_columns(pl.lit("OTHER").alias("posteam"))
    else:
        cohort = cohort.with_columns((pl.col("epa") + 1).alias("epa"))

    with pytest.raises(ValueError):
        ps.attach(profiles, cohort, 2026)


def test_tampered_cohort_fails_before_profile_output(pipeline):
    with (pipeline / "cohort.parquet").open("ab") as handle:
        handle.write(b"changed")

    with pytest.raises(ValueError, match="Checksum"):
        fixtures.run(pipeline)

    assert not (pipeline / "reports").exists()
