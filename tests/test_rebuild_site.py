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

    profile = state["stage_manifests"]["profiles"]

    for key in ("dropback_leaderboard", "position_leaderboards"):
        stage = h.verify_manifest(
            pipeline,
            state["stage_manifests"][key],
        )
        assert stage["source_profile_manifest"] == profile

    for record in state["linked_pages"].values():
        h.verify(pipeline, record)

    assert (output / "index.html").exists()


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
