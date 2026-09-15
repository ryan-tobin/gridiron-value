# Gridiron Value

Reproducible research into NFL offensive production, inspired by baseball’s use of context-adjusted statistics and value above average.

The first implementation, **Pass+**, presents opponent-adjusted production on a player’s eligible dropbacks. It pairs an efficiency index with accumulated value, workload, and a game-resampling diagnostic.

**Status: experimental research prototype.** The implementation and results documented here reflect work completed through September 15, 2026. Pass+ is a working name, and v0.1 describes the current research specification rather than a finalized player-evaluation standard.

## What the project measures

The initial question is:

> How productive was an offense on a player’s dropbacks, after a model-based adjustment for defensive opponents?

Eligible dropbacks include passes, sacks, and scrambles. The metric retains contributions from receivers, blockers, and play design. It does **not** isolate quarterback talent or estimate wins above replacement.

The project builds on existing EPA and football-analytics research. The standardized index is a presentation transform, not a claim of new predictive information or an entirely new statistical concept.

| Output | Meaning |
| --- | --- |
| EPA per dropback | Observed production efficiency in the eligible cohort |
| EPA above league average | Accumulated production relative to the season’s average eligible dropback |
| Schedule adjustment | Estimated defensive-opponent correction, available per play, per dropback, and in total |
| Opponent-adjusted EPA per dropback | Observed efficiency plus the schedule correction |
| Opponent-adjusted EPA above average | Accumulated adjusted production relative to the league reference |
| **Pass+** | A standardized presentation of adjusted efficiency, centered at 100 |
| Game-resampling range | Conditional variability in rates when a player’s observed games are resampled |

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

Here, $N_j$ is the player’s eligible dropback count. The standard deviation describes **player-season rates**, not individual play outcomes.

| Pass+ | Interpretation |
| ---: | --- |
| 100 | At the play-weighted league reference |
| 115 | One weighted standard deviation above the reference |
| 85 | One weighted standard deviation below the reference |

**120 does not mean 20% better than average.** Pass+ is a standardized index, not a percentage-above-average measure like baseball’s wRC+.

Every eligible participant contributes to the reference, including players below the leaderboard’s display threshold. Changing the display threshold does not change scores. Scores are not capped, and each season has its own reference parameters.

The index describes standing within a season’s production distribution. Equal scores in different seasons do not establish equal absolute ability or identical scoring environments.

## Methodology

### 1. Data acquisition

Play-by-play data is loaded through `nflreadpy` from nflverse. Each download creates a timestamped, unfiltered snapshot of the returned dataframe, serialized locally as Parquet.

Acquisition records include source information, retrieval time, row counts, schema, package versions, and local file checksums. The local Parquet checksum identifies the saved file; it does not identify the original upstream file’s bytes.

### 2. Eligible dropbacks

The cohort applies these rules sequentially:

1. Keep regular-season rows.
2. Keep rows with `qb_dropback == 1`.
3. Exclude two-point attempts.
4. Exclude spikes and kneel-downs.
5. Keep supported play types, `pass` and `run`.
6. Require finite EPA.

Unknown eligibility conditions fail the corresponding rule. Exclusion counts are sequential: a row removed by one rule is not counted again by later rules.

Penalty flags alone do not exclude a row. A row must still satisfy the other eligibility conditions; `no_play` rows fail the supported-play-type rule. This policy remains a candidate for sensitivity analysis.

The pipeline stops on missing or duplicate play keys, conflicting player identifiers, or unidentified eligible dropbacks.

### 3. Player attribution

Use `passer_id` as the primary dropback actor identifier. If it is missing, use `rusher_player_id` **only when `qb_scramble == 1`**. Record each fallback.

The resulting field is `dropback_player_id`. Eligible participants are retained regardless of roster position, so unusual dropback actors are not automatically reassigned to a team’s usual quarterback.

The 2025 audit illustrates why this matters: all 1,089 flagged regular-season dropbacks missing `passer_player_id` were scrambles. The broader `passer_id` covered 1,087 of them, and two additional actors were recovered through the scramble-specific runner fallback.

### 4. Descriptive baseline

Calculate EPA totals and rates using the same eligible plays:

$$
\mathrm{EPA\ above\ average}_j
=\sum_{i\in j}\mathrm{EPA}_i-N_j\overline{\mathrm{EPA}}_{\mathrm{season}}
$$

The league reference weights plays equally. It is not an unweighted average of player averages. Player totals are grouped by season and stable player ID, combining production across teams when a player changes teams.

### 5. Opponent model

Fit an additive team model:

$$
\mathrm{EPA}_i=\beta_0+\alpha_{\mathrm{offense}(i)}
+\delta_{\mathrm{defense}(i)}+\epsilon_i
$$

