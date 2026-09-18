from copy import deepcopy

import polars as pl
import pytest
import test_profile_dropbacks as fixtures

from gridiron_value import dropback_leaderboard as db
from gridiron_value import historical as h
from gridiron_value import profile_dropbacks as pd

pipeline = fixtures.pipeline


def sample(identity, totals):
    games, records = [], []

    for week, values in enumerate(totals, 1):
        game = {
            "season": 2026,
            "season_type": "REG",
            "game_id": f"g{week}",
            "team": "PHI",
            "gsis_id": identity,
            "week": week,
        }

        games.append(game)

        if values is not None:
            count, epa = values

            records.append(
                {
                    **{key: game[key] for key in pd.KEYS},
                    "eligible_dropbacks": count,
                    "total_epa": float(epa),
                    "epa_per_eligible_dropback": epa / count,
                }
            )

    profile = {
        "season": 2026,
        "gsis_id": identity,
        "name": identity,
        "teams": ["PHI"],
        "positions": ["QB"],
        "games": games,
    }

    pd.attach([profile], pl.DataFrame(records), 2026)

    return profile


def test_qualification_ties_and_partial_coverage():
    profiles = [
        sample("a", [(50, 5), (50, 5)]),
        sample("b", [(50, 5), (50, 5)]),
        sample("c", [(50, 0), (50, 0)]),
        sample("small", [(10, 9), (10, 9)]),
        sample("partial", [(100, 99), None]),
    ]

    rows = {row["gsis_id"]: row for row in db.compare(profiles, 100, 2)}

    assert [rows[key]["rank"] for key in ("a", "b", "c")] == [1, 1, 3]

    assert rows["small"]["rank"] is None
    assert "below minimum dropbacks" in rows["small"]["status"]

    assert rows["partial"]["rank"] is None
    assert "missing game matches" in rows["partial"]["status"]


def test_summary_tampering_is_rejected():
    profile = sample("a", [(10, 5), (30, -3)])

    assert pd.validate([profile], 2026)

    profile["dropbacks"]["epa_per_eligible_dropback"] = 999.0

    with pytest.raises(ValueError, match="summary"):
        db.compare([profile], 1, 1)


def test_legacy_and_incomplete_extensions():
    profile = sample("a", [(10, 5)])
    legacy = deepcopy(profile)

    del legacy["dropbacks"]
    del legacy["games"][0]["dropbacks"]

    assert pd.validate([legacy], 2026) is False

    with pytest.raises(ValueError, match="integration"):
        db.compare([legacy], 1, 1)

    del profile["games"][0]["dropbacks"]

    with pytest.raises(ValueError, match="extension"):
        pd.validate([profile], 2026)


@pytest.mark.parametrize("minimum", [0, -1, 1.5, True])
def test_invalid_thresholds(minimum):
    with pytest.raises(ValueError, match="positive integers"):
        db.compare(
            [sample("a", [(10, 5)])],
            minimum,
            1,
        )


def test_build_empty_qualified_table_and_verified_links(pipeline):
    profiles = fixtures.run(pipeline)

    output = db.build(
        pipeline,
        profiles / "profile_manifest.json",
        2026,
    )

    state = h.read_json(output / "dropback_leaderboard_manifest.json")

    assert state["qualified_count"] == 0
    assert state["observed_count"] == 1
    assert pl.read_csv(output / "qualified.csv").height == 0

    observed = pl.read_csv(output / "observed.csv")

    assert observed["epa_per_eligible_dropback"].item() == (pytest.approx(0.05))

    for record in state["files"].values():
        h.verify(pipeline, record)

    markup = (output / "index.html").read_text()

    assert "No players meet these display thresholds" in markup

    for record in state["linked_profile_pages"].values():
        path = h.verify(pipeline, record)
        assert path.name in markup


def test_source_changes_fail_before_leaderboard_output(pipeline):
    profiles = fixtures.run(pipeline)

    with (pipeline / "totals.csv").open("a") as handle:
        handle.write("changed\n")

    with pytest.raises(ValueError, match="Checksum"):
        db.build(
            pipeline,
            profiles / "profile_manifest.json",
            2026,
        )

    assert not list((pipeline / "reports/tables").glob("dropback_leaderboard_*"))


def test_consistent_cached_edits_still_disagree_with_source(pipeline):
    output = fixtures.run(pipeline)
    path = output / "profiles.json"
    payload = h.read_json(path)
    profile = payload["profiles"][0]

    for game in profile["games"]:
        values = game["dropbacks"]
        values["total_epa"] *= 2
        values["epa_per_eligible_dropback"] *= 2

    profile["dropbacks"]["total_epa"] *= 2
    profile["dropbacks"]["epa_per_eligible_dropback"] *= 2

    h.save_json(path, payload)

    manifest_path = output / "profile_manifest.json"
    state = h.read_json(manifest_path)
    state["files"]["profiles.json"] = h.record(pipeline, path)

    h.save_json(manifest_path, state)

    with pytest.raises(ValueError, match="source table"):
        db.build(pipeline, manifest_path, 2026)


def test_render_escapes_names():
    rows = db.compare(
        [sample("<script>", [(10, 5)])],
        1,
        1,
    )

    markup = db.render(
        rows,
        {"<script>": "player.html"},
    )

    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup
