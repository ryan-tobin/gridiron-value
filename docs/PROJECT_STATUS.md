# Gridiron Value — verified project status

Verified September 16, 2026 against the existing `/workspaces/gridiron-value` Codespaces checkout, starting at commit `cc0865d3964f21d7322706de3832e2cd9b531fe7` on `main`, including its uncommitted source and local snapshots. This is an experimental research and offline analytics product. Live ingestion, current-season Pass+ scoring, defensive efficiency, and gameday readiness are **not implemented**.

## Verified progress

| Area | Evidence and present capability |
| --- | --- |
| Historical Pass+ | 2017–2025, 180,348 eligible dropbacks, 992 player-seasons; 329 at the provisional 200-dropback display minimum. Season-specific references include all eligible actors. |
| Chronological validation | Nine seasons, 122 expanding weekly test windows starting at Week 5; recorded training dates precede test dates in every window. Offense-plus-defense has lower test MSE than offense-only in each saved season. This is a retrospective backtest on inspected seasons and revised EPA, not independent prospective validation. |
| Current-season coverage | All 10 requested feeds have rows in the September 15 snapshot. PBP has 2,756 rows across 16 Week 1 regular-season games. Availability does not certify completeness, timeliness, or final-game status. |
| Identity | 1,492 snap rows: 1,489 normally resolved, one exact-record reviewed override, two unmapped. The override is not a global PFR-ID rewrite. |
| Production and participation | 1,117 production player-game rows; 1,490 resolved participation rows, including 378 snap-only records. One anonymous production row remains separately preserved. |
| Position metrics | 1,117 production-attached rows and QB/RB/WR/TE exports. Snap-only players are preserved in participation and profiles rather than fabricated production rows. |
| Player profiles | 1,495 REG profiles in the latest full run: 1,112 production-and-participation keys, 378 snap-only keys, and five production-only keys. This snapshot has one observed game per profile. Profiles reproduce exactly from the consumed metric and participation inputs. |
| Leaderboards | QB/RB/WR/TE comparisons, metric-specific qualification, competition ties and peer percentile bars. All 2,033 comparison records reproduce exactly from saved profiles and settings; 529 qualified, 1,110 below minimum, 390 incomplete, four small-cohort records. These are player/metric records, not distinct player counts. 1,070 profiles have no supported comparison group. |
| Status reports | Existing `project_status.py` generates milestone tables, coverage and chronological tables, Markdown and HTML; those older reports precede the new dropback milestone. |
| New current-season foundation | `current_dropbacks.py` consumes an explicit coverage manifest offline, verifies its PBP checksum, reuses the versioned historical REG cohort, and writes eligible plays, event/identity/exclusion audits, player/team totals, and four independent situational tables with checksummed provenance. No model or Pass+ index is fitted. |

The new preserved-snapshot run contains **1,140 eligible dropbacks across 16 games and 37 dropback actors**. The existing cohort excludes four two-point attempts after selecting 1,144 flagged dropbacks. Eligible events include 72 sacks and 75 scrambles. Down, yards to go, field position and score differential are available on all 1,140 eligible plays in this particular snapshot. The implementation still preserves an explicit unknown bucket for absent, invalid or missing context on other snapshots.

## Data scope and interpretation

The coverage snapshot contains 1,118 source player-stat rows, 32 team-stat rows, 1,492 snap rows and 2,520 FTN rows. NGS passing/rushing/receiving have 64/68/146 rows including Week 0 aggregate rows; player-game joins exclude those aggregates. Rosters span Weeks 1 and 2. The 272-row schedule includes future games and is not a count of completed games. FTN availability has not established a validated charting join or pressure/coverage opportunity denominator.

Production and participation are separate populations. Missing statistics are not zero. Totals and rates in profiles describe observed games; completeness for those games does not prove full schedule coverage. Rates use paired numerator and denominator observations and ratios of sums. NGS and share values remain game-level context because season weighting is not established.

Regular season, postseason and preseason must remain separate. Existing profiles and leaderboards support separate REG/POST selections; the new dropback adapter deliberately supports REG only through the existing cohort contract. It does not silently generalize historical Pass+ to postseason.

