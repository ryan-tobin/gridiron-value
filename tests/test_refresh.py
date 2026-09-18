import json

import pytest

from gridiron_value import historical as h
from gridiron_value import refresh


def test_coverage_changes_keep_missing_counts_unknown():
    old = {
        "season": 2026,
        "feeds": {
            "pbp": {"status": "available", "rows": 100},
        },
    }
    new = {
        "season": 2026,
        "feeds": {
            "pbp": {"status": "error"},
        },
    }

    row = refresh.compare_coverage(old, new)[0]

    assert row["current_rows"] is None
    assert row["row_delta"] is None

    with pytest.raises(ValueError, match="seasons"):
        refresh.compare_coverage(
            {**old, "season": 2025},
            new,
        )


@pytest.mark.parametrize("exit_code", [0, 2])
def test_stage_uses_printed_manifest_and_checks_exit_code(tmp_path, exit_code):
    package = tmp_path / "gridiron_value"
    package.mkdir()
    (package / "__init__.py").write_text("")

    (package / "worker.py").write_text(
        "from pathlib import Path\n"
        "Path('fresh_manifest.json').write_text('{\"files\": {}}')\n"
        "print('Manifest: fresh_manifest.json')\n"
        f"raise SystemExit({exit_code})\n"
    )

    (tmp_path / "old_manifest.json").write_text('{"files": {}}')

    arguments = (
        tmp_path,
        "worker",
        [],
        "fresh_manifest.json",
        tmp_path / "stage.log",
    )

    if exit_code:
        with pytest.raises(RuntimeError, match="code 2"):
            refresh.run_stage(*arguments)
    else:
        assert refresh.run_stage(*arguments) == (tmp_path / "fresh_manifest.json")

    assert "fresh_manifest.json" in (tmp_path / "stage.log").read_text()


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("")

    snapshot = tmp_path / "snapshot.bin"
    snapshot.write_bytes(b"test snapshot")

    coverage = tmp_path / "coverage_manifest.json"

    h.save_json(
        coverage,
        {
            "season": 2026,
            "feeds": {
                name: {
                    "status": "available",
                    "rows": 10,
                    "snapshot": h.record(tmp_path, snapshot),
                }
                for name in refresh.FEEDS
            },
        },
    )

    calls = []
    controls = {"fail": None, "mismatch": False}

    def fake_stage(root, module, arguments, filename, log):
        calls.append((module, list(arguments)))

        if module == controls["fail"]:
            raise RuntimeError("test stage failure")

        if module == "coverage_audit":
            return coverage

        path = root / filename
        state = {"files": {}}

        if module == "player_identity":
            state.update(
                status="needs_review",
                unattributed_stats_rows=1,
            )

        if module == "reconcile_passing":
            csv = root / "summary.csv"
            status = "difference" if controls["mismatch"] else "match"

            csv.write_text(
                "purpose,status\n"
                f"count_reconstruction,{status}\n"
                "definition_comparison,difference\n"
            )

            state["files"]["summary"] = h.record(root, csv)

        h.save_json(path, state)
        return path

    monkeypatch.setattr(refresh, "run_stage", fake_stage)

    return tmp_path, coverage, calls, controls


@pytest.mark.parametrize("reuse", [False, True])
def test_full_chain_uses_explicit_outputs_and_retains_review(pipeline, reuse):
    root, coverage, calls, _ = pipeline

    path = refresh.build(
        root,
        2026,
        coverage=coverage if reuse else None,
    )

    state = h.read_json(path)

    assert state["status"] == "completed_with_review"
    assert ("coverage_audit" in dict(calls)) is (not reuse)
    assert state["stages"]["coverage_audit"] == h.record(root, coverage)

    for _, arguments in calls:
        assert "--latest" not in arguments

    final_args = dict(calls)["rebuild_site"]

    for flag, stage in (
        ("--position-metrics-manifest", "position_metrics"),
        ("--participation-manifest", "participation"),
        ("--current-dropbacks-manifest", "current_dropbacks"),
    ):
        argument = final_args[final_args.index(flag) + 1]

        assert h.record(root, argument) == state["stages"][stage]


@pytest.mark.parametrize(
    "kind",
    ["stage_failure", "reconstruction_mismatch"],
)
def test_failure_keeps_coverage_checkpoint_and_stops_downstream(pipeline, kind):
    root, coverage, calls, controls = pipeline

    if kind == "stage_failure":
        controls["fail"] = "player_identity"
    else:
        controls["mismatch"] = True

    with pytest.raises((RuntimeError, ValueError)):
        refresh.build(root, 2026, coverage=coverage)

    checkpoint = next((root / "reports/tables").glob("refresh_*/refresh_manifest.json"))

    state = json.loads(checkpoint.read_text())

    assert state["status"] == "failed"
    assert state["stages"]["coverage_audit"] == h.record(root, coverage)
    assert "rebuild_site" not in dict(calls)


def test_empty_ngs_is_allowed_but_failed_required_feed_is_not(pipeline):
    root, path, _, _ = pipeline
    state = h.read_json(path)

    state["feeds"]["ngs_passing"]["status"] = "empty_for_season"
    refresh.check_coverage(root, state, 2026)

    state["feeds"]["pbp"]["status"] = "error"

    with pytest.raises(ValueError, match="Required feed pbp"):
        refresh.check_coverage(root, state, 2026)


def test_changed_snapshot_bytes_are_rejected(pipeline):
    root, path, _, _ = pipeline
    (root / "snapshot.bin").write_bytes(b"changed")

    with pytest.raises(ValueError, match="Checksum"):
        refresh.check_coverage(root, h.read_json(path), 2026)
