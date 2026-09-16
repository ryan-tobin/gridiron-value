# Gridiron Value

Reproducible football analytics for understanding every player, every team, and every situation.

Gridiron Value is an experimental NFL analytics platform inspired by the way Baseball Savant combines comprehensive data, player profiles, context-adjusted metrics, historical records, and visual exploration in one system.

The project begins with **Pass+**, an opponent-adjusted production metric for eligible quarterback dropbacks. It is expanding into a complete football data layer with:

- position-specific player metrics;
- team and opponent profiles;
- historical player and team statistics;
- game-state and situational analysis;
- participation and workload tracking;
- current-season and eventually real-time reporting; and
- an operational foundation suitable for NFL gameday decision support.

**Current status: experimental research and data-platform prototype.** The research outputs are useful, inspectable, and reproducible, but the project is not yet an official player-evaluation standard, live production service, or causal measure of individual talent.

## Vision

The long-term goal is a football equivalent of a modern baseball analytics platform:

| Surface | Intended capability |
| --- | --- |
| Player profiles | A single page for each player with workload, production, efficiency, role, usage, context, peer comparisons, and historical trends |
| Team profiles | Offensive, defensive, special-teams, roster, matchup, and situational performance views |
| Position metrics | Metrics designed for the responsibilities of quarterbacks, backs, receivers, tight ends, offensive linemen, defensive linemen, linebackers, defensive backs, specialists, and returners |
| Historical explorer | Season, game-log, career-window, leaderboard, and era-aware comparisons |
| Situational analysis | Performance by down, distance, field position, score differential, time remaining, personnel, formation, coverage, pressure, red zone, goal line, two-minute, and other game states when the data supports them |
| Current-season center | A reproducible view of what is available now, what is incomplete, and how recently each feed was refreshed |
| Gameday tools | Low-latency, auditable views for matchup preparation, live decision support, substitution/workload monitoring, and postgame review |

The project will prioritize transparent definitions and evidence over a single opaque â€œplayer rating.â€ A number should be accompanied by its denominator, context, uncertainty, source data, and limitations.

## Current checkpoint

The current implementation has completed the first end-to-end data foundation for the 2026 season and the historical Pass+ research pipeline.

| Area | Current result |
| --- | --- |
| Historical Pass+ | 2017â€“2025; 180,348 eligible dropbacks; 992 player-seasons |
| Historical display analysis | 329 player-seasons qualify at a 200-dropback minimum |
| Chronological validation | Nine seasons evaluated with an expanding earlier-week training window; defense reduced test MSE in every season in the current run |
| 2026 feed coverage | 10/10 requested feeds available for the audited snapshot |
| 2026 identity resolution | 1,489 normal resolutions, 1 reviewed exact-record override, 2 unresolved snap rows |
| Production player-game table | 1,117 production-attached player-game rows |
| Participation table | 1,490 resolved participants, including 378 snap-only participants |
| Position metrics | 1,117 metric rows with QB, RB, WR, TE, and all-position outputs |
| Validation | The current branch has a passing automated test suite; run `python -m pytest -q` for the exact local count |

Generated status reports are written under `reports/tables/project_status_<timestamp>/`. Each report includes milestone tables, feed coverage, chronological validation, CSV summaries, a lightweight HTML dashboard, and a provenance manifest.

## What Pass+ measures

The first research question is:

> How productive was an offense on a player's eligible dropbacks after a model-based adjustment for defensive opponents?

The eligible-dropback cohort currently includes passes, sacks, and scrambles. It retains shared contributions from receivers, blockers, play design, and team context. It does **not** isolate quarterback talent, estimate wins above replacement, or make a causal claim about any individual player.

Pass+ combines:

- observed EPA per eligible dropback;
- EPA above the season's league reference;
- an opponent adjustment derived from held-out team effects;
- workload and accumulated adjusted production; and
- a conditional game-resampling diagnostic.

The model is a research instrument and presentation layer, not a claim that the transformed index contains information unavailable in its EPA and context inputs.

## Reading Pass+

For player-season adjusted rate $r_j$:

$$
\mathrm{Pass+}_j = 100 + 15\frac{r_j-\mu}{\sigma}
$$

The reference mean and standard deviation use eligible dropbacks as player-season weights:

$$
\mu = \frac{\sum_j N_j r_j}{\sum_j N_j},
\qquad
\sigma = \sqrt{\frac{\sum_j N_j(r_j-\mu)^2}{\sum_j N_j}}
$$