Percentiles describe qualified snapshot peers, not isolated talent or a certified NFL-wide population. The current thresholds (20 attempts, 20 attempts plus sacks, 10 carries, five targets, five receptions, five peers) are provisional display filters. Snapshot EPA retains teammate, scheme and team contributions. Total defensive snaps cannot substitute for pass-rush or coverage opportunities.

## Verification and lineage

The audit traversed **58 linked manifests** from the selected runs and checked **3,194 distinct path/checksum pairs**: 3,192 matched and two recorded source-code versions differed. No missing or mismatched data, report, or manifest artifact was found in that traversal. This count covers explicit `{path, sha256}` records, not every file in the repository or every bare hash field. Separately checked historical stage run IDs and input hashes agree for all nine seasons. Profile/participation/player-game identity and source links agree. Profile summaries and leaderboard comparisons were recomputed in memory and match saved results.

Source history needs care:

- The earlier identity run's recorded `player_identity.py` differs from today's override-capable file; the later identity run records the current version.
- The position-metrics run records a different source hash from the current pre-existing working-tree file. Its tracked diff removes the module docstring and final newline. Saved output checksums still match.
- A separate check of the latest profile manifest's scalar `code_sha256` differs from current `player_profile.py`. The leaderboard manifest's current profile-code hash and leaderboard-code hash match. Saved profiles nevertheless reproduce exactly. Preserve the producing code revision before claiming byte-for-byte source reproducibility.

Checksums establish the identity of preserved local bytes, not the correctness of upstream football events. The audit did not refit all historical models, replay all acquisition steps, reconstruct historical upstream EPA availability, or certify live freshness.

The new adapter records its coverage manifest, raw snapshot, cohort version, code hashes, Polars version, source retrieval timestamp, observed weeks, definitions and every output hash. Older snapshots and manifests are not rewritten.

## Validation status

Before implementation: `python -m pytest -q` passed **95 tests** in Codespaces Python 3.14.2; `python -m pip check` reported no broken requirements. The new focused suite passed **eight additional tests**, including checksums, wrong-season rejection, invalid keys/context, REG-only eligibility, scramble identity recovery, split conservation, play weighting and output provenance.

Existing whole-repository Ruff checks report **39 lint findings**; format check reports **18 files needing formatting and 22 already formatted** before this change. They were not auto-fixed. After implementation, the complete suite passed **103 tests in 2.80 seconds**. The two new Python files pass their own lint and formatting checks. The generated run's output/input/code hashes were verified, and every player/team situation partition conserves its parent count and EPA total within numerical tolerance. Passing tests do not establish metric validity or operational readiness.

## Remaining gaps and concrete inconsistencies

1. **Passing EPA per attempt remains withheld.** Profiles and leaderboards correctly omit it, but `position_metrics.py` still calculates `pass_epa_per_attempt`, its saved Parquet contains that column, an existing test asserts its arithmetic, and README advertises it. The new adapter does not expose it. Arithmetic correctness does not validate numerator eligibility. Audit the player-stat EPA cohort and official attempt/sack/scramble treatment before any release decision; correct/deprecate the upstream field through a new version, preserving existing artifacts.
2. **Current-season Pass+ is not yet a defined scoring mode.** Historical schedule correction is game-held-out and retrospectively centered over the full season. Chronological validation predicts later weeks, resets each season and starts testing at Week 5. Those are different estimands. Week 1 data alone cannot run that established evaluation policy. Early-season priors, unseen teams, minimum training support, reference population, centering and revision semantics require an explicit contract.
3. **Situations now have a basic descriptive layer only.** Down, distance, field position and score-state splits are available. No pressure, coverage, personnel, route, lineup, clock-phase or multidimensional filtering product is established. New splits are neither opponent-adjusted nor uncertainty-qualified.
4. **Historical/non-QB integration is incomplete.** Historical Pass+ tables exist separately from current profile game logs; a comprehensive historical multi-position/team explorer does not yet exist. New team summaries describe offensive eligible-dropback production only.
5. **Join contracts need expansion before broader seasons.** For example, NGS joins currently use season/week/team/player without an explicit season-type key. Existing current data are Week 1 REG; that is not proof of safe mixed REG/POST ingestion. Identity override behavior needs dedicated regression coverage beyond basic mapping tests.
6. **Documentation and release reproducibility lag implementation.** README still lists profiles as a next milestone; profile docs still describe qualifications/percentiles as future work even though the separate leaderboard stage exists. Several required source/test files and generated datasets are untracked. A clean GitHub clone alone cannot reproduce this checkout. Preserve a versioned source checkpoint and snapshot inventory; do not bulk-stage generated datasets.
7. **Product and operations remain future work.** Searchable offline HTML is useful but has not received analyst usability validation or comprehensive visual/accessibility QA here. No correction-aware ingestion service, latency benchmark, production deployment, monitoring or operational decision validation has been established.

