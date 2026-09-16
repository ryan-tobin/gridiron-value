# Qualified position leaderboards

This adds offline leaderboards and profile percentile bars using the existing
verified player-profile snapshot. It makes no network requests and creates a new
timestamped report directory. Existing snapshots and profile outputs stay intact.

## Run

From your repository root, after extracting this update's `src`, `tests`, and `docs`
folders into the matching repository folders:

```bash
python -m pytest -q tests/test_leaderboards.py tests/test_player_profile.py
python -m gridiron_value.leaderboards --latest --season 2026
```

`--latest` selects the newest full profile run for the requested season and season
type (REG by default), prints the selected manifest, and records it in the output.
Single-player exports are excluded. If the selected snapshot fails verification,
the command stops instead of silently using an older run.

Alternatively, use `--profile-manifest` with an exact `profile_manifest.json` path.
Use `--season-type POST` to build postseason comparisons separately.

If no full profile run is found, generate one from the existing inputs:

```bash
python -m gridiron_value.player_profile --position-metrics-manifest reports/tables/position_metrics_20260916T011936835823Z/position_metrics_manifest.json --participation-manifest reports/tables/participation_20260916T010329368054Z/participation_manifest.json --season 2026
```

Open the printed `leaderboards.html` or `index.html` path in your browser. Each
leaderboard player links to an enriched profile. The directory remains searchable
by player, ID, team, or position. All pages work offline.

## Comparison groups and metrics

| Group | Metrics |
|---|---|
| QB | Completion rate, passing yards/attempt, TD rate, interception rate, sack-rate proxy, rushing yards/carry, rushing EPA/carry |
| RB (including HB and FB) | Rushing yards/carry, rushing EPA/carry, catch rate, receiving yards/target, receiving EPA/target, YAC/reception |
| WR | Catch rate, receiving yards/target, receiving EPA/target, YAC/reception |
| TE | Catch rate, receiving yards/target, receiving EPA/target, YAC/reception |

WR and TE are separate cohorts. A player whose observed positions span different
groups receives no comparison. Defensive, line, and special-teams profiles remain
available; this release does not invent efficiency metrics from total snaps.
Interception rate and sack-rate proxy are lower-is-better; other metrics are
higher-is-better. Sack-rate proxy uses attempts + sacks and excludes scrambles.

## Qualification

| Denominator | Default minimum |
|---|---:|
| Pass attempts | 20 |
| Attempts + sacks | 20 |
| Carries | 10 |
| Targets | 5 |
| Receptions | 5 |
| Qualifying peers for a percentile, including the player | 5 |

These are provisional display filters, **not validated reliability thresholds**.
They remain absolute as the season progresses. Customize explicitly with:

```bash
python -m gridiron_value.leaderboards --latest --season 2026 --qualification-config docs/leaderboard_qualification.json
```

Each metric has its own qualifying cohort. A rate must have paired numerator and
denominator data for every observed game and meet its denominator minimum.
Missing values are not zero-filled. Rates use summed numerators divided by summed
denominators, not averages of game rates. Complete observed-game coverage does not
certify full schedule coverage. Different players may have different game counts.

## Ranking and percentiles

Ranks use unrounded values and competition ties: 1, 1, 3. Percentiles use:

`100 × (worse peers + 0.5 × equal peers) / qualifying cohort size`

The cohort includes the player. Higher percentiles always mean better observed
results in the metric's stated direction. All-tied cohorts receive 50; endpoints
need not be 0 and 100. Percentiles are withheld when the qualifying cohort is too
small; descriptive ranks and workload counts remain visible.

These are **qualified snapshot peer percentiles**, not certified NFL population
percentiles or isolated player-talent estimates. Early regular-season snapshots
through week 4 carry an early-season label. Later snapshots still carry observed
coverage and descriptive-result labels. No opponent adjustment, current Pass+,
NGS season aggregation, or defensive opportunity denominator is added here.

## Outputs and audit trail

- `leaderboards.html`: separate position and metric tables with workload, game
  counts, ranks, peer counts, and percentiles.
- `index.html` and `player_*.html`: searchable directory and enriched profiles,
  including players without a comparison group.
- `comparisons.csv`: every supported player/metric, including exclusion reasons.
- `comparisons.json`: comparison rows, cohort counts, settings, limitations, and
  a list of unsupported or mixed-role profiles.
- `leaderboard_manifest.json`: exact source manifest and profiles checksum,
  applied settings, optional configuration checksum, code hashes, and output hashes.

The reader verifies the profiles checksum, season/type, unique IDs, game keys,
counts, and consistency of stored rates with the underlying game observations.
Keep the complete output folder together so relative profile links work.
