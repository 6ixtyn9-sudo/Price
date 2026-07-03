#!/usr/bin/env bash
set -euo pipefail
if [ ! -f "HANDOVER.md" ]; then
  echo "ERROR: run from the Price repo root." >&2; exit 1
fi
if grep -qF 'V5 - Direction-Agnostic Results (2026-07-03)' HANDOVER.md 2>/dev/null; then
  echo "Section already present; skipping."; exit 0
fi
cat >> HANDOVER.md <<'INNEREOF'


V5 - Direction-Agnostic Results (2026-07-03)
The validation gate was previously long-only by construction: survives()
hard-requires mean_return > 0, so any slice whose forward return was
significantly NEGATIVE was rejected -- even if it would have been a clean
short edge. The direction-agnostic layer (commit db6fef3) fixes this without
weakening the gate: returns are now direction-adjusted before the cost test,
so a short's P&L is the negated forward return minus borrow drag, and the same
mean_return > 0 test then correctly accepts a promotable short. A new
RiskLimits.allow_shorts=False kill-switch blocks short execution by default;
paper_trade.py --allow-shorts is the only thing that opens it.

This section records the first real exercise of the short side.

Method: re-ran discover_slices.py across 1d and 1h for the full 10-symbol
universe (now tagging every slice side=long|short by sign of mean return, and
ranking the output by tradeable direction-adjusted P&L). Then re-ran
validate_slices.py --candidate-leaderboard, which now carries a short-borrow
stress grid (short_cost_bps 2 / 5 / 10) in addition to the existing cost/split
scenarios. 682 slices total: 487 long, 195 short -- so 29% of the candidate
pool is now shorts the old system could never have considered.

Result on the short side: 195 short candidates tested, 0 promoted, 1 added to
the watch list. No short cleared the full survived gate (train + valid +
cost + Newey-West + walk-forward + sample floor + parent-excess).

The single watch-list short is TLT 1d state_ext=stretched_up + state_vol=mid_vol:
  - provisional_sample_starved (valid_n=11, one below the min_samples=15 floor)
  - direction-adjusted valid mean +0.73%, Newey-West p=0.022
  - walk-forward pattern 0010 (only fold 2 passes -- intermittent, not stable)
  - excess vs best parent +0.0013 (positive, clears the parent-excess bar)
  - BORROW STRESS GRID (the key number):
      default (0 bps borrow): provisional
      short_borrow2:          provisional
      short_borrow5:          provisional
      short_borrow10:         rejected
  Real TLT borrow over a 5-bar (~1 week) hold is a fraction of a basis point,
  so surviving 5 bps is a comfortable margin -- borrow is NOT what is keeping
  TLT off the survived list. Sample (n=11) and walk-forward instability (0010)
  are. That makes it a needs-more-data case, not a falsified case.
  Economically coherent: bonds stretched up + normal vol -> fade is a classic
  mean-reversion story, and TLT is one of the few assets where fades are real.

The other 194 shorts were correctly rejected:
  - USO 1h variants and XLF 1h stretched_up+downtrend landed in
    provisional_sample_starved with eye-catching means (+2.5% to +2.8%) but
    n=4-10 and walk-forward 0000 (no fold passes). Those huge returns on
    single-digit samples are exactly the small-sample artifacts the triage
    system exists to catch. Correctly binned, correctly not promoted.
  - DIA 1d stretched_up+low_vol passed significance (p=0.047) but is
    late_emerging_recent_only -- a latest-fold-only effect, a recent-regime
    artifact.
  - Everything else is rejected_unsupported: not significant, or negative even
    after direction-adjustment (IWM/QQQ 1h shorts at -0.03% to -0.05% -- the
    direction-adjustment could not rescue them because there is no edge there
    to invert).

Practical conclusion:
- The direction-agnostic layer worked exactly as designed: it searched both
  sides symmetrically, held shorts to the same gate as longs, stress-tested
  borrow, and the data says short edges on liquid US ETFs over a 5-bar daily
  hold are genuinely rarer than long edges.
- The asymmetry the old long-only lock HID turned out to be a real asymmetry,
  not an artifact of the lock. That is itself a defensible research
  conclusion.
- TLT stretched_up+mid_vol joins the watch list alongside XLF (long,
  walk-forward-strong, p-weak) and XLE 1d (long, p-strong, walk-forward-
  decaying). None promotable. The project's standing deadlock is unchanged:
  no slice -- long or short -- combines robust walk-forward + search-wide-
  defensible p + positive parent-excess.
- Nothing changes about monitoring or execution. monitor.DEFAULT_MONITORED_
  SLICES still carries only long slices (each now explicitly tagged
  side=long). allow_shorts defaults to False. A short would only enter the
  monitored set after it clears survived AND walk-forward stabilizes, which
  TLT has not.

Scope note for future agents: the short search was limited to the existing
combinatorial feature grid (state_ext / state_slope / state_vol / state_session
on 1d and 1h). The ML bridge (ml_to_slices.py) emits candidates without a side
tag; if ML short discovery is wanted, interactions_to_state_slices would need
to infer side from the scored mean_return sign, mirroring discovery.py. That
is a small, contained follow-up -- not done here, because the combinatorial
short search already answered the research question.
INNEREOF
echo "Appended V5 Direction-Agnostic Results section to HANDOVER.md"
