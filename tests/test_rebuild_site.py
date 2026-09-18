import pytest
import test_profile_dropbacks as fixtures

from gridiron_value import historical as h
from gridiron_value import rebuild_site as site

pipeline = fixtures.pipeline


def test_build_links_verified_outputs_from_one_profile_run(pipeline):
    output = site.build(
        pipeline,
        pipeline / "metrics.json",
        pipeline / "participation.json",
        pipeline / "dropbacks.json",
        2026,
    )

    state = h.read_json(output / "site_manifest.json")

    assert state["status"] == "complete"
    assert state["coverage"]["profile_games"] == 2
    assert state["coverage"]["matched_dropbacks"] == 40
    assert state["coverage"]["pbp_retrieved_at"] is None
    assert "Snapshot coverage" in (output / "index.html").read_text()

    profile = state["stage_manifests"]["profiles"]

    for key in (
        "dropback_leaderboard",
        "position_leaderboards",
        "counting_leaderboards",
    ):
        stage = h.verify_manifest(
            pipeline,
            state["stage_manifests"][key],
        )
        assert stage["source_profile_manifest"] == profile

    for record in state["linked_pages"].values():
        h.verify(pipeline, record)

    assert (output / "index.html").exists()
    assert "Counting-stat leaderboards" in (output / "index.html").read_text()


def test_invalid_input_does_not_publish_landing_page(pipeline):
    with (pipeline / "totals.csv").open("a") as handle:
        handle.write("changed\n")

    with pytest.raises(ValueError, match="Checksum"):
        site.build(
            pipeline,
            pipeline / "metrics.json",
            pipeline / "participation.json",
            pipeline / "dropbacks.json",
            2026,
        )

    assert not list((pipeline / "reports/tables").glob("site_build_*"))


def test_coverage_counts_unique_games_and_keeps_week_gaps():
    profiles = [
        {
            "games": [
                {"game_id": "g1", "week": 1},
                {"game_id": "g3", "week": 3},
            ],
            "dropbacks": {
                "matched_games": 2,
                "eligible_dropbacks": 40,
            },
        },
        {
            "games": [
                {"game_id": "g1", "week": 1},
            ],
            "dropbacks": {
                "matched_games": 0,
                "eligible_dropbacks": None,
            },
        },
    ]

    source = {
        "observed_weeks": [1, 2, 3],
        "games_observed": 4,
        "rows": 50,
        "source_retrieved_at": "2026-09-16T00:00:00Z",
    }

    result = site.summarize_coverage(profiles, source)

    assert result["profile_weeks"] == [1, 3]
    assert result["profile_games"] == 2
    assert result["player_profiles"] == 2
    assert result["matched_dropback_players"] == 1
    assert result["matched_dropbacks"] == 40
    assert result["source_dropbacks"] == 50
    assert result["source_dropback_games"] == 4
    assert result["pbp_retrieved_at"] == source["source_retrieved_at"]
    assert "1, 3" in site.render_coverage(result)


def test_missing_source_metadata_is_not_zero_or_build_time():
    result = site.summarize_coverage([], {})

    assert result["source_dropback_weeks"] is None
    assert result["source_dropback_games"] is None
    assert result["source_dropbacks"] is None
    assert result["pbp_retrieved_at"] is None
    assert result["player_profiles"] == 0
    assert "Unavailable" in site.render_coverage(result)