Here, $N_j$ is the player's eligible dropback count. The standard deviation describes player-season rates, not individual play outcomes.

| Pass+ | Interpretation |
| ---: | --- |
| 100 | At the play-weighted league reference |
| 115 | One weighted standard deviation above the reference |
| 85 | One weighted standard deviation below the reference |

**120 does not mean 20% better than average.** Pass+ is a standardized index, not a percentage-above-average measure such as baseball's wRC+.

Every eligible participant contributes to the reference, including players below a leaderboard's display threshold. Changing the display threshold does not change the scores. Scores are not capped, and every season has its own reference parameters.

Equal scores in different seasons do not establish equal absolute ability or identical scoring environments.

## Position-specific metrics

The current-season metric layer is deliberately transparent. Rates are null when their denominator is zero or unavailable, rather than silently treating missing production as zero. EPA-based rates retain shared player, blocking, scheme, and team contributions.

### Common workload and context fields

- offensive, defensive, and special-teams snaps;
- snap percentages where available;
- total participation snaps;
- season, week, game, team, opponent, position, and position group;
- production-attached versus snap-only participation status;
- touches, targets, and other role-specific denominators;
- source identity status and provenance; and
- available Next Gen Stats context.

### Quarterbacks

- attempts, completions, completion rate, passing yards, and passing yards per attempt;
- passing EPA and passing EPA per attempt;
- passing touchdowns and touchdown rate;
- interceptions and interception rate;
- sacks suffered and sack-rate proxy;
- dropbacks proxy, explicitly distinguished from the finalized Pass+ eligible-dropback definition;
- CPOE/expected completion context where available;
- time to throw, intended air yards, completed air yards, aggressiveness, and air-yards-to-sticks context where available; and
- game-state and opponent splits as the situational layer matures.

### Running backs and fullbacks

- carries, rushing yards, yards per carry, and rushing EPA;
- rushing EPA per carry;
- rushing touchdowns and explosive-run rate;
- expected rush yards and rush yards over expected from NGS where available;
- rush yards over expected per attempt;
- attempts against eight or more defenders and time to line of scrimmage where available;
- targets, receptions, receiving production, and receiving efficiency; and
- workload, snap, personnel, and game-state splits.

### Wide receivers and tight ends

- targets, receptions, catch rate, receiving yards, and yards per target;
- receiving EPA and receiving EPA per target;
- receiving touchdowns;
- yards after catch and YAC per reception;
- explosive-reception rate;
- target share, air-yards share, and WOPR where available;
- average separation, average cushion, intended air yards, expected YAC, and YAC above expectation from NGS where available; and
- alignment, coverage, personnel, route, and situation splits as charting support expands.

### Offensive line

The participation foundation now preserves offensive-line snap records, including players who do not receive standard skill-position production rows. The next OL layer will focus on:

- offensive snap share and continuity;
- pass-blocking and run-blocking context when a licensed or sufficiently detailed source is available;
- pressure, sack, hit, and hurry responsibility with explicit attribution rules;
- penalties and penalty impact;
- position, alignment, and replacement/continuity effects; and
- unit-level rather than falsely precise individual credit where the data cannot support individual attribution.

### Defensive line, linebackers, and defensive backs

The player-game and participation layers preserve defensive players even when standard production feeds are incomplete. Planned defensive metrics include:

- defensive snap share and role;
- tackles, tackles for loss, sacks, pressures, hits, hurries, and disruption rates;
- forced fumbles, pass breakups, interceptions, and coverage outcomes;
- missed tackles and penalties when consistently sourced;
- target, catch, separation, and coverage context where charting supports it;
- pressure and coverage situation splits; and
- team- and scheme-aware context rather than assigning all defensive outcomes to one player.

### Specialists and returners

Planned specialist coverage includes:

- field-goal and extra-point accuracy by distance and situation;
- punting distance, hang time, net yards, inside-20 rate, touchbacks, and return context;
- kickoff and punt return workload, yards, explosive returns, and touchdown rate;
- special-teams snap share; and
- game-state leverage and field-position value.

The project will not publish a metric merely because a source column exists. Each metric needs a documented denominator, attribution rule, coverage assessment, and validation test.

## Data architecture

The pipeline is organized as explicit, verifiable layers:

```text
nflverse snapshots and source feeds
             |
             v
     coverage audit
             |
             v
 explicit player identity resolution
             |
       +-----+-----+
       |           |
       v           v
 production     participation
 player-game    player-game
       |           |
       +-----+-----+
             v
 position-specific metrics
             |
       +-----+----------------+
       |                      |
       v                      v
 historical/context         current-season
 research outputs            status and profiles
             |
             v
 situational and gameday services
```

