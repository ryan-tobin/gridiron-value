# Gridiron Value project status

Generated: 2026-09-16T02:09:09.124896+00:00

All values below are sourced from the supplied timestamped manifests.

## Milestones

| Stage | Status | Rows / units | Detail |
|---|---|---|---|
| Historical Pass+ | completed | 992 | 2017–2025; 329 qualifying display rows at 200 dropbacks |
| Chronological validation | completed | 9 | 9 seasons; first test week 5 |
| Current-season coverage | completed | 10 | 10/10 feeds available for season 2026 |
| Player identity | needs_review | 1492 | 1,489 resolved; 1 override; 2 unresolved |
| Player-game joins | completed | 1117 | 1 unattributed stats; 378 unmatched resolved snaps |
| Participation | completed | 1490 | 1,490 resolved participants; 2 unresolved |
| Position metrics | completed | 1117 | QB/RB/WR/TE metric tables plus all-position output |

## Coverage

| Kind | Name | Status | Rows | Match fraction | Detail |
|---|---|---|---|---|---|
| feed | pbp | available | 2756 |  |  |
| feed | player_stats | available | 1118 |  |  |
| feed | team_stats | available | 32 |  |  |
| feed | rosters | available | 2964 |  |  |
| feed | schedules | available | 272 |  |  |
| feed | snap_counts | available | 1492 |  |  |
| feed | ngs_passing | available | 64 |  |  |
| feed | ngs_rushing | available | 68 |  |  |
| feed | ngs_receiving | available | 146 |  |  |
| feed | ftn_charting | available | 2520 |  |  |
| identity | player_stats | evaluated |  | 1.0 | 1117/1117 distinct IDs matched |
| identity | snap_counts | evaluated |  | 0.8022788203753352 | 1197/1492 distinct IDs matched |
| identity | ngs_passing | evaluated |  | 1.0 | 32/32 distinct IDs matched |
| identity | ngs_rushing | evaluated |  | 1.0 | 34/34 distinct IDs matched |
| identity | ngs_receiving | evaluated |  | 1.0 | 73/73 distinct IDs matched |

## Chronological validation

| Season | Offense-only MSE | Offense + defense MSE | Defense MSE gain |
|---|---|---|---|
| 2017 | 2.535207 | 2.533610 | +0.001596 |
| 2018 | 2.578986 | 2.578649 | +0.000337 |
| 2019 | 2.601563 | 2.597689 | +0.003874 |
| 2020 | 2.487664 | 2.484884 | +0.002780 |
| 2021 | 2.530659 | 2.529669 | +0.000990 |
| 2022 | 2.434742 | 2.434564 | +0.000178 |
| 2023 | 2.568395 | 2.566879 | +0.001515 |
| 2024 | 2.477301 | 2.477170 | +0.000131 |
| 2025 | 2.546200 | 2.542593 | +0.003607 |

## Source manifests

| Stage | Manifest |
|---|---|
| coverage | reports/tables/coverage_20260915T231229504521Z/coverage_manifest.json |
| historical_analysis | reports/tables/historical_analysis_20260915T220616093983Z/analysis_manifest.json |
| chronological | reports/tables/chronological_20260915T222852538611Z/chronological_manifest.json |
| identity | reports/tables/player_identity_20260915T234030577889Z/identity_manifest.json |
| player_game | reports/tables/player_game_20260916T004308385374Z/player_game_manifest.json |
| participation | reports/tables/participation_20260916T010329368054Z/participation_manifest.json |
| position_metrics | reports/tables/position_metrics_20260916T011936835823Z/position_metrics_manifest.json |

