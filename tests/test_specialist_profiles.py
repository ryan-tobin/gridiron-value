import pytest
from test_stat_catalog import make_profile

from gridiron_value import player_profile as pp
from gridiron_value import stat_catalog as sc


def test_defense_preserves_sack_fractions_and_separate_tackle_fields():
    profile = make_profile(
        position=["DE", "DE"],
        attempts=[0, 0],
        completions=[0, 0],
        passing_yards=[0, 0],
        def_sacks=[0.5, 1.0],
        def_tackles_solo=[2, 3],
        def_tackles_with_assist=[1, 2],
        def_tackle_assists=[2, 1],
        def_fumbles_forced=[0, None],
    )

    assert profile["totals"]["def_sacks"]["value"] == 1.5
    assert profile["totals"]["def_tackles_solo"]["value"] == 5
    assert profile["totals"]["def_tackles_with_assist"]["value"] == 3
    assert profile["totals"]["def_tackle_assists"]["value"] == 3
    assert "def_tackles_total" not in profile["totals"]

    assert profile["totals"]["def_fumbles_forced"]["value"] is None
    assert profile["totals"]["def_fumbles_forced"]["observed_sum"] == 0

    markup = pp.render_profile(profile)

    assert "<h3>Defense game log</h3>" in markup
    assert "Defensive efficiency metrics are not implemented" in markup


def test_kicking_percentages_use_total_opportunities():
    profile = make_profile(
        position=["K", "K"],
        fg_made=[1, 2],
        fg_att=[1, 4],
        fg_pct=[1.0, 0.5],
        pat_made=[1, 7],
        pat_att=[2, 8],
        fg_made_50_59=[1, 0],
        fg_missed_50_59=[0, 1],
    )

    assert profile["rates"]["field_goal_rate"]["value"] == 0.6
    assert profile["rates"]["extra_point_rate"]["value"] == 0.8
    assert profile["totals"]["fg_made_50_59"]["value"] == 1
    assert profile["totals"]["fg_missed_50_59"]["value"] == 1

    markup = pp.render_profile(profile)

    assert "60.0%" in markup
    assert "80.0%" in markup
    assert "<h3>Kicking game log</h3>" in markup


@pytest.mark.parametrize(
    ("metric", "numerator", "denominator"),
    [
        ("field_goal_rate", "fg_made", "fg_att"),
        ("extra_point_rate", "pat_made", "pat_att"),
        ("punt_return_yards_per_return", "punt_return_yards", "punt_returns"),
        (
            "kickoff_return_yards_per_return",
            "kickoff_return_yards",
            "kickoff_returns",
        ),
    ],
)
def test_specialist_rates_keep_zero_and_missing_opportunities_distinct(
    metric, numerator, denominator
):
    zero = make_profile(
        **{
            numerator: [0, 0],
            denominator: [0, 0],
        }
    )

    assert zero["rates"][metric]["value"] is None
    assert zero["rates"][metric]["denominator"] == 0

    partial = make_profile(
        **{
            numerator: [1, None],
            denominator: [2, 8],
        }
    )

    rate = partial["rates"][metric]

    assert rate["value"] is None
    assert rate["observed_value"] == 0.5
    assert rate["denominator"] == 2
    assert rate["observed_games"] == 1


def test_punt_outcomes_do_not_become_returner_touchdowns():
    profile = make_profile(
        position=["P", "P"],
        pt_att=[3, 4],
        pt_blocked=[1, 0],
        pt_yards=[120, 190],
        pt_net_yards=[90, 150],
        pt_inside_20=[1, 2],
        pt_return_tds=[1, 0],
    )

    assert profile["totals"]["pt_yards"]["value"] == 310
    assert profile["totals"]["pt_net_yards"]["value"] == 240
    assert profile["totals"]["pt_return_tds"]["value"] == 1
    assert profile["totals"]["special_teams_tds"]["value"] is None
    assert "returns" not in pp.profile_groups(profile)

    markup = pp.render_profile(profile)

    assert "<h3>Punting game log</h3>" in markup
    assert "Punt return TDs allowed" in markup


def test_any_position_can_have_returns_recoveries_and_penalties():
    profile = make_profile(
        position=["T", "T"],
        punt_returns=[1, 3],
        punt_return_yards=[-2, 22],
        kickoff_returns=[1, 2],
        kickoff_return_yards=[20, 70],
        special_teams_tds=[0, 1],
        fumble_recovery_own=[1, 0],
        penalties=[0, 1],
        penalty_yards=[0, 10],
    )

    assert {"returns", "fumble_recoveries", "penalties"} <= set(
        pp.profile_groups(profile)
    )

    assert profile["rates"]["punt_return_yards_per_return"]["value"] == 5
    assert profile["rates"]["kickoff_return_yards_per_return"]["value"] == 30
    assert profile["totals"]["special_teams_tds"]["value"] == 1
    assert profile["totals"]["fumble_recovery_own"]["value"] == 1
    assert "<h3>Returns game log</h3>" in pp.render_profile(profile)


def test_longest_distances_stay_at_game_grain():
    profile = make_profile(
        position=["K", "K"],
        fg_long=[52, 47],
        pt_long=[None, 61],
    )

    assert not set(sc.GAME_CONTEXT) & set(sc.COUNTS)
    assert "fg_long" not in profile["totals"]
    assert "pt_long" not in profile["totals"]

    assert profile["games"][0]["context"]["fg_long"] == 52
    assert profile["games"][1]["context"]["fg_long"] == 47
    assert profile["games"][0]["context"]["pt_long"] is None

    assert "Longest made field goal in game" in pp.render_profile(profile)
