import tempfile
import unittest
from pathlib import Path

from gridiron_value import historical as h
from gridiron_value.historical_analysis import load_rows, rank_rows, summarize_players


def row(player_id, season, count, score):
    return {
        "dropback_player_id": player_id, "player": player_id,
        "season": season, "dropbacks": count, "games": 1,
        "pass_plus": score, "total_epa": 0.0,
        "opponent_adjusted_epa_per_dropback": 0.1,
        "opponent_adjusted_epa_above_average": 2.0,
    }


class HistoricalAnalysisTests(unittest.TestCase):
    def test_filter_preserves_scores_and_exact_ties(self):
        rows = [row("a", 2025, 1, 300), row("b", 2025, 200, 110),
                row("c", 2025, 300, 110), row("d", 2025, 400, 100)]
        ranked = rank_rows(rows, 200)
        self.assertEqual([r["rank"] for r in ranked], [1, 1, 3])
        self.assertEqual([r["pass_plus"] for r in ranked], [110, 110, 100])
        self.assertNotIn("rank", rows[1])

    def test_window_weights_all_observed_seasons(self):
        rows = [row("a", 2024, 100, 130), row("a", 2025, 300, 90)]
        result = summarize_players(rows, 200)[0]
        self.assertEqual(result["dropback_weighted_season_pass_plus"], 100)
        self.assertEqual(result["qualified_seasons"], 1)
        self.assertEqual(result["qualified_seasons_above_100"], 0)
        self.assertEqual(result["observed_seasons"], 2)

    def test_checksum_and_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            table = root / "players.csv"
            manifest = root / "manifest.json"
            record = row("a", 2025, 200, 110)
            h.save_csv(table, [record])
            h.save_json(manifest, {"status": "completed", "files": {
                "historical_pass_plus": h.record(root, table)}})
            self.assertEqual(len(load_rows(root, manifest)[0]), 1)
            h.save_csv(table, [record, record])
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                load_rows(root, manifest)
            h.save_json(manifest, {"status": "completed", "files": {
                "historical_pass_plus": h.record(root, table)}})
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_rows(root, manifest)


if __name__ == "__main__":
    unittest.main()