## Prioritized roadmap

| Priority | Bounded milestone | Exit criterion |
| --- | --- | --- |
| 1 — implemented here | Current-season verified REG cohort and basic situational summaries | Explicit snapshot input; shared historical eligibility; audited exclusions/identities/events; player/team partitions conserve counts/EPA; unknown context retained; immutable outputs; focused tests and a real-snapshot run. |
| 2 — next | Player-stat numerator/denominator reconciliation | Compare official attempts, sacks and supplied passing EPA with keyed PBP categories per player/game/team, including penalties, scrambles, spikes, two-point attempts and unattributed rows. Explain differences without zero-filling or forcing equality. Keep EPA/attempt withheld until its contract is approved by evidence. |
| 3 | Current-season Pass+ policy and offline scoring | Specify descriptive as-of versus strictly earlier-week scoring, early-season not-evaluated behavior or validated prior, frozen penalties, reference/centering and revision policy. Reuse verified cohorts; assert no future/test-outcome leakage; test one-week/unseen-team cases; retain raw EPA and workload beside scores. |
| 4 | Profile/team situational explorer | Expose the verified summaries with explicit snapshot/season/type labels, sample counts, missing coverage and small-sample cautions. Add clock phases and multidimensional filters only with tested definitions. Join historical records under explicit IDs and season-specific references. |
| 5 | Reproducible refresh and release | Pin source/environment, preserve manifests, reconcile late revisions, add feed-health checks, finish lint cleanup as a separate change, and test safe reruns/failures. Refresh creates new runs rather than replacing old evidence. |
| 6 | Richer position analytics and gameday evaluation | Validate licensed/available opportunity and attribution data for OL/defense/specialists; then measure latency, reliability, access controls and analyst decision usefulness. Gameday readiness requires evidence, not merely a dashboard. |

## Selected evidence manifests

Paths are relative to the repository root. Timestamped local artifacts may not be on GitHub.

- coverage: `reports/tables/coverage_20260915T231229504521Z/coverage_manifest.json`
- identity: `reports/tables/player_identity_20260915T234030577889Z/identity_manifest.json`
- player_game: `reports/tables/player_game_20260916T004308385374Z/player_game_manifest.json`
- participation: `reports/tables/participation_20260916T010329368054Z/participation_manifest.json`
- metrics: `reports/tables/position_metrics_20260916T011936835823Z/position_metrics_manifest.json`
- profiles: `reports/tables/player_profiles_20260916T043855599727Z/profile_manifest.json`
- leaderboards: `reports/tables/leaderboards_20260916T045425106021Z/leaderboard_manifest.json`
- chronological: `reports/tables/chronological_20260915T222852538611Z/chronological_manifest.json`
- status: `reports/tables/project_status_20260916T020909123901Z/project_status_manifest.json`
- historical: `data/metadata/20260915T175338521414Z/historical_manifest.json`
- historical_analysis: `reports/tables/historical_analysis_20260915T220421516599Z/analysis_manifest.json`
- New dropback run: `reports/tables/current_dropbacks_20260916T051825937228Z/current_dropbacks_manifest.json`

Run instructions and output definitions: [current_dropbacks.md](current_dropbacks.md).