The current candidate uses separate ridge penalties:

| Setting | Value |
| --- | ---: |
| Offensive-team penalty | 100 |
| Defensive-team penalty | 1,000 |
| Evaluation folds | 5 |
| Grouping unit | Entire game |

Stronger defensive shrinkage was selected after the original equal-penalty specification failed to replicate consistently. Feature-group scaling implements the separate penalties while preserving EPA units in extracted defensive contributions.

Three prediction references are compared: training-set league mean, offense-only, and offense-plus-defense. Preprocessing and regression are fitted inside each training fold.

This is **retrospective within-season evaluation**. Training folds can include games played later than the held-out game. Existing upstream EPA values are treated as a fixed target. The experiment does not establish chronological forecasting performance.

### 6. Schedule correction

For each play, extract only the defensive contribution from a model trained without that play’s game. Do not subtract the complete prediction, which would also remove offensive production.

Let $d_i$ be that held-out defensive contribution and $\bar d$ its average over all eligible season dropbacks:

$$
c_i=\bar d-d_i,
\qquad
\mathrm{Adjusted\ EPA}_i=\mathrm{EPA}_i+c_i
$$

A positive correction credits production against defenses estimated to suppress EPA. A negative correction discounts production against defenses estimated to allow more EPA.

The final centering uses the complete season’s exposure mix. Corrections sum to zero, preserving league-wide total EPA and its mean. Player-level adjusted EPA above average consequently also balances to zero across the complete reference population.

### 7. Game-resampling diagnostics

Two diagnostics are implemented:

- **Model comparison:** resample paired game-level errors while retaining dropback weighting. The approximate percentile interval is conditional on existing fitted models and folds. Shared teams and overlapping training sets create dependence not fully captured by this procedure.
- **Player variability:** resample each player’s observed games, pairing game EPA totals with their dropback counts. Recalculate the rate as a ratio of totals. Hold schedule corrections and the Pass+ reference fixed.

Player ranges describe conditional resampled-performance variability. They are not confidence intervals for isolated talent or uncertainty in already-observed season totals. They omit opponent-model refitting uncertainty and do not constitute pairwise ranking tests.

## Research results

The initial model used offense and defense penalties of 100. Its 2025 improvement did not replicate consistently across 2020–2024, prompting the stronger defensive-shrinkage candidate.

The table below reports the candidate with offense penalty 100 and defense penalty 1,000. Positive MSE reduction means offense-plus-defense outperformed offense-only on held-out plays.

| Season | Role in research | MSE reduction | Folds improved |
| --- | --- | ---: | ---: |
| 2017 | Additional frozen-candidate evaluation | +0.002267 | 5/5 |
| 2018 | Additional frozen-candidate evaluation | +0.000330 | 3/5 |
| 2019 | Additional frozen-candidate evaluation | +0.005123 | 5/5 |
| 2020 | Development | +0.003328 | 5/5 |
| 2021 | Development | +0.000268 | 2/5 |
| 2022 | Development | +0.000435 | 4/5 |
| 2023 | Development | +0.002224 | 5/5 |
| 2024 | Development | −0.000256 | 3/5 |
| 2025 | Development | +0.004462 | 5/5 |

The candidate improved MSE in eight of nine inspected seasons. Six seasons informed development, so these are **not nine independent validation results**. Gains were small. Among the three additional evaluation seasons, the conditional intervals were positive in 2017 and 2019; the 2018 interval included zero.

Predictive improvements support investigating the adjustment but do not prove causal defensive effects or accurate individual player attribution.

## 2025 reference output

Reference population: **101 participants and 19,734 eligible dropbacks**.

- League adjusted EPA per dropback: approximately **0.043145**.
- Weighted SD of player-season rates: approximately **0.187506**.
- Two documented scramble identity fallbacks.
- Schedule corrections sum to zero within numerical precision.

Selected results, using a display minimum of 100 dropbacks:

| Player | Games with eligible dropbacks | Dropbacks | Pass+ | Resampled index range | Adjusted EPA above average |
| --- | ---: | ---: | ---: | --- | ---: |
| D. Maye | 17 | 601 | 120 | 111–132 | +152.6 |
| J. Love | 15 | 483 | 117 | 108–126 | +101.9 |
| M. Stafford | 17 | 624 | 114 | 105–123 | +110.7 |
| B. Purdy | 9 | 313 | 114 | 98–129 | +55.2 |
| P. Mahomes | 14 | 586 | 111 | 101–120 | +81.8 |
| J. Goff | 17 | 619 | 111 | 100–122 | +84.2 |
| J. Allen | 16 | 547 | 110 | 100–120 | +68.1 |
| D. Prescott | 17 | 654 | 110 | 101–118 | +78.1 |
| D. Jones | 13 | 426 | 109 | 97–121 | +49.1 |
| M. Jones | 8 | 310 | 108 | 98–119 | +31.0 |