The architecture intentionally separates:

1. **Availability** - what source rows were retrieved.
2. **Identity** - which source records can be linked with explicit evidence.
3. **Participation** - who was on the field, including snap-only players.
4. **Production** - what standard statistics and EPA-based fields exist.
5. **Metrics** - transparent transformations with denominators and null rules.
6. **Context** - opponent, game state, personnel, alignment, and matchup information.
7. **Presentation** - reports, player profiles, dashboards, and eventual services.

Missing production is not automatically interpreted as zero production. Unresolved identity rows remain preserved for review.

## Current-season workflow

Run the commands from the repository root with the project environment active. Each stage prints a timestamped manifest path. Replace placeholders with paths from your own run; they are placeholders, not literal commands.

### 1. Audit source coverage

```bash
python -m gridiron_value.coverage_audit \
  --season 2026

COVERAGE_MANIFEST="reports/tables/coverage_REPLACE_WITH_RUN_ID/coverage_manifest.json"
```

The coverage audit records feed availability, week counts, schema fields, null/nonfinite values, identifier matches, and scored schedule games missing from PBP.

### 2. Resolve player identity

```bash
python -m gridiron_value.player_identity \
  --coverage-manifest "$COVERAGE_MANIFEST" \
  --reuse-identity-manifest reports/tables/player_identity_PREVIOUS_RUN/identity_manifest.json \
  --overrides docs/identity_overrides_2026.json

IDENTITY_MANIFEST="reports/tables/player_identity_REPLACE_WITH_RUN_ID/identity_manifest.json"
```

Identity resolution uses explicit mappings. It preserves `missing_pfr_id`, `unmapped`, `conflict`, `resolved`, and reviewed `resolved_override` statuses. Manual overrides must identify the exact record, selected candidate, reason, and evidence.

### 3. Build player-game production joins

```bash
python -m gridiron_value.player_game \
  --coverage-manifest "$COVERAGE_MANIFEST" \
  --identity-manifest "$IDENTITY_MANIFEST"

PLAYER_GAME_MANIFEST="reports/tables/player_game_REPLACE_WITH_RUN_ID/player_game_manifest.json"
```

This stage joins player statistics, resolved snaps, and available Next Gen Stats fields. Missing snaps remain null rather than being converted to zero.

### 4. Build participation records

```bash
python -m gridiron_value.participation \
  --identity-manifest "$IDENTITY_MANIFEST" \
  --player-game-manifest "$PLAYER_GAME_MANIFEST"

PARTICIPATION_MANIFEST="reports/tables/participation_REPLACE_WITH_RUN_ID/participation_manifest.json"
```

Participation records include snap-only players. They are the foundation for offensive-line, defensive, and special-teams profiles where conventional player-stat feeds may omit a player.

### 5. Calculate position metrics

```bash
python -m gridiron_value.position_metrics \
  --player-game-manifest "$PLAYER_GAME_MANIFEST" \
  --participation-manifest "$PARTICIPATION_MANIFEST"

POSITION_METRICS_MANIFEST="reports/tables/position_metrics_REPLACE_WITH_RUN_ID/position_metrics_manifest.json"
```

The current production output contains all-position metrics plus QB, RB, WR, and TE tables. Additional position groups will be added only after their attribution and denominator contracts are defined.

### 6. Generate the project status report

```bash
python -m gridiron_value.project_status \
  --historical-analysis-manifest reports/tables/historical_analysis_REPLACE_WITH_RUN_ID/analysis_manifest.json \
  --chronological-manifest reports/tables/chronological_REPLACE_WITH_RUN_ID/chronological_manifest.json \
  --coverage-manifest "$COVERAGE_MANIFEST" \
  --identity-manifest "$IDENTITY_MANIFEST" \
  --player-game-manifest "$PLAYER_GAME_MANIFEST" \
  --participation-manifest "$PARTICIPATION_MANIFEST" \
  --position-metrics-manifest "$POSITION_METRICS_MANIFEST"
```

The status run writes:

- `report.md` - GitHub-readable milestone, coverage, and validation tables;
- `dashboard.html` - a local visual view;
- `milestones.csv`;
- `coverage_summary.csv`;
- `chronological_validation.csv`; and
- `project_status_manifest.json` with source and output checksums.

The reproducible sequence is **generate â†’ inspect â†’ commit the verified checkpoint**.

