# Current-season dropbacks and basic situations

Run offline from the repository root with the project Python environment:

```bash
python -m gridiron_value.current_dropbacks \
  --coverage-manifest reports/tables/coverage_20260915T231229504521Z/coverage_manifest.json \
  --season 2026
```

Use an explicit preserved coverage manifest. No download, automatic latest-run selection or model fitting occurs. Each successful execution writes a new `reports/tables/current_dropbacks_<timestamp>/` directory and prints its manifest. `--project-root` supports an explicit repository location. Only regular-season eligible dropbacks are analyzed; postseason and preseason rows are excluded and counted by the existing cohort rules.

Outputs:

- `dropbacks.parquet`: eligible plays, original context and resolved dropback actor.
- `exclusions.csv`: sequential historical-cohort rule counts.
- `identity_audit.csv`: primary/fallback actor-source counts.
- `event_audit.csv`: mutually exclusive sack, scramble, other-eligible, unknown-flag or conflicting-flag categories. Other eligible plays are **not** certified official pass attempts.
- `player_totals.csv`, `team_totals.csv`: EPA totals, eligible dropbacks, EPA per eligible dropback, positive-EPA fraction and distinct observed games.
- `player_situations.csv`, `team_situations.csv`: the same statistics separately by down, yards-to-go band, field-position band and possession-team score state.
- `report.md`, `current_dropbacks_manifest.json`: interpretation and checksummed lineage.

Down uses 1–4. Distance bands are 0–3, >3–7 and >7 yards. Field position uses yards to the opponent goal: 0–20 is red zone, >20–50 is opponent half outside red zone, >50–100 is own half. Score state uses pre-play score differential from the possession team's perspective. Missing/nonfinite or invalid values go to `unknown`; absent context columns do too.

Each dimension is a separate partition. Never add rows across different dimensions. Player totals combine observed teams within season/type; team totals group `posteam` and describe offensive dropback production. No roster-position filter silently removes unusual actors. EPA rates use exactly the same eligible plays for numerator and denominator, with equal weight per play. There is no talent interpretation, qualification threshold or inferred reliability from these sample sizes.

The manifest explicitly reports chronological evaluation as `not_evaluated`; having a Week 5 row is only a descriptive flag, not a readiness certificate. Current-season Pass+ and passing EPA per attempt remain absent. The event audit does not yet reconcile the separate player-stat feed's supplied EPA numerator; that is the next bounded audit.

```bash
python -m pytest -q tests/test_current_dropbacks.py
python -m ruff check src/gridiron_value/current_dropbacks.py tests/test_current_dropbacks.py
```