Index values are rounded for display; saved CSVs preserve full precision. Equal displayed scores can therefore appear in a particular sort order without implying a meaningful distinction. The resampling ranges carry the limitations described above.

## Installation

Use Python 3.11 or newer and run these commands from the repository root. The activation command below is for Bash or Zsh on macOS, Linux, or WSL.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

In VS Code, select the interpreter inside `.venv` using **Python: Select Interpreter**.

Dependencies are declared in `pyproject.toml`. The workflow uses nflreadpy, Polars, NumPy, SciPy, scikit-learn, and Matplotlib; development dependencies include pytest, Ruff, and ipykernel. The bootstrap code uses SciPy’s `rng` argument, available in SciPy 1.15 and newer.

```bash
python -m pip check
python -m ruff check src/gridiron_value tests
python -m pytest -q
```

## Running the 2025 pipeline

Run all commands from the repository root with the virtual environment active. Each stage prints its output manifest path. Replace the `REPLACE_WITH_...` portions below with the paths from your own run; they are placeholders, not commands that automatically discover the latest result.

Downloaded and generated data are excluded from Git by default. A fresh clone needs new downloads or access to preserved snapshots. Recorded historical run IDs do not themselves restore the associated files.

### Download and inspect

```bash
python -m gridiron_value.data --season 2025

RAW_MANIFEST="data/metadata/REPLACE_WITH_RAW_RUN_ID/manifest.json"

python -m gridiron_value.inspect_dropbacks \
  --manifest "$RAW_MANIFEST"
```

Inspect identifier coverage and flagged play categories before interpreting any results. The diagnostic exports unresolved and conflicting identity records for review.

### Build the cohort and descriptive baseline

```bash
python -m gridiron_value.cohort \
  --manifest "$RAW_MANIFEST" \
  --min-dropbacks 100

COHORT_MANIFEST="data/metadata/REPLACE_WITH_COHORT_RUN_ID/cohort_manifest.json"
```

The output includes the sequential exclusion report, eligible cohort, raw baseline, and identity fallback records.

### Calculate the frozen candidate’s schedule corrections

```bash
python -m gridiron_value.schedule \
  --cohort-manifest "$COHORT_MANIFEST" \
  --min-dropbacks 100

SCHEDULE_MANIFEST="data/metadata/REPLACE_WITH_SCHEDULE_RUN_ID/schedule_manifest.json"
```

The schedule module uses offense penalty 100 and defense penalty 1,000. Its saved leaderboard includes all eligible participants, regardless of the display minimum.

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

The two manifests must refer to the same schedule run and scored-play snapshot. To display additional players, first generate variability records with a sufficiently low dropback threshold.

## Reproducing the research experiments

### Original equal-penalty model

The `adjustment` CLI remains the original equal-penalty comparison. Supplying `--alpha 100` sets both penalties to 100 through its default behavior; this is not the frozen stronger-shrinkage candidate.

```bash
python -m gridiron_value.adjustment \
  --cohort-manifest "$COHORT_MANIFEST" \
  --alpha 100 \
  --folds 5

ADJUSTMENT_MANIFEST="data/metadata/REPLACE_WITH_ADJUSTMENT_RUN_ID/adjustment_manifest.json"

python -m gridiron_value.uncertainty \
  --adjustment-manifest "$ADJUSTMENT_MANIFEST" \
  --resamples 10000 \
  --seed 2026
```

### Defensive-penalty comparison

```bash
python -m gridiron_value.shrinkage \
  --cohort-manifests "$COHORT_MANIFEST"
```

Additional cohort manifest paths can follow the same argument. This compares defense penalties 100 and 1,000 while holding the offense penalty at 100 and retaining the same folds.

### Frozen-candidate historical replication

```bash
python -m gridiron_value.replicate --seasons 2017 2018 2019
```

The current runner uses offense penalty 100, defense penalty 1,000, and five game-grouped folds. It downloads snapshots and checkpoints each completed season. Historical results produced before the penalty change retain their original settings in their manifests.

To rerun the candidate across all inspected seasons:

```bash
python -m gridiron_value.replicate \
  --seasons 2017 2018 2019 2020 2021 2022 2023 2024 2025
```

This rerun does not create new independent validation evidence. The replication runner evaluates models; it does not automatically generate historical Pass+ presentations.

