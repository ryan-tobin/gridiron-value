import copy
from html.parser import HTMLParser

import pytest

from gridiron_value import historical as h
from gridiron_value import leaderboards as lb
from gridiron_value import player_profile as pp


def profile(player, position="QB", **stats):
    game = {
        **{field: None for field in (*pp.COUNTS, *pp.SNAPS)},
        "gsis_id": player,
        "name": player,
        "position": position,
        "team": "AAA",
        "opponent": "BBB",
        "season": 2026,
        "season_type": "REG",
        "week": 1,
        "game_id": "2026_01_AAA_BBB",
        "has_production": True,
        "has_participation": False,
        "identity_status": None,
        "context": {},
        **stats,
    }
    return pp.profiles_from_games([game], 2026)[0]


def selected(profiles, metric="completion_rate", settings=None):
    return {
        r["gsis_id"]: r for r in lb.compare(profiles, settings) if r["metric"] == metric
    }


def save_profiles(root, profiles, run="20260916T010000000000Z", **overrides):
    folder = root / "reports" / "tables" / f"player_profiles_{run}"
    folder.mkdir(parents=True)
    path = folder / "profiles.json"
    h.save_json(path, {"schema_version": 1, "profiles": profiles})
    manifest = folder / "profile_manifest.json"
    h.save_json(
        manifest,
        {
            "run_id": run,
            "season": 2026,
            "season_type": "REG",
            "gsis_id_filter": None,
            "profile_count": len(profiles),
            "files": {"profiles.json": h.record(root, path)},
            **overrides,
        },
    )
    return manifest


def test_ties_competition_ranks_midpoint_percentiles():
    players = [
        profile(str(i), attempts=20, completions=c)
        for i, c in enumerate([16, 16, 12, 10, 8])
    ]
    rows = selected(players)
    assert [r["rank"] for r in rows.values()] == [1, 1, 3, 4, 5]
    assert [r["percentile"] for r in rows.values()] == [80, 80, 50, 30, 10]
    assert all(r["peer_count"] == 5 for r in rows.values())


def test_lower_is_better_and_all_tied():
    players = [
        profile(str(i), attempts=20, passing_interceptions=i, sacks_suffered=0)
        for i in range(5)
    ]
    rows = selected(players, "interception_rate")
    assert rows["0"]["rank"] == 1
    assert rows["0"]["percentile"] == 90
    assert rows["4"]["percentile"] == 10
    sacks = selected(players, "sack_rate_proxy")
    assert all(r["percentile"] == 50 and r["rank"] == 1 for r in sacks.values())


def test_qualification_boundary_partial_missing_and_small_cohort():
    players = [
        profile("exact", attempts=20, completions=10),
        profile("short", attempts=19, completions=19),
        profile("missing", attempts=30),
    ]
    partial = profile("partial", attempts=30, completions=20)
    other = dict(partial["games"][0], game_id="g2", week=2, completions=None)
    partial = pp.profiles_from_games(partial["games"] + [other], 2026)[0]
    rows = selected(players + [partial])
    assert rows["exact"]["status"] == "small_cohort"
    assert rows["exact"]["rank"] == 1 and rows["exact"]["percentile"] is None
    assert rows["short"]["status"] == "below_minimum"
    assert rows["missing"]["status"] == rows["partial"]["status"] == "incomplete"
    assert all(r["peer_count"] == 1 for r in rows.values())
    assert rows["partial"]["rank"] is None


def test_role_separation_and_metric_specific_cohorts():
    players = [
        profile("wr", "WR", targets=5, receptions=4, receiving_yards=50),
        profile("te", "TE", targets=5, receptions=5, receiving_yards=50),
        profile("fb", "FB", carries=10, rushing_yards=20),
        profile("defender", "DE", def_sacks=2),
    ]
    mixed = profile("mixed", targets=5, receptions=4)
    mixed["positions"] = ["QB", "WR"]
    rows = lb.compare(players + [mixed])
    assert not any(r["gsis_id"] in ("defender", "mixed") for r in rows)
    assert {r["role"] for r in rows if r["gsis_id"] == "fb"} == {"RB"}
    catches = selected(players, "catch_rate")
    assert catches["wr"]["peer_count"] == catches["te"]["peer_count"] == 1
    assert selected(players, "yac_per_reception")["wr"]["rank"] is None


def test_sum_rates_are_weighted_by_opportunities():
    first = profile("qb", attempts=20, completions=20)
    second = dict(first["games"][0], game_id="g2", week=2, attempts=80, completions=20)
    player = pp.profiles_from_games(first["games"] + [second], 2026)[0]
    assert selected([player])["qb"]["value"] == 0.4
    assert selected([player])["qb"]["denominator"] == 100


