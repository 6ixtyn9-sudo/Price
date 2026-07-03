#!/usr/bin/env bash
set -euo pipefail

if ! grep -qF 'V5 — ML Discovery Results (2026-07-03)' HANDOVER.md 2>/dev/null; then
cat << '__HANDOVER_RESULTS_END__' >> HANDOVER.md


V5 — ML Discovery Results (2026-07-03)
Ran the V5 ML bridge end-to-end on real warehouse data: scripts/ml_to_slices.py
on SPY 1d produced 8 candidate state-slices from 8 promising LightGBM
interactions, then validate_slices.py ran them through full V4 discipline.

First read (ML candidates only, m=8): one clean survivor, SPY 1d
state_ext=stretched_up + state_ret_3=ret_up (valid_n=70, valid mean +0.718%
cost-adj, NW p=0.0076, walk-forward 0101). This is structurally novel -- the
combinatorial 1d grid only covers state_ext+state_slope and state_ext+state_vol,
so it can never pair extension with a return band. The ML path's dividend is
this family.

Fold-count sweep (NF=3,4,5,6) on the ML lead: pass patterns 011 / 0101 / 01101
/ 001001. Sign is positive in EVERY fold at EVERY fold count (6/6 at NF=6).
The failures are thin-sample significance, not sign flips -- the XLF hallmark,
not the QQQ-lunch intermittent pattern initially feared.

Date-range freshness gate: passes all, latest_12m, 2026-ytd, and latest_6m.
Critically, it is STRENGTHENING, not decaying -- 2026-ytd and latest_6m both
+1.33% (vs +0.41% all-window). This is the opposite trajectory of every prior
demoted candidate (SPY afternoon, XLE 1d, GLD 1h all decayed into latest_6m).
Caveat: calendar-2024 parent-excess was NEGATIVE (-0.0019); the edge only turns
clearly positive from 2025 onward, so the full-history significance is partly
carried by the recent regime. XLF had genuine 2024 strength, so do not call
this strictly better than XLF on history depth.

Search-wide reality check (the decisive number): pooled the 8 ML candidates
with the 352 combinatorial slices into a combined family of 360 and re-ran the
candidate leaderboard with proper multiple-testing correction.

- ML lead SPY stretched_up+ret_3: search_wide_rank 19, BH FAIL, Bonferroni
  FAIL. At rank 19/360 the BH critical p is 0.00264; its NW p is 0.0076.
  It is NOT search-wide-defensible. Its raw p is ~35x weaker than the slice
  that does clear BH.
- The only CLEAN survivor that clears BH is combinatorial, not ML: XLE 1d
  state_ext=stretched_down + state_slope=downtrend (sw_rank 6, BH pass). But
  it is decaying (wf 0110, recent windows fail) -- already flagged in V4.
- Bonferroni-passers (DIA sw_rank 2, IWM sw_rank 4) are all
  late_emerging_recent_only (0001, scenario_survived 0) -- small-sample recent
  artifacts, discarded per the existing HANDOVER warning.

Practical conclusion on the ML path:
- ML did NOT produce the project's most defensible candidate. A fixed-prior
  combinatorial slice beat every ML slice on the strictest gate. This is
  consistent with ML's in-sample 75th-percentile "in-state" cut being more
  overfit-prone than the grid's fixed +-0.015 / tertile priors.
- ML DID expand the search space and surface a structurally-novel, sign-stable,
  fresh family the grid cannot reach. That is a real but modest dividend.
- The project's standing deadlock is unchanged: no slice -- ML or combinatorial
  -- combines robust walk-forward + search-wide-defensible p + positive
  parent-excess. Nothing is promoted.

Net verdict: adding ML was a net positive as a SECOND discovery engine held to
the same discipline. It earns its place alongside the combinatorial grid, not
above it. Treat it as "a different search that found a different profile," not
as "the smarter engine." The ML lead (SPY stretched_up+ret_3) joins the watch
list alongside XLF (walk-forward-strong, p-weak) and XLE (p-strong,
walk-forward-decaying) as the third distinct profile -- none promotable yet.

Honest methodological note for future agents: the ML path's candidate
generation uses an in-sample quantile cut, which is inherently more
overfit-prone than fixed-bin priors. If the ML path is expanded, the highest-
value next improvement is replacing the in-sample-quantile "in-state" definition
with out-of-sample / rolling-quantile bins, or a proper conditional-interaction
test (SHAP interactions, partial-dependence contrasts), rather than adding more
features or symbols.
__HANDOVER_RESULTS_END__
  echo "Appended V5 ML Discovery Results section to HANDOVER.md"
else
  echo "V5 ML Discovery Results section already present; skipping"
fi
