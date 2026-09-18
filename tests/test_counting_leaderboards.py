import csv
from html.parser import HTMLParser

import pytest
import test_leaderboards as fixtures

from gridiron_value import counting_leaderboards as cb
from gridiron_value import historical as h
from gridiron_value import leaderboards as lb
from gridiron_value import player_profile as pp


def selected(profiles, metric):
    return {r["gsis_id"]: r for r in cb.compare(profiles) if r["metric"] == metric}


def save(root, profiles):
    path = fixtures.save_profiles(root, profiles)

    (path.parent / "index.html").write_text(
        "Player directory",
        encoding="utf-8",
    )

    for profile in profiles:
        (path.parent / lb.player_filename(profile)).write_text(
            pp.render_profile(profile),
            encoding="utf-8",
        )

    state = h.read_json(path)

    for page in path.parent.glob("*.html"):
        state["files"][page.name] = h.record(root, page)

    h.save_json(path, state)
    return path


def test_ties_use_totals_across_positions_without_workload_thresholds():
    players = [
        fixtures.profile("qb", "QB", rushing_yards=10, carries=1),
        fixtures.profile("rb", "RB", rushing_yards=10, carries=5),
        fixtures.profile("wr", "WR", rushing_yards=3, carries=1),
    ]

    rows = selected(players, "rushing_yards")

    assert {key: row["rank"] for key, row in rows.items()} == {
        "qb": 1,
        "rb": 1,
        "wr": 3,
    }


def test_partial_and_missing_totals_do_not_receive_ranks():
    partial = fixtures.profile("partial", "RB", rushing_yards=100)

    second = dict(
        partial["games"][0],
        game_id="g2",
        week=2,
        rushing_yards=None,
    )

    partial = pp.profiles_from_games(
        partial["games"] + [second],
        2026,
    )[0]

    rows = selected(
        [
            partial,
            fixtures.profile("complete", "RB", rushing_yards=10),
            fixtures.profile("missing", "RB"),
        ],
        "rushing_yards",
    )

    assert rows["complete"]["rank"] == 1
    assert rows["partial"]["rank"] is None
    assert rows["partial"]["status"] == "partial"
    assert rows["partial"]["observed_total"] == 100
    assert rows["missing"]["status"] == "unavailable"
    assert rows["missing"]["observed_total"] is None


def test_recorded_zero_ranks_above_negative_yards():
    rows = selected(
        [
            fixtures.profile("zero", "RB", rushing_yards=0),
            fixtures.profile("negative", "RB", rushing_yards=-2),
        ],
        "rushing_yards",
    )

    assert rows["zero"]["rank"] == 1
    assert rows["negative"]["rank"] == 2


def test_build_keeps_cutoff_ties_escapes_names_and_verifies_links(tmp_path):
    players = [
        fixtures.profile("a", "RB", rushing_yards=10),
        fixtures.profile("b", "RB", rushing_yards=10),
    ]

    players[0]["name"] = '<script>alert("x")</script>'

    manifest = save(tmp_path, players)
    output = cb.build(tmp_path, manifest, 2026, limit=1)
    state = h.read_json(output / "counting_leaderboard_manifest.json")

    assert state["source_profile_manifest"] == h.record(tmp_path, manifest)

    for record in (
        *state["files"].values(),
        *state["linked_profile_pages"].values(),
    ):
        h.verify(tmp_path, record)

    markup = (output / "index.html").read_text()

    assert '<script>alert("x")</script>' not in markup
    assert "&lt;script&gt;" in markup

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                target = dict(attrs).get("href", "")

                if target and not target.startswith("#"):
                    assert (output / target).resolve().is_file()

    Links().feed(markup)

    section = markup.split("Rushing yards · 2 ranked</summary>")[1].split("</details>")[
        0
    ]

    assert lb.player_filename(players[0]) in section
    assert lb.player_filename(players[1]) in section

    with (output / "counting_leaderboards.csv").open() as handle:
        rows = [r for r in csv.DictReader(handle) if r["metric"] == "rushing_yards"]

    assert [row["rank"] for row in rows] == ["1", "1"]


@pytest.mark.parametrize("change", ["totals", "page", "profiles"])
def test_modified_inputs_fail_before_output(tmp_path, change):
    profile = fixtures.profile("rb", "RB", rushing_yards=10)
    path = save(tmp_path, [profile])
    state = h.read_json(path)

    if change == "totals":
        data = path.parent / "profiles.json"
        payload = h.read_json(data)

        payload["profiles"][0]["totals"]["rushing_yards"]["observed_sum"] = 999

        h.save_json(data, payload)
        state["files"]["profiles.json"] = h.record(tmp_path, data)
        h.save_json(path, state)

    else:
        name = lb.player_filename(profile) if change == "page" else "profiles.json"

        with (path.parent / name).open("a") as handle:
            handle.write("changed")

    with pytest.raises(ValueError, match="totals disagree|Checksum"):
        cb.build(tmp_path, path, 2026)

    assert not list((tmp_path / "reports/tables").glob("counting_leaderboards_*"))


def test_no_relevant_production_still_writes_csv_header_and_page(tmp_path):
    manifest = save(tmp_path, [fixtures.profile("ol", "T")])
    output = cb.build(tmp_path, manifest, 2026)

    with (output / "counting_leaderboards.csv").open() as handle:
        reader = csv.DictReader(handle)

        assert reader.fieldnames == list(cb.FIELDS)
        assert list(reader) == []

    markup = (output / "index.html").read_text()

    assert "No complete observed-game totals available" in markup