## Repository organization

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | Package metadata, dependencies, and development configuration |
| `src/gridiron_value/data.py` | Acquisition, schema audit, checksums, and shared JSON helpers |
| `src/gridiron_value/inspect_dropbacks.py` | Player identity and dropback-category diagnostics |
| `src/gridiron_value/cohort.py` | Eligibility rules and player attribution |
| `src/gridiron_value/baseline.py` | Descriptive rates and EPA above league average |
| `src/gridiron_value/adjustment.py` | Team models and held-out evaluation |
| `src/gridiron_value/uncertainty.py` | Conditional paired game-error resampling |
| `src/gridiron_value/replicate.py` | Fixed-specification historical experiments |
| `src/gridiron_value/shrinkage.py` | Separate defensive-penalty comparison |
| `src/gridiron_value/schedule.py` | Defensive corrections and adjusted production |
| `src/gridiron_value/variability.py` | Conditional player-game resampling |
| `src/gridiron_value/reporting.py` | Pass+ reference and presentation |
| `tests/` | Attribution, accounting, leakage, weighting, and transformation checks |
| `notebooks/` | Exploratory analysis |
| `docs/methodology.md` | Definitions, assumptions, and research design |
| `docs/progress.md` | Decisions, findings, and experiment history |
| `docs/results/` | Optional curated reference outputs committed with a checkpoint |
| `data/raw/` | Unfiltered loader snapshots |
| `data/interim/` | Intermediate data, including held-out predictions |
| `data/processed/` | Eligible and scored dropback datasets |
| `data/metadata/` | Provenance, reference parameters, and experiment manifests |
| `reports/tables/` | Generated leaderboards and diagnostics |
| `reports/figures/` | Generated visualizations |

## Reproducibility and testing

Manifests connect outputs to source snapshots and record settings, hashes, and relevant package versions. Downstream stages verify the source files they consume. Seeds make resampling repeatable within the recorded environment.

Preserve the underlying data files as well as their manifests when exact future reproduction matters: upstream datasets may be revised, and a checksum alone cannot recover the original bytes. Package-version records also do not substitute for a fully locked environment.

Tests cover:

- Scramble-specific identity recovery and identifier conflicts.
- Correct dropback denominators and season-specific league weighting.
- Conservation of totals and zero-sum above-average values.
- Game-grouped evaluation and exclusion of held-out outcomes from their own fitted predictions.
- Equivalent behavior under equal penalties and stronger defensive shrinkage.
- Correct extraction and sign of defensive effects.
- Zero-sum schedule corrections.
- Paired resampling with unequal game sizes.
- Weighted Pass+ centering and dispersion.

```bash
python -m ruff format src/gridiron_value tests
python -m ruff check src/gridiron_value tests
python -m pytest -q
```

Passing implementation tests is not evidence that the statistical model is sufficient for every intended use. Model evaluation and accounting checks answer different questions.

## Limitations and next research steps

The current prototype has several material limits:

- Team effects do not isolate an individual quarterback from supporting players or play design.
- The additive model does not represent all matchup interactions or changes in team strength during a season.
- There are no additional game-context predictors beyond the existing EPA target and team identities.
- Five-fold game grouping introduces a partition choice whose effect on player corrections needs further assessment.
- Conditional resampling omits model-refitting uncertainty and does not fully account for cross-game dependence.
- The play-weighted distribution of player-season rates includes sampling noise; standardization is not a talent-shrinkage procedure.
- Small predictive gains do not establish causal schedule corrections or meaningful separation between nearby player ranks.
- Retrospective testing does not establish future-game forecasting, replacement value, or WAR.

Planned work includes historical Pass+ presentations under the frozen specification, game-partition sensitivity, uncertainty from refitting the opponent model, and a separately designed chronological evaluation if forecasting becomes an objective. Alternative cohort and penalty treatments should be documented and evaluated explicitly.

See the [methodology](docs/methodology.md) and [progress notebook](docs/progress.md) for the evolving research record.

## Data sources and prior work

- [nflverse](https://github.com/nflverse): data and tools supporting the pipeline.
- [nflreadpy](https://github.com/nflverse/nflreadpy): Python access to nflverse data.
- [nflfastR](https://nflfastr.com/): play-by-play data and expected-points models.
- [nflWAR: A Reproducible Method for Offensive Player Evaluation in Football](https://arxiv.org/abs/1802.00998): prior work on reproducible player-value estimation.
- [scikit-learn Ridge](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.Ridge.html), [ColumnTransformer](https://scikit-learn.org/stable/modules/generated/sklearn.compose.ColumnTransformer.html), and [GroupKFold](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupKFold.html): model implementation and evaluation tools.
- [SciPy bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html): paired resampling implementation.

Downloaded datasets and dependencies retain their respective terms and attribution requirements. This README does not assign a license to third-party data or substitute for a project `LICENSE` file.