## Historical Pass+ workflow

The historical pipeline remains the research foundation for Pass+.

### Acquire and inspect raw seasons

```bash
python -m gridiron_value.data --season 2025

RAW_MANIFEST="data/metadata/REPLACE_WITH_RAW_RUN_ID/manifest.json"

python -m gridiron_value.inspect_dropbacks \
  --manifest "$RAW_MANIFEST"
```

### Build the eligible cohort

```bash
python -m gridiron_value.cohort \
  --manifest "$RAW_MANIFEST" \
  --min-dropbacks 100

COHORT_MANIFEST="data/metadata/REPLACE_WITH_COHORT_RUN_ID/cohort_manifest.json"
```

### Estimate schedule corrections

```bash
python -m gridiron_value.schedule \
  --cohort-manifest "$COHORT_MANIFEST" \
  --min-dropbacks 100

SCHEDULE_MANIFEST="data/metadata/REPLACE_WITH_SCHEDULE_RUN_ID/schedule_manifest.json"
```

The frozen candidate uses an offense penalty of 100 and a defense penalty of 1,000.

### Calculate player-game variability

```bash
python -m gridiron_value.variability \
  --schedule-manifest "$SCHEDULE_MANIFEST" \
  --min-dropbacks 100 \
  --resamples 10000 \
  --seed 2026

VARIABILITY_MANIFEST="data/metadata/REPLACE_WITH_VARIABILITY_RUN_ID/variability_manifest.json"
```

### Build the Pass+ presentation

```bash
python -m gridiron_value.reporting \
  --schedule-manifest "$SCHEDULE_MANIFEST" \
  --variability-manifest "$VARIABILITY_MANIFEST" \
  --min-dropbacks 100
```

The schedule and variability manifests must refer to the same scored-play snapshot and compatible run settings.

## Research methodology

### Eligible dropbacks

The cohort applies these rules sequentially:

1. Keep regular-season rows.
2. Keep rows with `qb_dropback == 1`.
3. Exclude two-point attempts.
4. Exclude spikes and kneel-downs.
5. Keep supported play types, currently `pass` and `run`.
6. Require finite EPA.

Unknown eligibility conditions fail the corresponding rule. Exclusion counts are sequential: a row removed by one rule is not counted again later.

Penalty flags alone do not exclude a row. A row must still satisfy the other eligibility conditions; `no_play` rows fail the supported-play-type rule. This policy remains a documented sensitivity-analysis candidate.

The pipeline stops on missing or duplicate play keys, conflicting player identifiers, or unidentified eligible dropbacks.

### Player attribution

`passer_id` is the primary dropback actor identifier. If it is missing, `rusher_player_id` is used only when `qb_scramble == 1`. Each fallback is recorded.

The resulting field is `dropback_player_id`. Eligible participants are retained regardless of roster position, so unusual dropback actors are not automatically reassigned to a team's usual quarterback.

### Opponent model

The current Pass+ research model is additive:

$$
\mathrm{EPA}_i = \beta_0 + \alpha_{\mathrm{offense}(i)} + \delta_{\mathrm{defense}(i)} + \epsilon_i
$$

| Setting | Value |
| --- | ---: |
| Offensive-team penalty | 100 |
| Defensive-team penalty | 1,000 |
| Evaluation folds | 5 |
| Grouping unit | Entire game |

Preprocessing and regression are fitted inside each training fold. The defensive contribution is extracted without using the player's own game when computing schedule corrections.

### Schedule correction

Let $d_i$ be the held-out defensive contribution and $\bar d$ its average over all eligible season dropbacks:

$$
c_i = \bar d - d_i,
\qquad
\mathrm{Adjusted\ EPA}_i = \mathrm{EPA}_i + c_i
$$

A positive correction credits production against defenses estimated to suppress EPA. A negative correction discounts production against defenses estimated to allow more EPA.

The correction is centered on the complete season exposure mix. Corrections sum to zero within numerical precision, preserving league-wide total EPA and its mean.

### Chronological evaluation

The chronological module evaluates an expanding earlier-week training window within each season. Weeks before the selected cutoff are training only; later weeks are test windows. The model resets each season.

This is a retrospective backtest using previously inspected seasons, not independent prospective validation. It does not establish live-feed availability, forecasting performance, causal defensive effects, replacement value, or individual talent attribution.

### Resampling

The project uses paired game-level resampling for model comparisons and player-game resampling for conditional variability. Player variability recalculates a rate as a ratio of resampled EPA totals to resampled dropbacks rather than averaging game rates.

