"""Calculate descriptive passing production relative to league average."""

import polars as pl


def passing_baseline(cohort: pl.DataFrame) -> pl.DataFrame:
    """Summarize production and compare it with each season's league average."""
    league = cohort.group_by("season").agg(
        pl.len().alias("league_dropbacks"),
        pl.col("epa").mean().alias("league_epa_per_dropback"),
    )

    players = cohort.group_by("season", "dropback_player_id").agg(
        pl.col("dropback_player_name").drop_nulls().first().alias("player"),
        pl.len().alias("dropbacks"),
        pl.col("game_id").n_unique().alias("games"),
        pl.col("epa").sum().alias("total_epa"),
        pl.col("epa").mean().alias("epa_per_dropback"),
        (pl.col("epa") > 0).mean().alias("positive_epa_rate"),
        (pl.col("sack") == 1).sum().alias("sacks"),
        (pl.col("qb_scramble") == 1).sum().alias("scrambles"),
        (pl.col("penalty") == 1).sum().alias("penalty_rows"),
        (pl.col("identity_source") == "scramble_rusher_id")
        .sum()
        .alias("identity_fallbacks"),
    )

    result = players.join(
        league,
        on="season",
        how="left",
        validate="m:1",
    ).with_columns(
        (pl.col("epa_per_dropback") - pl.col("league_epa_per_dropback")).alias(
            "epa_above_average_per_dropback"
        ),
        (
            pl.col("total_epa")
            - pl.col("dropbacks") * pl.col("league_epa_per_dropback")
        ).alias("epa_above_average"),
    )

    # Centering against the same cohort must balance to zero each season.
    balance = result.group_by("season").agg(
        pl.col("epa_above_average").sum().abs().alias("residual")
    )

    if balance.filter(pl.col("residual") > 1e-8).height:
        raise ValueError("Season-level EPA above average does not balance.")

    return result.sort(
        ["season", "epa_per_dropback", "dropback_player_id"],
        descending=[False, True, False],
    )
