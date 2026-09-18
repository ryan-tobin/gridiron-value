"""Profile statistic definitions, independent of a player's listed position."""

VERSION = 2

# Only additive player-game fields belong here.
# Percentages and longest-distance fields are handled separately.
GROUPS = {
    "passing": (
        "attempts",
        "completions",
        "passing_yards",
        "passing_tds",
        "passing_interceptions",
        "sacks_suffered",
        "passing_epa",
    ),
    "rushing": (
        "carries",
        "rushing_yards",
        "rushing_tds",
        "rushing_first_downs",
        "rushing_fumbles",
        "rushing_fumbles_lost",
        "rushing_2pt_conversions",
        "rushing_epa",
    ),
    "receiving": (
        "targets",
        "receptions",
        "receiving_yards",
        "receiving_tds",
        "receiving_first_downs",
        "receiving_air_yards",
        "receiving_yards_after_catch",
        "receiving_fumbles",
        "receiving_fumbles_lost",
        "receiving_2pt_conversions",
        "receiving_epa",
    ),
    "defense": (
        "def_tackles_solo",
        "def_tackles_with_assist",
        "def_tackle_assists",
        "def_tackles_for_loss",
        "def_tackles_for_loss_yards",
        "def_fumbles_forced",
        "def_sacks",
        "def_sack_yards",
        "def_qb_hits",
        "def_interceptions",
        "def_interception_yards",
        "def_pass_defended",
        "def_tds",
        "def_fumbles",
        "def_safeties",
        "def_punt_blocks",
        "def_pat_blocks",
        "def_fg_blocks",
        "def_2pt_atts",
        "def_2pt_made",
    ),
    "kicking": (
        "fg_att",
        "fg_made",
        "fg_missed",
        "fg_blocked",
        "pat_att",
        "pat_made",
        "pat_missed",
        "pat_blocked",
        "fg_made_0_19",
        "fg_made_20_29",
        "fg_made_30_39",
        "fg_made_40_49",
        "fg_made_50_59",
        "fg_made_60_",
        "fg_missed_0_19",
        "fg_missed_20_29",
        "fg_missed_30_39",
        "fg_missed_40_49",
        "fg_missed_50_59",
        "fg_missed_60_",
    ),
    "punting": (
        "pt_att",
        "pt_blocked",
        "pt_yards",
        "pt_net_yards",
        "pt_inside_20",
        "pt_touchback",
        "pt_out_of_bounds",
        "pt_downed",
        "pt_fair_caught",
        "pt_returned",
        "pt_return_yards",
        "pt_return_tds",
    ),
    "returns": (
        "punt_returns",
        "punt_return_yards",
        "kickoff_returns",
        "kickoff_return_yards",
        "special_teams_tds",
    ),
    "fumble_recoveries": (
        "fumble_recovery_own",
        "fumble_recovery_yards_own",
        "fumble_recovery_opp",
        "fumble_recovery_yards_opp",
        "fumble_recovery_tds",
    ),
    "penalties": (
        "penalties",
        "penalty_yards",
    ),
}

COUNTS = tuple(field for fields in GROUPS.values() for field in fields)

# Rates use sums from games where every required input is available.
# Existing EPA rates retain their source definitions and limitations.
RATES = {
    "completion_rate": ("completions", ("attempts",)),
    "pass_yards_per_attempt": ("passing_yards", ("attempts",)),
    "passing_td_rate": ("passing_tds", ("attempts",)),
    "interception_rate": ("passing_interceptions", ("attempts",)),
    "sack_rate_proxy": ("sacks_suffered", ("attempts", "sacks_suffered")),
    "rush_yards_per_carry": ("rushing_yards", ("carries",)),
    "rush_epa_per_carry": ("rushing_epa", ("carries",)),
    "catch_rate": ("receptions", ("targets",)),
    "receiving_yards_per_target": ("receiving_yards", ("targets",)),
    "receiving_yards_per_reception": ("receiving_yards", ("receptions",)),
    "receiving_epa_per_target": ("receiving_epa", ("targets",)),
    "yac_per_reception": ("receiving_yards_after_catch", ("receptions",)),
    "field_goal_rate": ("fg_made", ("fg_att",)),
    "extra_point_rate": ("pat_made", ("pat_att",)),
    "punt_return_yards_per_return": ("punt_return_yards", ("punt_returns",)),
    "kickoff_return_yards_per_return": (
        "kickoff_return_yards",
        ("kickoff_returns",),
    ),
}

