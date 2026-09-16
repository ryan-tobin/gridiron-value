# Player profiles: first implementation

This command builds an offline searchable directory, individual HTML player
profiles, structured JSON, and a provenance manifest. It consumes existing
position-metric and participation snapshots; it does not download new data.

## Install the two files

Place `player_profile.py` in `src/gridiron_value/` and
`test_player_profile.py` in `tests/`. Existing pipeline files stay in place.
Run from your local repository root using its Python environment:

```bash
python -m pytest -q tests/test_player_profile.py
```

## Generate profiles from the saved checkpoint

This command is a single line so it works in PowerShell, Bash, and Zsh:

```bash
python -m gridiron_value.player_profile --position-metrics-manifest reports/tables/position_metrics_20260916T011936835823Z/position_metrics_manifest.json --participation-manifest reports/tables/participation_20260916T010329368054Z/participation_manifest.json --season 2026
```

Open the printed `index.html` path in a web browser. Search by player name,
GSIS ID, position, or team and click a profile. Keep the HTML files together;
their links are relative. No server or additional UI dependency is required.
To limit output, append `--gsis-id` followed by an exact ID from the directory.
Regular season is the default; `--season-type POST` selects postseason rows.

The named input files must have been transferred from Codespaces along with
their referenced Parquet snapshots. Existing manifests need no edits when
their relative directory structure is preserved.

## Metric contract v1

- Profile key: season, season type, and GSIS ID. Teams and positions retain
  observed history; traded players aggregate across teams within this scope.
- Full union of production and resolved participation keys preserves stats-only
  and snap-only players. Keys include season, game, team, and GSIS ID.
- Snaps come from participation; production comes from the metric snapshot.
- Duplicate keys, conflicting weeks/types, or contradictory production flags
  fail rather than silently choosing a row.
- REG, POST, and PRE are never combined. Week-zero aggregate records are rejected.
- Totals record observed sum, observed game count, and total game count.
  A complete `value` exists only when every observed game has a finite value.
  This does not establish that every actual game is present in the snapshot.
- Rates divide sums of paired, available numerator and denominator fields.
  A missing numerator excludes that game's denominator too. No average of game
  rates is used. A zero denominator produces null; a partial observed rate is
  labeled as such and its complete `value` stays null.
- NGS, target share, air-yards share, and WOPR remain game-level context.
  The join does not retain all source weighting counts, so no season averages
  are inferred.
- Passing EPA is shown as a supplied source total. Passing EPA per attempt is
  withheld pending a separate cohort audit; attempts plus sacks remains only
  the denominator for the explicitly named sack-rate proxy.
- No season snap percentages are inferred by averaging game percentages.
- Summaries select groups from observed roster positions and any nonzero
  game-level activity. A QB gets passing/rushing, a defender gets defense,
  and additional groups appear when the player has activity in them. Roles
  affect display only, never the stored totals or rates.
- All fields in a selected group stay visible, including known zero counts
  and unavailable values. Off-role activity is checked per game so outcomes
  that cancel in a season sum remain visible. Expanded game details and JSON
  retain all supported fields.
- Efficiency tables use only relevant offensive rate definitions. Roles with
  no implemented efficiency metrics get an explanation instead of an empty
  table. Defensive snaps are not substituted for pass-rush opportunities.
- Methodology and available NGS units are collapsible. The always-visible
  coverage note and per-field availability statuses distinguish missing,
  partial, complete-for-observed-games, and zero-opportunity values.
- EPA retains shared contributions. Individual OL/coverage grades, current-season
  Pass+, historical profile joins, qualifications, and percentiles remain future work.

## Provenance and scope

The generator checks the supplied participation manifest against the metric
manifest's recorded checksum, checks their shared player-game reference, and
verifies both consumed Parquet files before writing output. The profile manifest
records the consumed inputs, source manifests, settings, code hash, Polars
version, and hashes of all generated files. It does not replay or revalidate
every upstream transformation.

The two unresolved snap identities and anonymous statistics remain upstream
review records. They cannot safely receive a GSIS-keyed player profile.

## Verification

Tests cover unequal game workloads, partial/missing observations, zero
denominators, nonfinite values, separate season types, traded teams, preserved
snap-only and stats-only players, inconsistent identities, tampered input
files, lineage mismatch, output checksums, and escaped HTML source text.
Presentation checks cover defensive profiles, zero versus unavailable values,
and off-role activity with a zero season sum.
