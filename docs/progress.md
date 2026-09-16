Gridiron Value progress record

This file explains how to regenerate the project-wide progress view. The
generated report is intentionally kept separate from this durable research
record because each run is timestamped and tied to exact source manifests.

Generate the current status view

From the repository root, provide the manifests from the latest completed
stage of each pipeline branch:

python -m gridiron_value.project_status \
  --historical-analysis-manifest reports/tables/<historical-analysis>/analysis_manifest.json \
  --chronological-manifest reports/tables/<chronological>/chronological_manifest.json \
  --coverage-manifest reports/tables/<coverage>/coverage_manifest.json \
  --identity-manifest reports/tables/<identity>/identity_manifest.json \
  --player-game-manifest reports/tables/<player-game>/player_game_manifest.json \
  --participation-manifest reports/tables/<participation>/participation_manifest.json \
  --position-metrics-manifest reports/tables/<position-metrics>/position_metrics_manifest.json

The command writes a timestamped directory under reports/tables/ containing:

report.md — the GitHub-readable milestone, coverage, and validation tables;

dashboard.html — a lightweight local view with feed-coverage and
chronological-validation charts;

CSV summaries for milestones, feed coverage, and chronological validation;

project_status_manifest.json — checksums and source-manifest references.

Current pipeline stages

Historical Pass+ analysis and threshold sensitivity.

Chronological within-season validation.

Current-season feed and identifier coverage.

Explicit player identity resolution and reviewed overrides.

Production player-game joins.

Participation records, including snap-only players.

Position-specific metrics and NGS context.

The status report is descriptive project instrumentation. It does not replace
the methodology, model-evaluation reports, or the limitations recorded in each
stage manifest.