Ranges are conditional diagnostics. They are not confidence intervals for isolated talent, do not include complete model-refitting uncertainty, and do not constitute pairwise ranking tests.

## Research results and interpretation

The original equal-penalty model did not replicate consistently across the inspected seasons. The current frozen candidate uses stronger defensive shrinkage. The chronological run produced a modest defensive MSE improvement in every season from 2017 through 2025 in the current checkpoint.

These results support continued investigation of opponent adjustment. They do not prove that the model has isolated defensive quality, predicted future games, or separated nearby players meaningfully.

The historical analysis module produces:

- season leaderboards;
- all observed player-season histories;
- multi-season player summaries;
- threshold sensitivity tables;
- Markdown reports; and
- manifests linking each table to the source historical run.

## Project status and reporting

The project has two complementary records:

- `docs/progress.md` - the durable explanation of decisions, findings, and workflow;
- `reports/tables/project_status_<timestamp>/` - generated status snapshots tied to exact manifests.

Generated reports should be inspected before they are committed as checkpoints. Do not manually edit generated tables to correct a number; fix the producing stage, rerun it, and preserve the new manifest.

## Gameday product direction

The research pipeline is being designed so it can eventually support NFL-style operational use, but that requires additional engineering beyond the current reports.

### Preparation and pregame

- opponent tendencies and matchup profiles;
- player availability, roster, depth-chart, and role changes;
- workload and participation expectations;
- historical performance under comparable conditions;
- uncertainty and sample-size warnings; and
- exportable opponent-preparation reports.

### Live game

- current score, down, distance, clock, field position, possession, and win-probability context;
- live player workload and participation;
- opponent tendency updates as new plays arrive;
- situational performance and matchup alerts;
- explicit data freshness and feed-health indicators; and
- human-readable explanations for every recommendation or alert.

### Postgame

- corrected final statistics;
- play and player audit trails;
- role and workload changes;
- opponent-adjusted review;
- coaching and roster decision support; and
- versioned data revisions when upstream feeds change.

Before gameday use, the system needs latency measurements, feed failure behavior, late-stat correction handling, role-based access, observability, reproducible deployment, and formal validation against operational decisions.

## Roadmap

### Completed foundations

- frozen historical Pass+ specification;
- historical 2017â€“2025 analysis;
- chronological within-season validation;
- current-season feed coverage audit;
- explicit player identity registry and reviewed overrides;
- production player-game joins;
- participation records that retain snap-only players;
- first position-specific metric tables; and
- reproducible cross-stage project status dashboard.

### Next milestones

1. **Player profiles** - generate a Baseball-Savant-style profile from position metrics, participation, historical data, workload, percentiles, and peer context.
2. **Team profiles** - add team identity, roster, opponent, unit, and matchup views.
3. **Historical explorer** - expose player-season histories, game logs, leaderboards, and era-aware comparisons.
4. **Situational metrics** - define game-state, down-distance, field-position, personnel, alignment, pressure, and coverage dimensions with explicit sample rules.
5. **Defensive, OL, and special-teams metrics** - use participation and charting sources without overclaiming individual attribution.
6. **Current-season refresh orchestration** - add repeatable updates, freshness checks, feed-health status, and correction-aware reruns.
7. **Interactive application** - build the exploration layer after the metric contracts stabilize.
8. **Gameday readiness** - benchmark latency, reliability, explainability, permissions, and operational workflows before any live decision-support claim.

## Limitations and guardrails

The current project has material limits:

- team effects do not isolate an individual quarterback from supporting players or play design;
- additive models do not represent every matchup interaction or change in team strength;
- many position-specific outcomes require richer charting or tracking data than is currently available;
- snap counts establish participation, not individual blocking, coverage, route, or assignment quality;
- missing production rows must not be interpreted as zero production;
- identity conflicts and unmapped records remain visible rather than being resolved by name guesses;
- current-season snapshots are not certified live feeds;
- upstream data can be revised after initial retrieval;
- resampling omits some dependence and model-refitting uncertainty;
- small predictive gains do not establish causal schedule corrections;
- standardized scores are not talent-shrinkage estimates;
- retrospective testing is not future-game forecasting;
- Pass+ is not wins above replacement; and
- no output should be used for a high-stakes roster, medical, employment, or financial decision without independent review.

The project will prefer an honest null, an explicit â€œnot evaluatedâ€ status, or a review queue over a precise-looking metric unsupported by its source data.

