import polars as pl
import pytest

from gridiron_value.player_identity import resolve_snaps, split_stats


def mapping(pfr, gsis):
    return pl.DataFrame({"pfr_id": pfr, "gsis_id": gsis})


def test_registry_supplements_rosters_without_dropping_rows():
    snaps = pl.DataFrame({"pfr_player_id": ["a", "b", "c", None]})
    result = resolve_snaps(snaps, {
        "rosters": mapping(["a"], ["1"]), "players": mapping(["b"], ["2"]),
    })
    assert result.height == 4
    assert result["resolved_gsis_id"].to_list() == ["1", "2", None, None]
    assert result["identity_status"].to_list() == ["resolved", "resolved", "unmapped", "missing_pfr_id"]


def test_conflicting_source_mappings_remain_unresolved():
    result = resolve_snaps(pl.DataFrame({"pfr_player_id": ["a"]}), {
        "rosters": mapping(["a"], ["1"]), "players": mapping(["a"], ["2"]),
    })
    assert result["identity_status"][0] == "conflict"
    assert result["resolved_gsis_id"][0] is None
    assert result["candidate_gsis_ids"][0] == "1|2"


def test_repeated_same_mapping_does_not_multiply_snap_rows():
    result = resolve_snaps(pl.DataFrame({"pfr_player_id": ["a"]}), {
        "rosters": mapping(["a", "a"], ["1", "1"]),
        "players": mapping(["a"], ["1"]),
    })
    assert result.height == 1
    assert result["identity_status"][0] == "resolved"
    assert result["identity_sources"][0] == "players|rosters"


def test_anonymous_nonzero_stats_are_preserved():
    source = pl.DataFrame({"player_id": ["a", None, " "], "yards": [20, 5, 3]})
    identified, anonymous = split_stats(source)
    assert identified.height + anonymous.height == source.height
    assert anonymous["yards"].sum() == 8


def test_missing_mapping_schema_is_rejected():
    with pytest.raises(ValueError, match="explicit"):
        resolve_snaps(pl.DataFrame({"pfr_player_id": ["a"]}), {
            "players": pl.DataFrame({"name": ["Someone"]}),
        })
