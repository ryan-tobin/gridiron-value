import polars as pl
import pytest

from gridiron_value.position_metrics import derive_metrics


def base(**values):
    row = {
        "season": 2026,
        "week": 1,
        "game_id": "g1",
        "gsis_id": "p1",
        "position": "QB",
        "attempts": 10,
        "sacks_suffered": 2,
        "passing_epa": 5.0,
        "passing_yards": 200,
        "completions": 6,
        "passing_tds": 2,
        "passing_interceptions": 1,
        "carries": 2,
        "rushing_epa": 1.0,
        "rushing_yards": 10,
        "rushing_20": 1,
        "targets": 0,
        "receptions": 0,
        "receiving_epa": 0.0,
        "receiving_yards": 0,
        "receiving_yards_after_catch": 0,
        "receiving_20": 0,
    }
    row.update(values)
    return pl.DataFrame(row)


def test_qb_rates_use_explicit_denominators():
    result = derive_metrics(base()).row(0, named=True)
    assert result["pass_epa_per_attempt"] is None
    assert result["completion_rate"] == 0.6
    assert result["sack_rate_proxy"] == pytest.approx(2 / 12)
    assert result["metric_role"] == "QB"


def test_zero_denominators_are_null():
    result = derive_metrics(base(attempts=0, carries=0)).row(0, named=True)
    assert result["pass_epa_per_attempt"] is None
    assert result["rush_yards_per_carry"] is None
    assert result["catch_rate"] is None


def test_receiver_metrics_and_role():
    result = derive_metrics(
        base(
            position="WR",
            attempts=0,
            sacks_suffered=0,
            carries=0,
            targets=5,
            receptions=4,
            receiving_epa=2,
            receiving_yards=60,
            receiving_yards_after_catch=20,
            receiving_20=1,
        )
    ).row(0, named=True)
    assert result["metric_role"] == "WR"
    assert result["receiving_yards_per_target"] == 12
    assert result["catch_rate"] == 0.8
    assert result["explosive_reception_rate"] == 0.25


def test_duplicate_player_game_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        derive_metrics(pl.concat([base(), base()]))


def test_deprecated_passing_rate_overwrites_stale_values():
    result = derive_metrics(base(pass_epa_per_attempt=0.5))

    assert result.schema["pass_epa_per_attempt"] == pl.Float64
    assert result["pass_epa_per_attempt"].to_list() == [None]
    assert result["passing_epa"].to_list() == [5.0]
    assert result["pass_yards_per_attempt"].to_list() == [20.0]