## Repository organization

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, and development configuration |
| `src/gridiron_value/data.py` | Acquisition, schema audits, checksums, and shared JSON helpers |
| `src/gridiron_value/inspect_dropbacks.py` | Player identity and dropback-category diagnostics |
| `src/gridiron_value/cohort.py` | Eligibility rules and player attribution |
| `src/gridiron_value/baseline.py` | Descriptive rates and EPA above league average |
| `src/gridiron_value/adjustment.py` | Team models and held-out evaluation |
| `src/gridiron_value/uncertainty.py` | Conditional paired game-error resampling |
| `src/gridiron_value/replicate.py` | Fixed-specification historical experiments |
| `src/gridiron_value/shrinkage.py` | Defensive-penalty comparison |
| `src/gridiron_value/schedule.py` | Defensive corrections and adjusted production |
| `src/gridiron_value/variability.py` | Conditional player-game resampling |
| `src/gridiron_value/reporting.py` | Pass+ presentation and leaderboards |
| `src/gridiron_value/historical_analysis.py` | Historical season, threshold, and player-window analysis |
| `src/gridiron_value/chronological.py` | Expanding-window within-season validation |
| `src/gridiron_value/coverage_audit.py` | Current-season feed and identifier coverage |
| `src/gridiron_value/player_identity.py` | Explicit PFR/GSIS identity resolution and reviewed overrides |
| `src/gridiron_value/player_game.py` | Production player-game joins |
| `src/gridiron_value/participation.py` | Snap-based participation, including snap-only players |
| `src/gridiron_value/position_metrics.py` | Transparent role-specific metric derivation |
| `src/gridiron_value/project_status.py` | Cross-stage progress reports and dashboard |
| `tests/` | Attribution, accounting, leakage, transformation, and reporting tests |
| `docs/methodology.md` | Definitions, assumptions, and research design |
| `docs/progress.md` | Decisions, findings, and experiment history |
| `docs/identity_overrides_2026.json` | Reviewed exact-record identity overrides |
| `docs/results/` | Optional curated reference outputs committed with checkpoints |
| `data/raw/` | Unfiltered source snapshots |
| `data/interim/` | Intermediate data and held-out predictions |
| `data/processed/` | Eligible and scored datasets |
| `data/metadata/` | Provenance, parameters, and experiment manifests |
| `reports/tables/` | Generated leaderboards, diagnostics, and status reports |
| `reports/figures/` | Generated visualizations |

## Installation

Use Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

In VS Code, select the interpreter inside `.venv` using **Python: Select Interpreter**.

Dependencies are declared in `pyproject.toml`. The core workflow uses nflreadpy, Polars, NumPy, SciPy, scikit-learn, and Matplotlib. Development dependencies include pytest, Ruff, and ipykernel.

## Reproducibility and testing

Every important stage should record:

- the source snapshot and retrieval context;
- row counts and schemas;
- settings, thresholds, seeds, and package versions;
- local checksums;
- generated files; and
- limitations or statuses that affect interpretation.

Downstream stages verify the source files they consume. Seeds make resampling repeatable within the recorded environment.

Preserve the underlying data files as well as their manifests when exact future reproduction matters. A checksum cannot recover the original bytes after an upstream dataset changes. Package-version records also do not substitute for a fully locked environment.

Run the quality checks from the repository root:

```bash
python -m pip check
python -m ruff format src/gridiron_value tests
python -m ruff check src/gridiron_value tests
python -m pytest -q
```

Passing implementation tests is not evidence that the statistical model is sufficient for every intended use. Model evaluation, data coverage, accounting checks, and operational readiness answer different questions.

## Data sources and prior work

- [nflverse](https://github.com/nflverse) - data and tools supporting the pipeline.
- [nflreadpy](https://github.com/nflverse/nflreadpy) - Python access to nflverse.
- [nflfastR](https://nflfastr.com/) - play-by-play data and expected-points models.
- [nflWAR: A Reproducible Method for Offensive Player Evaluation in Football](https://arxiv.org/abs/1802.00998) - prior work on reproducible football player-value estimation.
- [scikit-learn Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html) - team-effect model implementation.
- [scikit-learn ColumnTransformer](https://scikit-learn.org/stable/modules/generated/sklearn.compose.ColumnTransformer.html) - fold-contained preprocessing.
- [scikit-learn GroupKFold](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html) - game-grouped evaluation support.
- [SciPy bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html) - paired resampling implementation.

Downloaded datasets and dependencies retain their respective terms and attribution requirements. This README does not assign a license to third-party data or substitute for a project `LICENSE` file.