LABELS = {
    "rushing_first_downs": "Rushing first downs",
    "rushing_fumbles": "Rushing fumbles",
    "rushing_fumbles_lost": "Rushing fumbles lost",
    "rushing_2pt_conversions": "Rushing two-point conversions",
    "receiving_first_downs": "Receiving first downs",
    "receiving_air_yards": "Receiving air yards (source)",
    "receiving_fumbles": "Receiving fumbles",
    "receiving_fumbles_lost": "Receiving fumbles lost",
    "receiving_2pt_conversions": "Receiving two-point conversions",
    "receiving_yards_per_reception": "Receiving yards per reception",
    "def_tackles_with_assist": "Tackles with assistance (source)",
    "def_tackles_for_loss": "Tackles for loss",
    "def_tackles_for_loss_yards": "Tackle-for-loss yards",
    "def_fumbles_forced": "Forced fumbles",
    "def_sack_yards": "Sack yards",
    "def_interception_yards": "Interception return yards",
    "def_tds": "Defensive TDs",
    "def_fumbles": "Fumbles on defense",
    "def_safeties": "Safeties",
    "def_punt_blocks": "Punts blocked by player",
    "def_pat_blocks": "Extra points blocked by player",
    "def_fg_blocks": "Field goals blocked by player",
    "def_2pt_atts": "Defensive two-point attempts",
    "def_2pt_made": "Defensive two-point conversions",
    "fg_missed": "Field goals missed",
    "fg_blocked": "Field-goal attempts blocked",
    "pat_missed": "Extra points missed",
    "pat_blocked": "Extra-point attempts blocked",
    "field_goal_rate": "Field-goal percentage",
    "extra_point_rate": "Extra-point percentage",
    "pt_blocked": "Punts blocked against player",
    "pt_inside_20": "Punts inside the 20",
    "pt_touchback": "Punt touchbacks",
    "pt_out_of_bounds": "Punts out of bounds",
    "pt_downed": "Punts downed",
    "pt_fair_caught": "Punts fair caught",
    "pt_returned": "Punts returned by opponent",
    "pt_return_yards": "Punt return yards allowed",
    "pt_return_tds": "Punt return TDs allowed",
    "special_teams_tds": "Kick/punt return TDs (combined)",
    "fumble_recovery_own": "Own-team fumbles recovered",
    "fumble_recovery_yards_own": "Own-team recovery yards",
    "fumble_recovery_opp": "Opponent fumbles recovered",
    "fumble_recovery_yards_opp": "Opponent recovery yards",
    "fumble_recovery_tds": "Fumble recovery TDs",
    "fg_long": "Longest made field goal in game",
    "pt_long": "Longest punt in game",
}

for suffix, distance in (
    ("0_19", "0–19"),
    ("20_29", "20–29"),
    ("30_39", "30–39"),
    ("40_49", "40–49"),
    ("50_59", "50–59"),
    ("60_", "60+"),
):
    LABELS[f"fg_made_{suffix}"] = f"Field goals made: {distance} yards"
    LABELS[f"fg_missed_{suffix}"] = f"Field goals missed: {distance} yards"

# Compact game logs; expandable details retain every catalog field.
GAME_LOGS = {
    "rushing": (
        "carries",
        "rushing_yards",
        "rushing_tds",
        "rushing_first_downs",
        "rushing_fumbles",
        "rushing_fumbles_lost",
    ),
    "receiving": (
        "targets",
        "receptions",
        "receiving_yards",
        "receiving_tds",
        "receiving_first_downs",
        "receiving_yards_after_catch",
        "receiving_fumbles_lost",
    ),
    "defense": (
        "def_tackles_solo",
        "def_tackle_assists",
        "def_tackles_for_loss",
        "def_sacks",
        "def_qb_hits",
        "def_interceptions",
        "def_pass_defended",
        "def_fumbles_forced",
    ),
    "kicking": (
        "fg_made",
        "fg_att",
        "fg_missed",
        "fg_blocked",
        "pat_made",
        "pat_att",
    ),
    "punting": (
        "pt_att",
        "pt_blocked",
        "pt_yards",
        "pt_net_yards",
        "pt_inside_20",
        "pt_touchback",
    ),
    "returns": GROUPS["returns"],
    "fumble_recoveries": GROUPS["fumble_recoveries"],
    "penalties": GROUPS["penalties"],
}

# Nonadditive values are retained in per-game details only.
GAME_CONTEXT = ("fg_long", "pt_long")

NOTES = [
    "Tackle categories are shown separately using source definitions; no combined tackle total is inferred.",
    "Fumble recoveries and penalties can belong to any position. They are not restricted to defense.",
    "Kick/punt return touchdowns are a combined source field and are not split between return types.",
    "Kicking percentages use recorded makes divided by recorded attempts; distance bins show source counts only.",
    "Longest field goals and punts are shown per game only and are not added across games.",
    "Punting counts and yards retain source definitions. Punt averages and defensive efficiency ratings are not calculated here.",
    "Statistical categories may overlap. Do not sum category touchdown or fumble fields into a player total.",
]


def contract():
    """Persist the definitions used to build this particular profile run."""
    return {
        "version": VERSION,
        "source": "player_stats fields preserved through position_metrics",
        "groups": GROUPS,
        "labels": LABELS,
        "count_aggregation": "Sum finite observed player-game values.",
        "rates": RATES,
        "rate_aggregation": (
            "Ratio of sums over games with every required input; "
            "never an average of game rates."
        ),
        "missing_policy": (
            "Missing and nonfinite values remain null. Partial sums and rates "
            "are reported as observed only. Nonpositive denominators yield null."
        ),
        "game_logs": GAME_LOGS,
        "game_context": GAME_CONTEXT,
        "notes": NOTES,
    }