@pytest.mark.parametrize(
    "settings",
    [
        {"minimum_peers": 1},
        {"attempts": 0},
        {"attempts": True},
        {"attempts": 1.5},
        {"unknown": 4},
    ],
)
def test_reject_invalid_config(settings):
    with pytest.raises(ValueError):
        lb.qualification(settings)


def test_custom_minimum_changes_qualification():
    players = [
        profile("a", attempts=20, completions=15),
        profile("b", attempts=21, completions=15),
    ]
    assert selected(players, settings={"minimum_peers": 2})["a"]["percentile"] == 75
    assert (
        selected(players, settings={"attempts": 21})["a"]["status"] == "below_minimum"
    )


@pytest.mark.parametrize(
    "override",
    [
        {"gsis_id_filter": "qb"},
        {"season": 2025},
        {"season_type": "POST"},
        {"profile_count": 2},
    ],
)
def test_reject_filtered_or_inconsistent_manifest(tmp_path, override):
    manifest = save_profiles(tmp_path, [profile("qb")], **override)
    with pytest.raises(ValueError):
        lb.build(tmp_path, manifest, 2026)


def test_checksum_failure_and_rate_disagreement(tmp_path):
    manifest = save_profiles(tmp_path, [profile("qb", attempts=20, completions=10)])
    path = manifest.parent / "profiles.json"
    data = h.read_json(path)
    data["profiles"][0]["rates"]["completion_rate"]["value"] = 0.9
    h.save_json(path, data)
    with pytest.raises(ValueError, match="Checksum"):
        lb.build(tmp_path, manifest, 2026)
    state = h.read_json(manifest)
    state["files"]["profiles.json"] = h.record(tmp_path, path)
    h.save_json(manifest, state)
    with pytest.raises(ValueError, match="rates disagree"):
        lb.build(tmp_path, manifest, 2026)


def test_duplicate_profile_ids_rejected(tmp_path):
    player = profile("qb")
    manifest = save_profiles(tmp_path, [player, copy.deepcopy(player)])
    with pytest.raises(ValueError, match="unique player IDs"):
        lb.build(tmp_path, manifest, 2026)


def test_latest_matches_full_season_and_does_not_fallback(tmp_path):
    players = [profile("qb")]
    first = save_profiles(tmp_path, players, "20260916T010000000000Z")
    selected_manifest = save_profiles(tmp_path, players, "20260916T020000000000Z")
    save_profiles(tmp_path, players, "20260916T030000000000Z", gsis_id_filter="qb")
    save_profiles(tmp_path, players, "20260916T040000000000Z", season=2025)
    save_profiles(tmp_path, players, "20260916T050000000000Z", season_type="POST")
    assert lb.latest(tmp_path, 2026, "REG") == selected_manifest
    (selected_manifest.parent / "profiles.json").write_text("{}")
    with pytest.raises(ValueError, match="Checksum"):
        lb.build(tmp_path, lb.latest(tmp_path, 2026, "REG"), 2026)
    assert first.exists()
    with pytest.raises(ValueError, match="No full"):
        lb.latest(tmp_path, 2024, "REG")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href")


def test_build_preserves_profiles_provenance_links_and_escaping(tmp_path):
    players = [profile(str(i), attempts=20, completions=10 + i) for i in range(5)]
    players += [profile("defender", "DE", def_sacks=2), profile("snap_only", "T")]
    players[0]["name"] = '<script>alert("x")</script>'
    manifest = save_profiles(tmp_path, players)
    output = lb.build(tmp_path, manifest, 2026)
    state = h.read_json(output / "leaderboard_manifest.json")
    assert state["source_profile_manifest"] == h.record(tmp_path, manifest)
    assert state["profile_count"] == 7 and state["uncompared_profile_count"] == 2
    for record in state["files"].values():
        path = h.verify(tmp_path, record)
        if path.suffix == ".html":
            markup = path.read_text()
            assert '<script>alert("x")</script>' not in markup
            parser = Links()
            parser.feed(markup)
            for link in parser.hrefs:
                assert link.startswith("#") or (output / link).is_file()
    markup = (output / lb.player_filename(players[0])).read_text()
    assert "<meter" in markup and "Early-season snapshot" in markup
    assert "does not calculate current-season Pass+ or peer percentiles" not in markup
    assert "Sack-rate proxy excludes scrambles" in markup
    defender = (output / lb.player_filename(players[-2])).read_text()
    assert "No comparison group" in defender and "Solo tackles" in defender
    assert "<meter" not in defender
    payload = h.read_json(output / "comparisons.json")
    assert len(payload["uncompared_profiles"]) == 2


def test_all_unsupported_still_produces_directory(tmp_path):
    manifest = save_profiles(tmp_path, [profile("ol", "T")])
    output = lb.build(tmp_path, manifest, 2026)
    assert (output / "comparisons.csv").read_text().startswith("gsis_id,")
    assert "No players qualify" in (output / "leaderboards.html").read_text()
