import json

import polars as pl
import pytest
import test_player_profile as fixtures

from gridiron_value import historical as h
from gridiron_value import player_profile as pp
from gridiron_value import stat_catalog as sc


def make_profile(**columns):
    metrics, participation = fixtures.inputs()

    metrics = metrics.filter(pl.col("gsis_id") == "p1")
    participation = participation.filter(pl.col("resolved_gsis_id") == "p1")

    metrics = metrics.with_columns(
        *(pl.Series(name, values) for name, values in columns.items())
    )

    return pp.profiles_from_games(pp.combine_games(metrics, participation), 2026)[0]


def test_catalog_has_unique_fields_and_valid_rate_inputs():
    assert len(sc.COUNTS) == len(set(sc.COUNTS))

    for numerator, denominators in sc.RATES.values():
        assert numerator in sc.COUNTS
        assert set(denominators) <= set(sc.COUNTS)

    for group, fields in sc.GAME_LOGS.items():
        assert set(fields) <= set(sc.GROUPS[group])

    json.dumps(sc.contract(), allow_nan=False)


def test_rushing_and_receiving_use_totals_and_keep_new_fields():
    profile = make_profile(
        position=["RB", "RB"],
        carries=[2, 18],
        rushing_yards=[20, 54],
        rushing_first_downs=[1, 4],
        rushing_fumbles=[0, 1],
        rushing_fumbles_lost=[0, 0],
        rushing_2pt_conversions=[0, 1],
        targets=[2, 8],
        receptions=[2, 4],
        receiving_yards=[30, 20],
        receiving_air_yards=[-5, 15],
    )

    assert profile["rates"]["rush_yards_per_carry"]["value"] == 3.7
    assert profile["rates"]["catch_rate"]["value"] == 0.6

    assert profile["rates"]["receiving_yards_per_reception"]["value"] == pytest.approx(
        50 / 6
    )

    assert profile["totals"]["rushing_first_downs"]["value"] == 5
    assert profile["totals"]["rushing_fumbles_lost"]["value"] == 0
    assert profile["totals"]["rushing_2pt_conversions"]["value"] == 1
    assert profile["totals"]["receiving_air_yards"]["value"] == 10


def test_partial_rates_only_pair_games_with_both_inputs():
    profile = make_profile(
        receptions=[2, 8],
        receiving_yards=[30, None],
        rushing_first_downs=[1, None],
    )

    rate = profile["rates"]["receiving_yards_per_reception"]

    assert rate["value"] is None
    assert rate["observed_value"] == 15
    assert rate["denominator"] == 2
    assert rate["observed_games"] == 1
    assert pp.coverage_status(rate) == "Partial"

    assert profile["totals"]["rushing_first_downs"]["value"] is None
    assert profile["totals"]["rushing_first_downs"]["observed_sum"] == 1


def test_zero_opportunities_and_nonfinite_values_stay_unavailable():
    profile = make_profile(
        receptions=[0, 0],
        receiving_yards=[0, 0],
        receiving_fumbles=[float("nan"), float("inf")],
    )

    assert profile["rates"]["receiving_yards_per_reception"]["value"] is None
    assert profile["totals"]["receiving_fumbles"]["observed_sum"] is None

    json.dumps(profile, allow_nan=False)


def test_receiving_section_and_logs_are_not_restricted_to_receivers():
    profile = make_profile(
        position=["QB", "QB"],
        targets=[1, 0],
        receptions=[1, 0],
        receiving_yards=[8, 0],
        receiving_fumbles_lost=[0, None],
    )

    profile["games"][0]["opponent"] = "<opponent>"
    markup = pp.render_profile(profile)

    assert "<h2>Receiving</h2>" in markup
    assert "<h3>Receiving game log</h3>" in markup
    assert "Receiving yards per reception" in markup
    assert "&lt;opponent&gt;" in markup
    assert "<opponent>" not in markup
    assert "Unavailable" in markup


def test_snap_only_player_is_preserved_without_invented_production():
    profiles = pp.profiles_from_games(pp.combine_games(*fixtures.inputs()), 2026)
    profile = next(p for p in profiles if p["gsis_id"] == "p3")

    assert profile["totals"]["offense_snaps"]["value"] == 60
    assert all(profile["totals"][c]["observed_sum"] is None for c in sc.COUNTS)
    assert "<h3>Receiving game log</h3>" not in pp.render_profile(profile)


def test_build_records_catalog_and_source_field_availability(tmp_path):
    output = pp.build(tmp_path, *fixtures.saved_inputs(tmp_path), 2026)
    manifest = h.read_json(output / "profile_manifest.json")

    catalog = h.read_json(h.verify(tmp_path, manifest["files"]["stat_catalog.json"]))

    assert catalog["version"] == sc.VERSION
    assert "rushing_first_downs" in catalog["groups"]["rushing"]

    missing = manifest["stat_catalog"]["missing_source_fields"]

    assert "rushing_first_downs" in missing
    assert "attempts" not in missing
