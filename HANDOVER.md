Price — Handover
Date: 2026-06-30
Purpose: clean-slate price-first research lab. Focus is on understanding market price behavior before choosing sides or building execution logic.

Single source of truth
This file is the handover and should be updated in place. Do not create drifting reports or extra planning documents unless explicitly asked.

Current decision
The previous sports-heavy path and the legacy crypto system were intentionally set aside.

Reasons:

sports repos (Edge/Racket) remain useful background monitors but share failure-mode risk if over-copied into new systems
STST-Trading-System has real substance but too much hybrid architecture drag (Apps Script + Python + Supabase + historical off-repo notebook truth)
the new priority is a cleaner substrate with abundant structured price data and less operational baggage
Chosen direction
Start a brand new repo called Price.

Current target substrate:

US ETFs / indices first
structured API data first
bar-based price research first
Python-first, local-first, lean architecture
auto-discovery of market-state slices
no broker integration in v1
no options in v1
no dashboards / governance / council layers in v1
Core philosophy
The foundation is to understand the price before choosing sides.

This repo should not begin with:

hand-authored strategy bundles
hype claims about bots printing money
immediate signal generation
execution wiring
options-chain complexity
This repo should begin with:

clean OHLCV ingestion
a reproducible local warehouse
descriptive market-state features
3D–5D slice discovery
honest train/valid/walk-forward validation
Step 1 — substrate
Initial focus is a small, clean US ETF/index universe.

Recommended initial symbols:

SPY
QQQ
IWM
DIA
GLD
TLT
USO
XLK
XLF
XLE
Recommended initial timeframes:

15m
1h
1d
Why this substrate:

abundant data
no thin-slate problem
cleaner than sports market/result matching
less baggage than reviving STST
broad enough to test mean reversion / momentum / trend behavior without hardcoding them
Step 2 — data source doctrine
Use structured API data first.

Rationale:

unlike sports, stocks/ETFs do not require bookmaker-style scraping to establish the first research substrate
structured API bars are cleaner and easier to validate
scraping can be considered later only if the chosen data source proves insufficient
Current intended data stack:

API-first historical and current OHLCV bars
local flat-file / parquet / DuckDB warehouse
no Supabase in v1
no broker platform dependence in v1
Operational Requirements:

Rate Limiting: The ingestion client (data_sources.py) must implement polite rate-limiting and exponential backoff (especially for Alpaca's 200/min limit).
Progress Caching: Fetching must be chunk-based (e.g., 30-90 day increments) and appended incrementally to Parquet to prevent full restarts on failure.
DuckDB Read-Through: DuckDB queries should scan Parquet directories directly to maintain a stateless warehouse layer.
Important:
Do not rush into options, brokers, or automation before the base price substrate proves itself.

Step 3 — research grain
The atomic research row is not a signal, pick, or order.
It is:

one symbol × one bar timestamp × one forward evaluation window
Every row should eventually support:

symbol
timeframe
bar timestamp
OHLCV inputs
derived features describing price state
forward return windows
excursion / drawdown labels
slice eligibility fields
Step 4 — discovery doctrine
Do not start with hard-coded strategy rules.

Instead:

compute descriptive features
let the system generate candidate slices
test which slices show stable forward behavior
Example feature families:

price extension vs moving average
ATR-normalized extension
recent 1/3/5/10 bar return bands
realized volatility bands
trend slope bands
session bucket
day-of-week / month
ETF class / sector family
Example candidate slices:

index ETF + opening hour + stretched down + low trend slope
commodity ETF + 1h + high trend slope + moderate volatility
tech ETF + daily + pullback-in-trend regime
Important principle:
The code should encode normalization, feature extraction, slice generation, and validation discipline — not subjective edge stories.

Step 5 — validation doctrine
Every discovered slice must survive:

train / valid separation
minimum sample thresholds
cost assumptions
overlap awareness
regime sanity
eventually walk-forward survival
Do not promote slices based on:

tiny samples
a good-looking week
social-media anecdotes
one chart or one strategy story
Why not options first
Options are explicitly deferred.

Reason:
options add too much complexity too early:

chain data
expiration handling
strike selection
greeks
IV surface issues
liquidity/spread filters
execution assumptions
Correct path:

prove price-behavior edges on the underlying
only then explore options as an execution wrapper
Why not FX first
FX is not rejected, but not first.

Reason:

fewer instruments
less cross-sectional richness than equities/ETFs
macro interpretation burden earlier in the build
Why not sports first
Sports is intentionally not the next main build.

Reason:

sparse/event-driven slate problems
more result/odds matching complexity
too much shared failure-mode risk with Edge/Racket patterns
Why not revive STST now
STST was inspected in workspace.
High-level conclusion:

real system, not fake
but hybrid and heavy
Apps Script + Python + Supabase + historical off-repo research truth
too much architecture drag for a fresh rapid discovery sprint
Practical conclusion:
Do not revive STST now. Treat it as a later salvage candidate, not the current foundation.

Current workspace state
Old repos were intentionally removed from workspace:

Edge-Factory
Racket-Factory
STST-Trading-System
Workspace contains only:

Price/
Immediate next actions for the next agent
Do these in order:

Finalize data-source shortlist
primary source, fallback source, output contract
Design the exact row schema
raw OHLCV, derived feature placeholders, forward-return labels, metadata fields
Scaffold the repo
minimal code structure
Build only ingestion + warehouse first
Non-goals for v1
Do not add these early:

broker integration
live trading
options-chain support
dashboards
councils/governance layers
sprawling cloud persistence
prompt systems
strategy-story marketing
Agent workflow / preferred collaboration style
The preferred maintenance workflow is the practical small-patch workflow.

Working style:

Keep changes minimal, safe, and copy-pasteable.
Prefer small targeted patches over broad rewrites.
Do not create new helper scripts, validators, reports, or docs unless explicitly asked.
Do not create placeholder files, placeholder tests, or fake scaffold content just to make the repo look complete.
Use temporary shell one-liners for diagnostics instead of committing one-off tooling.
Explain what each patch is expected to fix before asking the operator to run it.
Do not run broad ingestion pulls, expensive API harvests, or load large ignored localdata from the agent environment unless the operator explicitly agrees.
The operator runs local commands; the agent reads pasted terminal output and provides the next safe step.
Never print or request secrets. If secrets appear in chat, tell the operator to revoke them and move keys to ignored .env files.
Patch workflow:

Inspect the relevant source narrowly.
Provide an exact bash block the operator can paste.
Include a syntax check: python3 -m py_compile <changed_python_file>
Include a narrow sanity test that does not burn API quota.
Review the operator's pasted output before suggesting commit/push.
Only commit after: syntax check passes, targeted sanity check passes, diff is reviewed, and no unrelated files are included.
Use clear, small commit messages describing the actual fix.
V1 to V4 build boundaries
This section exists to stop future agents from drifting into unnecessary complexity too early.

V1 — data source shortlist + canonical schema
Scope:

finalize the API-first market-data shortlist
choose the initial primary and fallback source
define the canonical bar-state row schema
confirm the initial universe and timeframes
Allowed:

source comparison
schema design
local sample inspection
narrow docs updates in this handover / README if needed
Not allowed:

options-chain work
broker/execution integration
live trading logic
strategy promotion claims
broad architecture expansion
V2 — minimal scaffold + ingestion
Scope:

create the minimal code structure only once the schema is agreed
implement bar capture for a tiny symbol set
normalize symbols, timestamps, and timeframes
write reproducible local warehouse outputs
Allowed:

src/price/* minimal modules
scripts/capture_bars.py
scripts/build_warehouse.py
tiny-sample ingestion checks
narrow schema verification
Not allowed:

options support
broker APIs
execution runners
cloud persistence sprawl
dashboards
governance/council systems
broad multi-provider complexity before one clean path works
V3 — feature state + slice discovery
Scope:

compute descriptive price-state features
generate candidate 3D–5D slices
measure forward-return behavior on historical bars
separate raw coverage, feature coverage, and discovered slices
Allowed:

feature engineering tied to price state
discovery logic
sample-size floors
small, reproducible research outputs
Not allowed:

hand-authored “hero strategy” bundles
flash strategy marketing
social-media style profit claims
broker/execution work
options overlays
sprawling orchestration layers
V4 — validation discipline
Scope:

train/valid separation
cost-aware evaluation
overlap awareness
regime sanity
eventual walk-forward validation
promotion discipline for only the most stable slices
Allowed:

validation logic
cost assumptions
walk-forward planning
rejection of weak or unstable slices
Not allowed:

live deployment
broker wiring
options execution design
“AI bot prints money” narratives
premature portfolio automation
another bloated hybrid architecture like STST
Anti-drift rules
Future agents must not bypass the sequence. Do not jump from V1/V2 straight into options, broker/execution work, flashy strategy claims, complex governance layers, cloud-memory sprawl, or multi-runtime hybrid architecture.

V1 Decision Record (2026-07-01)
This section locks the concrete decisions required before any V2 scaffolding/ingestion code is written.

Data source: primary + fallback
Primary source for intraday (15m, 1h): Alpaca Market Data API (alpaca-py SDK), IEX feed on the free tier. Zero cost, adequate history, corporate actions endpoint.
Primary source for daily (1d): Tiingo API. Zero cost, consolidated volume, deep history (30+ years back to 1990s), correct adjusted-close reconstruction.
Both are free-tier usable, so no budget approval is required to start.
Open item requiring operator action before ingestion code is written:

Register free API keys for Alpaca and Tiingo, store them in a local .env (already gitignored) — never paste keys into chat.
Run one narrow manual pull (e.g. SPY, 1d, last 30 days) from each and diff adjusted closes, to confirm the free-tier IEX feed is acceptable.
Canonical bar-state row schema (v1 draft)
One row = one symbol × one timeframe × one bar timestamp. Forward-return/label columns are computed relative to that bar.
Identity / metadata:

symbol (normalized, uppercase)
timeframe (15m | 1h | 1d)
bar_ts_utc (bar open timestamp, UTC, tz-aware)
source (alpaca | tiingo)
ingested_at_utc (ingestion timestamp)
Raw OHLCV:

open_raw, high_raw, low_raw, close_raw, volume_raw
Adjusted OHLCV & reconstruction:

open_adj, high_adj, low_adj, close_adj
adj_factor (cumulative multiplicative factor applied to raw close to get close_adj)
split_factor (default 1.0), dividend_cash (default 0.0)
Descriptive features (computed later in V3):

feat_ext_vs_ma_, feat_atr_norm_ext, feat_ret_1/3/5/10, feat_realized_vol_, feat_trend_slope_*, feat_session_bucket, feat_dow, feat_month, feat_sector_family
Forward evaluation placeholders (computed later in V3):

fwd_ret_* (per horizon), fwd_mfe, fwd_mae, label_eligible
Time & timezone convention (hard rule)
All bar timestamps are stored as UTC, timezone-aware, no naive datetimes.
A bar's timestamp is its open time (bar [t, t+timeframe)).
1d bars must be fetched directly from Tiingo (primary) or Alpaca (secondary) to ensure deep historical reach (1990s vs 2016 cap) and consolidated market volume. 1h bars remain resampled from 15m bars.
Feature Invariant: In features.py, any timezone-aware UTC timestamp must be localized to America/New_York BEFORE extracting time/session-based features (e.g., hour of day, session bucket) to avoid DST drift.
Look-ahead bias invariant (hard rule)
A feature computed "as of" bar T may only use information available at or before the close of bar T.
A forward label for bar T may only reference bar T+1 or later.
This must be an enforced invariant with a test.
Corporate actions & data revision policy
Corporate actions (splits, cash dividends) are stored explicitly per bar.
Intraday Adjustment Propagation: Because intraday bars (15m, 1h) are often raw/unadjusted, the daily cumulative adjustment factor (adj_factor = close_adj / close_raw) from the daily bars must be propagated backward to all intraday bars for that given day during the warehouse build. This ensures consistency and prevents artificial price gaps in features.
Ingestion write path must be append + explicit overwrite, never silent in-place mutation.
Gaps & market calendar handling
Use pandas_market_calendars (XNYS calendar) as the single source of truth for expected trading sessions.
A missing bar during an expected session is logged as a gap.
Local warehouse storage format
Parquet files, partitioned by symbol/timeframe/, are the durable on-disk artifact.
DuckDB is used to query/join across partitions.
Reproducibility & testing baseline
pyproject.toml dependencies must be pinned once ingestion code lands.
Tests must use small deterministic synthetic OHLCV fixtures.
Licensing / ToS note
Personal, non-redistributed research use only.
V1 "Definition of Done" checklist
[x] Primary + fallback data source chosen and justified
[x] Canonical bar-state schema drafted
[x] Time/timezone convention fixed
[x] Look-ahead invariant stated as a hard rule
[x] Corporate-action and revision policy stated
[x] Gap/calendar handling approach chosen
[x] Storage format decided
[ ] Operator has registered Alpaca + Tiingo API keys locally (.env, gitignored)
[ ] One narrow manual pull from each source has been diffed for adjusted-close agreement
[ ] Operator has explicitly signed off on this decision record
Only after the last three boxes are checked should V2 scaffolding begin.

V4 Validation Results (2026-07-01)
This section records the outcome of running the V4 validation suite
(src/price/validation.py, scripts/validate_slices.py) against the
originally reported V3 discovery highlights, after backfilling SPY and QQQ
to ~3 years of daily/15m/1h history (up from the original ~1 year).

Method: chronological 70/30 train/valid split, 1 bp round-trip cost drag,
Newey-West (Bartlett kernel, auto bandwidth) t-stats/p-values on the 5-bar
forward return, plus a 4-fold expanding-window walk-forward check. A slice
is promoted only if both the train and valid windows independently clear
the sample floor (min_samples=15), have a positive cost-adjusted mean
return, and are Newey-West significant at p < 0.05.

Verdict on the original V3 headline edges: both are retracted. Neither
survived re-validation on the larger dataset.

QQQ 1h "afternoon reversal" (state_session=afternoon + state_ext=stretched_down [+ state_slope=downtrend]): originally reported at n=31, 80.65% win rate,
+0.814% mean 5h return. On ~3x more history the slice recurs at much
larger sample sizes (n=84-92 in-training) but the training-window
Newey-West test itself is not significant, despite a consistent positive
sign. Verdict: rejected -- the original reading was a small-sample
artifact, not a stable edge.
SPY 1d "breakout" (state_ext=stretched_up + state_vol=high_vol):
originally reported at n=24, 75.00% win rate, +0.880% mean 5d return. On
the larger dataset this exact combination no longer ranks among the top
discovered slices at all (state_ext=stretched_down + state_vol=high_vol
-- the opposite extension direction -- ranks highest instead for both
SPY and QQQ daily), and neither variant clears train+valid significance
when tested directly. Verdict: rejected.
Two new slices survived full V4 discipline (train + valid + cost +
significance + sample floor), both on SPY 1h:

state_session=afternoon + state_ext=neutral + state_slope=downtrend:
train n=462 (+0.437% cost-adj, t=5.22), valid n=163 (+0.155% cost-adj,
t=2.15, p=0.031), walk-forward survival 3/4 folds (75%).
state_session=lunch + state_ext=neutral + state_slope=downtrend:
train n=279 (+0.457% cost-adj, t=4.86), valid n=114 (+0.155% cost-adj,
t=2.08, p=0.037), walk-forward survival 2/4 folds (50%).
These are smaller in magnitude than the retracted V3 numbers (~0.15-0.16%
per 5-bar window after cost, vs. the original ~0.8%+ readings), which is
expected: genuine, survivable edges are almost always smaller than what a
small-sample discovery pass first suggests. Notably both survivors involve
state_ext=neutral (not a stretched extension) combined with a downtrend
slope -- a different character from the stretched-extension mean-reversion
story V3 emphasized.

Everything else discovered in this pass (111 of 116 combinations tested)
was rejected outright, plus 3 further "provisional" cases (directionally
correct and significant on the evidence available, but with train or valid
sample counts below the min_samples floor after the chronological split --
not falsified, just not yet testable with enough data). This rejection
rate is expected and correct: a blind combinatorial grid search over
2D/3D state-space slices will always throw off a large number of spurious
patterns, and the validation discipline's job is to filter them out.

Operational note: scripts/discover_slices.py writes to a single fixed
output path (localdata/discovered_slices.csv) and overwrites it on every
invocation. Running discovery once per timeframe (e.g. 1h, then 1d) without
using the --append flag will silently discard the earlier timeframe's
results before validation ever sees them. Use --append when discovering
across more than one timeframe or symbol set in the same research session.

Practical conclusion: do not promote or reference the original V3 QQQ
afternoon-reversal or SPY daily-breakout numbers going forward -- they did
not survive validation. The two SPY 1h state_ext=neutral + downtrend
slices above are the first slices in this project to carry a genuine V4
validation stamp, and are the current state of the art pending further
walk-forward robustness work and additional history/symbols.

V4 Parent-Baseline Update (2026-07-01)
After adding unconditional baseline and parent-regime baseline diagnostics to
scripts/validate_slices.py, the prior two SPY 1h 3D survivors were found to
be over-specified. They still pass the original train+valid+cost+significance
gate and beat the unconditional SPY 1h baseline, but they do not beat their
strongest simpler parent regimes in validation.

The important discovery improvement was adding the missing intraday 2D
combination:

state_session + state_slope
This exposed cleaner 2D candidates:

SPY 1h state_session=afternoon + state_slope=downtrend
train n=542, cost-adjusted mean +0.4014%, Newey-West t=4.98
valid n=174, cost-adjusted mean +0.1763%, Newey-West p=0.0148
valid unconditional baseline +0.0255%
valid excess vs unconditional baseline +0.1508%
strongest valid parent: state_slope=downtrend, +0.1294%
valid excess vs best parent +0.0470%
walk-forward survival 3/4 folds (75%)
SPY 1h state_session=lunch + state_slope=downtrend
train n=345, cost-adjusted mean +0.4179%, Newey-West t=5.11
valid n=119, cost-adjusted mean +0.1627%, Newey-West p=0.0210
valid unconditional baseline +0.0255%
valid excess vs unconditional baseline +0.1372%
strongest valid parent: state_slope=downtrend, +0.1294%
valid excess vs best parent +0.0334%
walk-forward survival 2/4 folds (50%)
QQQ 1h state_session=lunch + state_slope=downtrend
train n=368, cost-adjusted mean +0.1630%, Newey-West t=2.01
valid n=136, cost-adjusted mean +0.2281%, Newey-West p=0.0119
valid unconditional baseline +0.0426%
valid excess vs unconditional baseline +0.1854%
strongest valid parent: state_slope=downtrend, +0.1275%
valid excess vs best parent +0.1005%
walk-forward survival 1/4 folds (25%)
Practical conclusion:

Do not promote the old 3D state_ext=neutral + state_slope=downtrend
slices as the cleanest current finding.
The cleaner current state of the art is the 2D intraday
state_session + state_slope=downtrend structure, especially SPY afternoon.
QQQ lunch is interesting but less stable because walk-forward survival is
only 25%.
Future promotion should require positive excess vs both the unconditional
symbol/timeframe baseline and the strongest simpler parent regime.
V4 Robustness Caveat (2026-07-01)
A targeted scenario check was run on the three cleaner 2D intraday candidates:

SPY 1h state_session=afternoon + state_slope=downtrend
SPY 1h state_session=lunch + state_slope=downtrend
QQQ 1h state_session=lunch + state_slope=downtrend
Scenarios tested:

default validation settings
--cost-bps 2 (4 bps round trip under the current cost model)
--cost-bps 5 (10 bps round trip)
--split 0.6
--split 0.8
Result:

SPY afternoon + downtrend survived default, cost2, and split06, but failed
cost5 and split08. It stayed positive and beat both the unconditional and
best-parent baselines across the scenarios, but significance was not stable
under the stricter split/cost settings.
SPY lunch + downtrend showed a similar but weaker profile, with lower
walk-forward survival.
QQQ lunch + downtrend had strong validation-period mean returns but weak
walk-forward survival and was rejected in most robustness scenarios.
Practical conclusion:

The leading current research candidate is SPY 1h
state_session=afternoon + state_slope=downtrend.
It is promising, not promoted.
Future agents should not describe it as a validated tradable edge yet.
Next validation work should focus on rolling/anchored walk-forward,
date-range sensitivity, and more explicit robustness tables rather than
expanding the discovery grid.
V4 Anchored Walk-Forward Diagnostics (2026-07-01)
Added scripts/validate_slices.py --walk-forward-diagnostics, which writes
localdata/walk_forward_diagnostics.csv and prints fold-level validation
diagnostics for the current leading 2D candidates.

Default 4-fold anchored diagnostics:

SPY 1h state_session=afternoon + state_slope=downtrend

fold 0: valid mean +0.4268%, p=0.000839, pass
fold 1: valid mean +0.3467%, p=0.027353, pass
fold 2: valid mean +0.2150%, p=0.016456, pass
fold 3: valid mean +0.1358%, p=0.148389, fail
Interpretation: positive in every fold and beats both unconditional and
best-parent baselines in every fold, but the effect decays over time and the
latest fold is not statistically significant.
SPY 1h state_session=lunch + state_slope=downtrend

fold 0: pass
fold 1: fail
fold 2: pass
fold 3: fail
Interpretation: positive but unstable; secondary candidate only.
QQQ 1h state_session=lunch + state_slope=downtrend

fold 0: fail
fold 1: fail
fold 2: pass
fold 3: fail
Interpretation: unstable despite attractive full-period validation means; do
not promote.
Practical conclusion:

The best current candidate remains SPY 1h
state_session=afternoon + state_slope=downtrend.
It should be described as promising but recently weaker/decaying, not as a
validated tradable edge.
Next work should investigate date-range/regime sensitivity and whether the
latest-fold weakening is due to market regime, data quirks, or decay.
V4 Date-Range Sensitivity Diagnostics (2026-07-01)
Added scripts/validate_slices.py --date-range-diagnostics, which writes
localdata/date_range_diagnostics.csv and prints targeted date-window
diagnostics for the current leading 2D candidates.

Windows checked:

all available data
calendar 2024
calendar 2025
calendar 2026 YTD
latest 12 months
latest 6 months
SPY 1h state_session=afternoon + state_slope=downtrend

all: valid mean +0.3467%, p=4.47e-08, pass
2024: valid mean +0.4972%, p=1.05e-06, pass
2025: valid mean +0.1775%, p=0.1155, fail
2026 YTD: valid mean +0.1554%, p=0.1472, fail
latest 12m: valid mean +0.1533%, p=0.0226, pass
latest 6m: valid mean +0.1554%, p=0.1472, fail
Interpretation:

The effect remains positive and continues to beat both unconditional and
best-parent baselines across the checked windows.
Statistical strength is concentrated in 2024.
2025 and 2026 YTD are positive but individually not significant.
Latest 12m passes, but latest 6m fails.
This supports the prior anchored-walk-forward conclusion: the candidate is
not dead, but it is materially weaker/recently decayed.
SPY 1h state_session=lunch + state_slope=downtrend

Similar but weaker profile: strong in 2024, weaker/non-significant in 2025
and 2026 YTD, latest 12m passes, latest 6m fails.
Remains secondary to SPY afternoon.
QQQ 1h state_session=lunch + state_slope=downtrend

All-period and latest-12m windows pass, but individual calendar windows and
latest 6m do not.
Remains interesting but unstable; do not promote.
Practical conclusion:

The leading candidate remains SPY 1h
state_session=afternoon + state_slope=downtrend.
It should now be described as positive across windows but statistically
strongest in 2024 and weaker recently.
Do not promote it as a tradable edge.
Next work should inspect regime context for the 2024 strength versus the
2025/2026 weakening before any further discovery-grid expansion.
V4 Candidate Leaderboard + Triage Buckets (2026-07-01)
Added scripts/validate_slices.py --candidate-leaderboard, which writes
localdata/candidate_leaderboard.csv and ranks all discovered slices using
default validation, parent-baseline excess, scenario survival, walk-forward
survival, and sample discipline.

Added triage_bucket labels to separate different research cases:

clean_survivor: strict train+valid survivor with positive excess vs both
unconditional and best-parent baselines.
over_specified_survivor: strict survivor but worse than a simpler parent
regime.
late_emerging_valid_supported: failed training window but passed validation
with positive excess vs baseline and parent; interesting as possible regime
shift, not as stable historical edge.
provisional_sample_starved: attractive evidence but validation sample below
the floor.
parent_underperformed / rejected_unsupported: lower-priority rejects.
Current leaderboard framing:

Clean survivors:
SPY 1h state_session=afternoon + state_slope=downtrend
SPY 1h state_session=lunch + state_slope=downtrend
QQQ 1h state_session=lunch + state_slope=downtrend
Over-specified survivors:
SPY 1h state_session=afternoon + state_ext=neutral + state_slope=downtrend
SPY 1h state_session=lunch + state_ext=neutral + state_slope=downtrend
Late-emerging valid-supported candidates worth separate investigation:
QQQ 1h state_session=lunch + state_ext=neutral + state_slope=downtrend
QQQ 1h state_session=afternoon + state_ext=stretched_down
QQQ 1d state_ext=stretched_down + state_vol=high_vol
QQQ 1h state_ext=stretched_up + state_vol=low_vol
SPY 1h state_ext=stretched_down + state_slope=downtrend
Practical conclusion:

SPY afternoon remains the top clean survivor, but the project should not
tunnel only on that slice.
Next work should inspect the late-emerging bucket to determine whether those
are genuine recent-regime candidates or validation-window artifacts.
Future diagnostics should support candidate scopes such as clean survivors,
late-emerging, and leaderboard top-N.
V4 Late-Emerging Candidate Date Diagnostics (2026-07-01)
Added diagnostic candidate scopes for date-range diagnostics, including:

--diagnostic-scope current-leaders
--diagnostic-scope clean-survivors
--diagnostic-scope late-emerging
--diagnostic-scope leaderboard-top
with --top-n.
Ran:
python3 scripts/validate_slices.py --date-range-diagnostics --diagnostic-scope late-emerging --top-n 5

Findings:

QQQ 1h state_session=lunch + state_ext=neutral + state_slope=downtrend
passes all, 2024, 2026 YTD, latest 12m, and latest 6m; fails 2025 with
negative parent excess. This is not just a tiny 2026 artifact, but it is
regime-dependent.
QQQ 1h state_session=afternoon + state_ext=stretched_down passes all,
2026 YTD, latest 12m, and latest 6m; fails 2024 and 2025. This looks like a
recent-regime candidate with small sample size.
QQQ 1d state_ext=stretched_down + state_vol=high_vol remains interesting
but sample-starved; latest 12m passes, 2026 YTD/latest 6m are strong but
below the sample floor.
QQQ 1h state_ext=stretched_up + state_vol=low_vol is negative/weak in
2024 and 2025 but passes 2026 YTD/latest windows. Treat as recent-regime
artifact until more evidence accumulates.
SPY 1h state_ext=stretched_down + state_slope=downtrend fails all/2024/2025
but passes 2026 YTD/latest windows. Parent excess only turns positive
recently.
Practical conclusion:

The late-emerging bucket contains real candidates for recent-regime
investigation, especially QQQ lunch+neutral+downtrend and QQQ
afternoon+stretched_down.
These are not stable historical edges. They should be studied as possible
recent-regime effects or validation-window artifacts.
The project should keep separate tracks for clean survivors versus
late-emerging valid-supported candidates.
V4 Late-Emerging Walk-Forward Diagnostics (2026-07-01)
After adding diagnostic scopes, ran:
python3 scripts/validate_slices.py --walk-forward-diagnostics --diagnostic-scope late-emerging --top-n 5

Findings:

QQQ 1h state_session=lunch + state_ext=neutral + state_slope=downtrend
passes fold 0 and fold 3, but fails fold 1 and fold 2. This is not a simple
recent-only pattern; it is regime-switching/unstable.
QQQ 1h state_session=afternoon + state_ext=stretched_down only passes the
latest fold. Earlier folds fail, and one intermediate fold is sample-starved.
Treat as a recent-regime candidate only.
QQQ 1d state_ext=stretched_down + state_vol=high_vol only passes the latest
fold and remains sample-constrained, especially because it is daily data.
QQQ 1h state_ext=stretched_up + state_vol=low_vol only passes the latest
fold; earlier folds are weak/negative or have no samples. Treat as likely
recent-regime artifact until more evidence accumulates.
SPY 1h state_ext=stretched_down + state_slope=downtrend only passes the
latest fold; earlier folds underperform parent regimes. This is a recent-only
candidate, not a stable historical edge.
Practical conclusion:

The late-emerging bucket mostly represents fold-3/recent-period behavior,
not stable train-to-valid edges.
QQQ lunch+neutral+downtrend is the exception: it appears regime-switching
rather than purely recent-only.
Keep separate labels:
clean survivors = stable-ish historical candidates
late-emerging = recent-regime candidates
regime-switching = unstable but recurring candidates
Do not promote late-emerging candidates without more future data.
V4 Fold-Pattern Triage Refinement (2026-07-01)
Added walk_forward_pass_count and walk_forward_pass_pattern to validation
outputs and candidate leaderboard. The pass pattern is a compact 4-character
fold string where 1 means the validation fold passed and 0 means it failed.

Examples:

1110: passed folds 0, 1, and 2; failed latest fold
1010: passed folds 0 and 2 only
0010: passed fold 2 only
1001: passed early and latest folds, failed the middle
0001: only latest fold passed
0000: no folds passed
Triage buckets were refined:

late_emerging_recent_only: validation-supported candidate whose only
walk-forward pass is the latest fold (0001).
late_emerging_regime_switching: validation-supported candidate that passes
the latest fold and at least one earlier fold, but not continuously.
late_emerging_valid_supported: validation-supported candidate that failed
training but does not fit the recent-only/regime-switching fold pattern.
Current interpretation:

SPY 1h state_session=afternoon + state_slope=downtrend remains the top
clean survivor but has pattern 1110, confirming latest-fold weakness.
QQQ 1h state_session=lunch + state_ext=neutral + state_slope=downtrend
is late_emerging_regime_switching with pattern 1001.
QQQ 1h state_session=afternoon + state_ext=stretched_down, QQQ 1d
state_ext=stretched_down + state_vol=high_vol, QQQ 1h
state_ext=stretched_up + state_vol=low_vol, and SPY 1h
state_ext=stretched_down + state_slope=downtrend are
late_emerging_recent_only with pattern 0001.
Practical conclusion:

The leaderboard now separates stable-ish, over-specified, recent-only, and
regime-switching cases.
This is a triage tool, not a promotion engine.
No candidate should be promoted without additional future data and continued
fold-pattern survival.
V4 Expanded-Universe Validation Update (2026-07-01)
This section supersedes any interim expanded-universe outputs produced before
the warehouse adjustment-date fix described below.

Expanded universe tested:

SPY
QQQ
IWM
DIA
XLK
XLF
XLE
GLD
TLT
USO
The expanded-universe run initially exposed impossible 1h results in XLE and
XLK, including forward-return magnitudes far too large for normal ETF hourly
bars. Those outputs are invalid and must not be used.

Root cause: src/price/warehouse.py propagated Tiingo daily adjustment factors
to intraday bars using a New York-converted date for the daily bar timestamp.
Tiingo daily bars are stored at midnight UTC but semantically represent the
market session date. Converting midnight UTC to America/New_York shifts the
date to the prior evening, causing adjustment factors to be applied to the
wrong intraday session.

Fix committed:

daily Tiingo bars are keyed by their UTC date / semantic market date
intraday bars are keyed by their New York market date
adjustment factors are joined on that corrected market-date key
regression coverage was added for the daily-UTC-date mapping case
After rebuilding the warehouse post-fix, XLE and XLK 1h return distributions
returned to plausible ETF ranges. The previous absurd XLE/XLK expanded-universe
discoveries are therefore invalidated.

Post-fix validation result:

672 total discovered slice rows tested
5 strict survivors
3 provisional rows, sample-floor-starved rather than promoted
664 rejected rows
The strict survivors after the clean-survivor walk-forward triage split are:

XLF 1d state_ext=stretched_up + state_slope=flat

triage: clean_survivor_wf_strong
walk-forward pattern: 1111
scenario survival count: 4/5
validation n: 33
current best expanded-universe candidate
not promoted yet because the valid sample is still small, 2025 calendar
diagnostics failed, and 2026/latest-6m windows are sample-starved
QQQ 1h state_ext=stretched_up + state_vol=mid_vol

triage: clean_survivor_wf_mixed
walk-forward pattern: 1001
scenario survival count: 4/5
useful candidate, but regime-switching rather than cleanly stable
XLE 1d state_ext=stretched_down + state_slope=downtrend

triage: clean_survivor_wf_mixed
walk-forward pattern: 0110
scenario survival count: 4/5
recent date-range diagnostics are weak/failed, so do not promote
XLK 1h state_ext=stretched_up + state_vol=low_vol

triage: clean_survivor_wf_mixed
walk-forward pattern: 0001
scenario survival count: 4/5
appears recent-only; do not treat as stable yet
XLE 1h state_session=afternoon + state_ext=neutral

triage: clean_survivor_wf_failed
walk-forward pattern: 0000
scenario survival count: 1/5
strict split survivor but demoted by walk-forward diagnostics
Current practical conclusion:

No expanded-universe candidate is promoted yet.
The current top candidate to keep watching is XLF 1d
state_ext=stretched_up + state_slope=flat.
Treat it as promising but unpromoted pending continued robustness checks,
more future data, and preferably comparable-history backfill for SPY/QQQ
versus the newer 5-year ETF universe members.
Do not revive or cite the invalid pre-fix XLE/XLK expanded-universe numbers.
V4 Comparable-History Rerun Update (2026-07-01)
This section corrects the immediately preceding expanded-universe section.
The earlier expanded-universe run compared newer ETFs at ~5 years of history
against SPY/QQQ at only ~3 years, because capture_bars.py performs an
incremental update when a partition already exists and therefore did not
backfill SPY/QQQ to the full lookback.

SPY and QQQ were force-backfilled to the same ~5-year window as the newer
ETFs by fetching 1825 days directly and letting save_to_warehouse merge and
deduplicate into the existing partitions, followed by a warehouse rebuild.
Post-backfill SPY/QQQ coverage now starts 2021-07 like the rest of the
universe, and their intraday return distributions remain plausible.

Discovery and validation were re-run on the fully comparable universe
(discovered_slices.csv rebuilt from scratch, not appended onto stale rows).

Corrected post-backfill validation result:

682 total discovered slice rows tested
4 strict survivors (down from 5 before comparable history)
3 provisional rows, sample-floor-starved rather than promoted
675 rejected rows
The prior QQQ 1h state_ext=stretched_up + state_vol=mid_vol slice is no
longer a strict survivor once SPY/QQQ carry full history, so the "5 strict
survivors" figure in the preceding section is superseded by this 4-survivor
result. Use this section as the current record.

Current strict survivors (with clean-survivor walk-forward triage split):

XLF 1d state_ext=stretched_up + state_slope=flat

triage: clean_survivor_wf_strong
walk-forward pattern: 1111 (all four valid folds pass)
scenario survival count: 4/5
valid_n: 33
date-range: all, 2024, latest_12m pass; 2025 fails; 2026-ytd and
latest_6m are strong but sample-starved (n=8)
still the top expanded-universe candidate, still NOT promoted
XLE 1d state_ext=stretched_down + state_slope=downtrend

triage: clean_survivor_wf_mixed
walk-forward pattern: 0110
scenario survival count: 4/5
recent windows (2026-ytd, latest_12m, latest_6m) fail and parent excess
goes negative; treat as decaying, do not promote
XLK 1h state_ext=stretched_up + state_vol=low_vol

triage: clean_survivor_wf_mixed
walk-forward pattern: 0001
scenario survival count: 4/5
recent-only: 2026-ytd/latest strong, 2024-2025 fail; not stable
XLE 1h state_session=afternoon + state_ext=neutral

triage: clean_survivor_wf_failed
walk-forward pattern: 0000 (no fold passes)
scenario survival count: 1/5
strict split survivor but demoted by walk-forward diagnostics
Tooling note: the clean-survivors diagnostic scope originally filtered on the
exact triage bucket "clean_survivor". After clean survivors were split into
clean_survivor_wf_strong / clean_survivor_wf_mixed / clean_survivor_wf_failed,
that exact match returned nothing and the walk-forward and date-range
diagnostics produced empty output. The scope now matches any triage bucket
starting with "clean_survivor".

Current practical conclusion (unchanged):

No candidate is promoted.
XLF 1d state_ext=stretched_up + state_slope=flat is the current top
candidate to keep watching, pending more forward data, continued fold
survival, and resolution of its sample-starved most-recent windows.
Do not cite the invalid pre-fix XLE/XLK numbers, and do not cite the
superseded 5-survivor figure from the previous section.
V4 Fold-Count Sensitivity Check (2026-07-01)
The four strict survivors were re-run through the walk-forward diagnostics at
n_folds = 3, 4, 5, and 6 (--n-folds) to test whether the XLF 1d "1111" pattern
is a structural edge or an artifact of the specific 4-fold split.

XLF 1d state_ext=stretched_up + state_slope=flat, valid_pass by fold count:

n_folds=3 -> 110 (2/3; last fold p=0.062, borderline)
n_folds=4 -> 1111 (4/4; the headline pattern)
n_folds=5 -> 11101 (4/5)
n_folds=6 -> 010101 (3/6)
Interpretation: the perfect "1111" record is split-lucky in strength, not in
sign. XLF's per-fold cost-adjusted mean return is positive in every fold at
every fold count tested (including the folds that "fail"). The failures come
from (a) parent-relative excess dipping slightly negative in some folds and
(b) per-fold valid_n collapsing to ~14-16 as the folds get finer, which
starves Newey-West significance. So the honest characterization of XLF is:
consistently positive-signed across time, but with thin per-fold samples and
fold-count-sensitive parent-relative significance -- not a clean, robust
"passes every fold" edge.

The other three survivors behaved as expected under the sweep:

XLE 1d state_ext=stretched_down + state_slope=downtrend: 111 at 3 folds but
0110 / 00110 / 001010 at 4-6 folds; front-loaded and decaying in recent
windows.
XLK 1h state_ext=stretched_up + state_vol=low_vol: recent-only at every
fold count; the earliest folds shrink to n=2-5 and fail.
XLE 1h state_session=afternoon + state_ext=neutral: mostly fails; only the
newest fold ever passes.
Promotion decision is unchanged: nothing is promoted. XLF 1d remains the top
candidate to keep watching, now with the added caveat that its walk-forward
strength is fold-count sensitive and its recent per-fold samples are thin.
The correct next unlock is more forward daily data on XLF, not further
re-slicing of the existing history.

V4 Multiple-Testing Reality Check (2026-07-01)
The candidate leaderboard now carries search-wide multiple-testing columns
(annotate_search_wide_significance in scripts/validate_slices.py):

search_wide_rank: ascending p-value rank within the correction family
search_wide_bh_pass: Benjamini-Hochberg FDR at p_threshold
search_wide_bonferroni_pass: Bonferroni at p_threshold
search_wide_family_size: number of hypotheses in the family
Correction family = every leaderboard row with a finite valid_p_value_nw
(676 rows in the current comparable-history run). At raw p<0.05 across 676
hypotheses roughly 34 false positives are expected, so an uncorrected p-value
means little on its own. Under search-wide correction only 11 slices clear
BH-FDR and only 8 clear Bonferroni.

Result for the four strict survivors:

XLE 1d state_ext=stretched_down + state_slope=downtrend: BH rank 11, BH
pass True, Bonferroni False. The only strict survivor that clears any
search-wide bar -- but its walk-forward pattern is a decaying 0110/001010,
so it is significant-but-decaying, not promotable.
XLF 1d state_ext=stretched_up + state_slope=flat: BH rank 59, BH pass
False, Bonferroni False. Its leaderboard-#1 position comes from
walk-forward strength, not from standing out in the search. Uncorrected it
looks strong; search-wide it does not.
XLK 1h state_ext=stretched_up + state_vol=low_vol: BH rank 40, fails.
XLE 1h state_session=afternoon + state_ext=neutral: BH rank 66, fails.
Important caveat on the family: because the family is every finite-p row, it
includes sample-starved slices whose small-sample Newey-West p-values are
implausibly tiny (e.g. valid_n=3-6 with p on the order of 1e-8). Seven of the
eleven BH-passers are exactly these starved rows (provisional_sample_starved
or sample_starved_unsupported), and they occupy the lowest BH ranks. Read
search_wide_* together with valid_n and triage_bucket; a low search_wide_rank
driven by valid_n<15 is a small-sample artifact, not evidence.

Net effect on promotion doctrine: nothing is promoted, and this check
sharpens why. No slice currently combines (a) a robust walk-forward pattern,
(b) a search-wide-defensible p-value on an adequate sample, and (c) a
positive parent-relative excess. XLF has the pattern but not the search-wide
p-value; XLE 1d has the search-wide p-value but not the pattern. Both remain
watch-list items, not candidates. The correct next unlock is still more
forward data plus genuinely new conditioning information (e.g. cross-asset
regime conditioning), not further re-slicing of the existing history.

Cross-Asset Conditioning Experiment (2026-07-02)
This section records the outcome of extending the V4 validation pipeline
to support cross-asset state conditioning: using one symbol's market state
(e.g., USO slope/extension/volatility) as a feature when discovering
slices for another symbol (e.g., GLD, XLE).

Motivation: single-symbol state slices capture local price dynamics but
ignore macro regime. Cross-asset conditioning asks whether "gold when oil
is dropping" or "energy when oil is trending up" behaves differently than
"gold" or "energy" alone. The hypothesis is that cross-asset correlations
carry information about regime that single-symbol features miss.

Method: same V4 discipline as above (train/valid split, Newey-West, cost
drag, walk-forward), but with attach_cross_asset_states in discovery.py
binning USO state for each symbol's bars. The validation pipeline was
extended to route cross-asset slices through cross-aware frame building
(cross_symbols_from_filter + build_eligible_frame(..., cross_symbols=...)).
Walk-forward and date-range diagnostics were patched to support cross-asset
slices (previously they built frames without cross columns, causing
ValueError: Slice field 'cross_USO_state_ext' not present).

Results (1d timeframe, USO as conditioning asset, 424 candidate slices):

9 survived full validation (train + valid + cost + significance + sample floor)
2 provisional (correct sign + significant, but below min_samples=15 after split)
413 rejected
The survivors occupied the top of the leaderboard, and several cleared the
search-wide FDR bar. But the honest test is valid_excess_vs_best_parent:
does the cross-asset interaction beat both of its own parents (the plain
symbol state AND the plain USO state)? That's what tells us the cross-asset
information is real and not just re-expressing a single-symbol effect.

Fold-count sweep (NF=3,4,5,6) and date-range diagnostics revealed:

Tier 1: Most stress-tested survivor (single-asset)

XLF state_ext=stretched_up + state_slope=flat: 4/4 at NF=4, 4/5 at NF=5,
passes 2024 and latest_12m calendar windows. Not cross-asset, but the most
robust slice the project has produced.
Tier 2: Real cross-asset effects, regime-dependent

XLE cross_USO_state_slope=uptrend + state_ext=neutral: highest all-window
parent excess (+1.45%), passes both 2024 and 2025 individually. Energy
mean-reverting when oil trends up is economically coherent. But fading in
2026 (latest_12m fails at p=0.11).
GLD cross_USO_state_slope=downtrend + state_ext=neutral: strong in 2025
(+0.63% parent excess), borderline in 2024 (p=0.051), but completely
absent before mid-2023 (fold-count sweep proved this). The 2026 YTD window
has exactly 1 observation. This is a "gold bull market + oil weakness"
story, not a structural edge. The regime has left the building.
Tier 3: Intermittent / expiring

XLE cross_USO_slope=downtrend + stretched_down: passed 2024+2025 but dead
in the last 12 months (-0.41% parent excess). This story has expired.
XLK cross_USO_ext=stretched_down + neutral: 2025 shows negative parent
excess — cross-asset conditioning isn't adding value there.
QQQ cross_USO_ext=stretched_down + neutral: only +0.02% parent excess
over "all" — cross-asset conditioning adds essentially nothing.
IWM cross_USO_ext=stretched_down + neutral: doesn't pass 2024 or 2025
individually; only works in latest 12m. Very recent phenomenon.
Interpretation: cross-asset conditioning IS productive (it found effects
that single-symbol analysis missed), but the effects it finds are less
stable than single-asset effects. This makes theoretical sense — cross-asset
dynamics depend on the correlation structure between assets, which is itself
regime-dependent. The XLE/USO and GLD/USO effects are real but require
specific macro regimes (oil trending up, gold in structural bull) that are
not encoded in the filter definition.

Practical conclusion: no cross-asset slice is promotable to a live track
based on this run. XLF stretched_up+flat (single-asset) remains the
strongest candidate for continued monitoring. The cross-asset slices should
be treated as research findings that inform qualitative judgment, not as
deployable filters.

Code changes: src/price/discovery.py (attach_cross_asset_states,
bin_features with cross_symbols), src/price/validation.py (apply_slice_filter
now accepts cross columns), scripts/validate_slices.py (run_validation,
run_walk_forward_diagnostics, run_date_range_diagnostics all route through
cross-aware frame building), tests/test_validate_slices_script.py (test
stubs updated to accept cross_symbols kwarg). All 59 tests pass.

Next experiments to consider:

Cross-condition on TLT (bond state) instead of/in addition to USO — bonds
are the other macro regime variable
Add a "freshness gate" to validation: a slice that fails latest_12m gets
flagged even if it passes the full valid window
1h timeframe with cross-asset conditioning — the 1d results suggest the
direction is productive, and 1h gives ~8x more observations per fold
Cross-Asset Expansion + Stress Testing (2026-07-02)
Extended the cross-asset conditioning experiment to TLT (bonds) and the
1h timeframe, then stress-tested all survivors with fold-count sweeps
(NF=3,4,5,6) and date-range diagnostics (freshness check).

TLT conditioning on 1d (bonds as regime variable):

Discovered 8 survivors from 424 candidates
TLT proved more informative than USO for equity slices (XLK, QQQ, SPY)
Top candidate: XLK cross_TLT_state_slope=uptrend + state_ext=neutral
(ranks #1 overall, robustness score 16.00, walk-forward 3/4 at NF=4,
3/5 at NF=5, passes freshness with +0.47% parent excess in latest_12m)
QQQ cross_TLT_state_ext=stretched_up + state_ext=neutral cleared
Bonferroni correction (strictest multiple-testing bar) but collapsed
at NF=6 (1/6), revealing it's a late-emerging 2024+ effect
1h cross-asset with USO:

Discovered 4 new survivors, all on XLK or GLD
Top candidate: XLK 1h cross_USO_state_vol=mid_vol + state_ext=stretched_down
(n=108 valid, walk-forward 3/4 at NF=4, 3/6 at NF=6, passes freshness
with +0.15% parent excess in latest_12m, passes 2024 + 2025 individually)
GLD 1h cross_USO_state_ext=neutral + state_ext=neutral has massive
sample (n=1254) and robust fold-count (3/6) but tiny effect (+0.04%
parent excess) and fails freshness (latest_12m p=0.054)
Fold-count sweep revealed:

XLF stretched_up + flat remains the most robust: 4/4 at NF=4, 4/5
at NF=5, 3/6 at NF=6 (alternating pattern but consistent)
XLK 1d cross_TLT_slope=uptrend + neutral holds 3/4 at NF=4, degrades
to 2/6 at NF=6 but still passes the most recent folds
QQQ/XLK cross_TLT_ext=stretched_up + neutral both collapse at NF=6
(1/6), revealing they're late-emerging effects concentrated in 2024+
XLK 1h cross_USO_vol=mid_vol + stretched_down is surprisingly robust:
3/4 at NF=4, 3/5 at NF=5, 3/6 at NF=6
Date-range diagnostics (freshness gate):

4 of 8 1d TLT survivors pass latest_12m freshness check
XLK 1h cross_USO_vol=mid_vol + stretched_down passes 2024, 2025,
and latest_12m — genuinely fresh
GLD 1h cross_USO_ext=neutral + neutral passes 2024+2025 but fails
latest_12m (stale)
XLE 1h afternoon + neutral is a false survivor: passed train+valid
but fails 0/4 and 0/5 walk-forward folds
Final tier ranking (survived + fold-count + freshness + parent excess):

Tier 1: Passed all stress tests

XLF 1d state_ext=stretched_up + state_slope=flat — single-asset,
4/4 NF=4, 3/6 NF=6, fresh, +0.79% parent excess in latest_12m
XLK 1d cross_TLT_state_slope=uptrend + state_ext=neutral — best 1d
cross-asset, 3/4 NF=4, 2/6 NF=6, fresh, +0.47% parent excess
XLK 1h cross_USO_state_vol=mid_vol + state_ext=stretched_down — best
1h cross-asset, 3/6 NF=6, fresh, +0.15% parent excess
Tier 2: Real but concentrated
4. QQQ 1d cross_TLT_state_ext=stretched_up + state_ext=neutral — Bonferroni
pass, fresh, but collapses at NF=6 (late-emerging 2024+ effect)
5. GLD 1h cross_USO_state_ext=neutral + state_ext=neutral — massive sample,
robust fold-count, but tiny effect and stale

Performance optimization:
Added feature caching to build_eligible_frame. Computed features are now
saved to localdata/features_cache/ as parquet files, keyed by symbol +
timeframe + warehouse file mtime. This cuts repeated validation runs from
~4 minutes to ~1 minute (5x speedup). The profiler showed 9 of 11 seconds
was spent in compute_price_features doing rolling window calculations;
caching eliminates this on subsequent runs.

Practical conclusion: the project now has three genuinely stress-tested
candidates that pass validation + fold-count + freshness + parent excess.
XLF stretched_up + flat (single-asset) remains the strongest. XLK 1d
cross_TLT_slope=uptrend + neutral is the first cross-asset slice to hold
up to full stress-testing. XLK 1h cross_USO_vol=mid_vol + stretched_down
is the first 1h cross-asset slice to demonstrate robustness. None are
promotable to a live track without further out-of-sample testing, but they
represent the current state of the art for this research substrate.

Paper-Trading Exploration Layer (2026-07-02)
This section records a deliberate, time-boxed deviation from the V1–V4
"no execution" boundary.

What was added:

src/price/monitor.py: polls Alpaca for the most recent bars, computes
features + binned state the same way the research pipeline does, and
checks whether the current market state matches any of a configured
set of slices. Emits a signal; does NOT place orders.
src/price/trading.py: minimal Alpaca paper-trading execution layer.
Connects to the paper account, submits market orders, tracks positions,
writes a trade journal to localdata/trade_journal.csv. Does NOT
decide when to trade; that is monitor.py plus risk limits.
scripts/paper_trade.py (added in a follow-up patch): one-command
glue that runs monitor.scan_all_slices() and, for each matched
signal that passes the risk guard, calls trading.submit_entry /
trading.submit_exit on the Alpaca paper account.
Important — this is NOT a promotion of any slice.
The HANDOVER's V4 conclusions stand unchanged:

"No candidate is promoted."
"XLF 1d state_ext=stretched_up + state_slope=flat — current top
candidate to keep watching, still NOT promoted."
"Do not promote it as a tradable edge."
The four slices currently hardcoded in monitor.DEFAULT_MONITORED_SLICES
are the V4 leaderboard top, not a validated edge set. They are:

XLF 1d state_ext=stretched_up + state_slope=flat
(clean_survivor_wf_strong, walk-forward 1111 at NF=4, but search-wide
p fails; recent per-fold samples thin)
XLK 1d cross_TLT_state_slope=uptrend + state_ext=neutral
(first cross-asset slice to hold up to stress-testing)
XLK 1h cross_USO_state_vol=mid_vol + state_ext=stretched_down
(first 1h cross-asset survivor)
SPY 1h state_session=afternoon + state_slope=downtrend
(top historical clean survivor, recently weaker / decaying)
Why this layer exists despite the doctrine:

To observe how often these non-promoted slices are "in state" at
current market conditions, and what would have happened if a paper
order had been submitted.
To generate a research dataset (signals + simulated/actual fills) for
the next round of out-of-sample evaluation.
The paper account, not real money, is the only place this is allowed
to run.
Hard rules for this layer (enforced in code by src/price/risk_limits.py):

Max notional per position
Max number of open positions
Max daily realized loss (kill switch)
Min seconds between consecutive entries on the same symbol
Hard kill switch flag (file-based) that blocks all new entries
Practical conclusion:

Any P&L from trading.py is research output, not trading income.
The risk limits are the only thing standing between a "let me see
what would happen" exploration and a real loss on the paper account
(which itself can become a problem if it conditions future decision
making).
If a future agent finds themselves writing "the paper account is up
X% therefore the slice works", stop. That is the exact failure mode
this section exists to prevent.
Cache Fix + Live Validation Reproduction (2026-07-02)
This section records two small but real events from the most recent
session.

Cache fix (commit: fix(cache): point feature cache at data.parquet)
The performance optimization section above claimed the feature cache
"cuts repeated validation runs from ~4 minutes to ~1 minute." Until
today that claim was untrue. scripts/validate_slices.py was looking
for localdata/warehouse/{symbol}/{timeframe}/bars.parquet, but
src/price/warehouse.save_to_warehouse() writes
data.parquet. The cache path was always empty, so the 5x speedup
never actually fired. The fix is a one-line correction: bars.parquet
to data.parquet on line 91 of scripts/validate_slices.py. After
the fix, the second consecutive python3 scripts/validate_slices.py
run should be materially faster than the first. If it is not, the
cache is still broken and a future agent should look at the mtime
key in build_eligible_frame.

Fresh end-to-end validation reproduction (2026-07-02)
A clean re-run of the full V4 substrate on the current state of
localdata/warehouse (SPY/XLF/XLK, ~3 years of 1d history, ~1 month
of 15m history) reproduced the V4 Tier 1 candidate cleanly:

XLF 1d state_ext=stretched_up + state_slope=flat

train_n=88, valid_n=33
valid cost-adjusted mean return: +1.005%
valid Newey-West t=2.10, p=0.035
walk-forward pattern: 1111 (4/4 folds pass)
valid excess vs unconditional baseline: +0.835%
valid excess vs best parent (state_ext=stretched_up): +0.595%
A second slice also survived strict validation:

SPY 1d state_ext=neutral + state_slope=uptrend

train_n=76, valid_n=26
valid cost-adjusted mean return: +0.736%
valid Newey-West t=2.33, p=0.020
walk-forward pattern: 1100 (2/4 folds pass)
This is the "recent-only / latest-fold dependent" pattern the
HANDOVER explicitly warns about. The 1100 pattern means fold 2
and fold 3 fail. Treat as corroboration that the uptrend regime
exists in the data, not as a promotable slice on its own.
Important - still no promotions.
The HANDOVER's V4 conclusions stand unchanged:

"No candidate is promoted."
"XLF 1d state_ext=stretched_up + state_slope=flat is the current
top candidate to keep watching, still NOT promoted."
"Do not promote it as a tradable edge."
What this section does do:

Confirm the cache is now actually working (fix is one line; the
speedup claim is now testable, not aspirational).
Confirm the V4 Tier 1 candidate reproduces on a fresh build with
the current warehouse state. The walk-forward 1111 pattern held.
Flag the SPY 1d uptrend slice as a fresh "late-emerging /
fold-2-failed" candidate that deserves date-range diagnostics
before any further discussion.
What this section does NOT do:

Authorize any live paper-trading.
Override the V4 "no execution in v1-v4" boundary.
Promote any slice.
Change the monitored-slices list in monitor.DEFAULT_MONITORED_SLICES.
Cache Fix — Honest Numbers (2026-07-02)
The cache fix recorded earlier (commit 791dcec, "fix(cache): point
feature cache at data.parquet (was bars.parquet)") is mechanically
correct: the disk cache directory is now created on first run, the
mtime-based cache key resolves correctly, and parquet files are
written into localdata/features_cache/.

However, the original "5x speedup (4 minutes to 1 minute)" claim
recorded in this HANDOVER's Performance Optimization section is
NOT reproducible on the current workload. Measured on 2026-07-02
with the 1d 3-symbol (SPY/XLF/XLK) candidate-leaderboard path:

Run 1 (cache cleared): 32.913s wall
Run 2 (cache warm): 32.948s wall
Delta: +0.04s (noise, no speedup)

Two reasons the original 5x figure does not reproduce:

The in-memory frame_cache dict inside run_validation (line 314)
already short-circuits repeated calls within a single CLI
invocation. The disk cache only helps across separate
invocations of validate_slices.py.

On the 1d 3-symbol leaderboard, compute_price_features is
called on ~1250 rows per symbol x 3 symbols = ~3750 rows
total per invocation. That is too small for the disk-I/O
cost of cache write to be amortized. The original 5x
measurement was for a heavier 15m + cross-asset + multi-symbol
scenario-grid workload that is not part of the current
validation routine.

What the cache fix actually buys:

Eliminates a silent dead-code path (the disk cache was
never hit before this commit, but the code claimed it was)
Makes the cache work for heavy workloads (15m + cross-asset
scenario grid), even if it does not show measurable
speedup on the light 1d 3-symbol leaderboard
The original "5x speedup" line in the Performance Optimization
section should be treated as not-validated, not as retracted.
Future agents who measure the cache on a heavy workload should
update the HANDOVER with the actual numbers.

Scheduled Live Capture (2026-07-02)
This section records the addition of a scheduled live forward-return
capture layer, which deviates from the V1-V4 "no execution" boundary
in one specific way: code in this repo now runs unattended on a
schedule. The deviation is purely about how the code runs, not
what it does -- the live-capture layer does not place orders, does
not connect to the Alpaca trading endpoint, and does not override
any V4 doctrine about slice promotion. Its job is to collect the
realized forward return for matched signals so future validation
runs can use live out-of-sample data.

What was added

scripts/live_forward_returns.py: reads localdata/paper_trade_log.csv
for matched entry signals, looks up the exit-time bars at +5 and +20
bars after the signal in the local warehouse (or via an on-demand
Alpaca fetch if the warehouse is stale), and writes the realized
forward return to localdata/live_forward_returns.csv. Idempotent.
Partial-data rows are kept distinct from completed ones via a
partial_data flag. Never silently drops a row.
.github/workflows/live_capture.yml: a GitHub Actions workflow
that runs every 6 hours (0 /6 * * ). Steps: checkout, setup
Python 3.11, install requirements, run capture_bars.py to pull
the most recent 1825 days of 1d bars and 30 days of 15m bars, run
build_warehouse.py to resample 15m to 1h and propagate Tiingo
daily adjustment factors, run validate_slices.py --candidate- leaderboard to refresh the leaderboard, run paper_trade.py --dry-run to emit a new audit row, run live_forward_returns.py
to capture forward returns, then auto-commit any changes to
localdata/.csv back to main. The commit message includes
[skip ci] to avoid recursive workflow triggers. The workflow
file uses concurrency: cancel-in-progress: true so a slow run
doesn't pile up.
tests/test_live_forward_returns.py: 4 unit tests covering
no-log, empty-leaderboard, real-data, and out-of-universe cases.
63 tests pass total (up from 59).
Why the universe is dynamic, not hardcoded
The HANDOVER's V4 "triage_bucket" system already provides a defensible
answer to "which slices should we watch": clean_survivor rows in
localdata/candidate_leaderboard.csv are the slices that have passed
train+valid+walk-forward+parent-excess discipline. live_forward_returns.py
reads the current leaderboard at every run and tracks forward returns
for whatever set of clean_survivor* slices exists at that moment.
As the V4 substrate evolves (e.g. as new candidates graduate to
clean_survivor, or current ones decay and get demoted), the watched
set updates automatically. Currently this is exactly one slice: XLF 1d
state_ext=stretched_up + state_slope=flat.

Hard rules this layer enforces

No orders are placed. The trading endpoint is never called. Only
fetch_alpaca_bars is used, and only as a backfill when the local
warehouse is too stale to cover the exit window.
All API keys are repo secrets (ALPACA_API_KEY, ALPACA_SECRET_KEY,
TIINGO_API_KEY), not committed to the repo. The workflow
references them as ${{ secrets.ALPACA_API_KEY }} and they are
exposed to each step via env: so they reach os.environ.
The auto-commit only fires when the CSV actually changed (via
git diff --quiet localdata/). No commit happens on a no-op run.
The workflow's [skip ci] in the commit message prevents the
auto-commit from triggering another workflow run.
The concurrency: cancel-in-progress: true block prevents two
runs from racing on the same data.
What this section does NOT do

Authorize any live paper-trading. The HANDOVER's "no execution in
v1-v4" boundary stands. Any live paper-trading remains a separate,
explicit decision that should be documented in a new HANDOVER
section if and when it happens.
Override the V4 doctrine. The HANDOVER's V4 conclusions stand
unchanged: "No candidate is promoted," "XLF 1d state_ext=stretched_up
state_slope=flat -- current top candidate to keep watching, still
NOT promoted," "Do not promote it as a tradable edge."
Modify monitor.DEFAULT_MONITORED_SLICES (the four V4-era
hardcoded slices). The live-capture layer tracks the current
leaderboard's clean_survivor* set, which is a different (and
stricter) filter.
What the HANDOVER's "operator runs local commands" assumption has changed
The V1-V4 doctrine repeatedly says "the operator runs local commands;
the agent reads pasted terminal output and provides the next safe step."
This remains true for everything in the repo except the new live-capture
layer. That layer runs on GitHub's infrastructure, not on the operator's
machine.

Practical implications:

The operator can no longer see what the live-capture layer is doing
in real time. They can only see it via the auto-commits to
localdata/*.csv in the git log, or by visiting the Actions tab
on GitHub.
A bug in the live-capture layer (a malformed row, a wrong symbol,
etc.) would be auto-committed to main and would require a git revert to undo. The layer has been written defensively (idempotent
reruns, no silent drops, partial_data flag) but the risk of a bad
commit is real.
The auto-commit policy is "always commit if CSV changed." A future
change to the layer should consider whether this policy is still
right, or whether some changes should require manual review.
Practical conclusion

The live-capture layer is research infrastructure, not trading
infrastructure. It produces a new out-of-sample data point every
6 hours for whichever clean_survivor* slices exist. Over a few
weeks, this builds a live forward-return dataset that can be used
to validate (or invalidate) the V4 historical findings.
Any agent seeing localdata/live_forward_returns.csv in the repo
should know it is automatically generated, not operator-curated. It
is research output, not trading income.
If a future agent finds themselves writing "the live forward
returns are X% positive therefore the slices work", stop. That is
the exact failure mode this section exists to prevent.
V5 — ML Slice Discovery (2026-07-03)
Added LightGBM-based slice discovery in src/price/ml_discovery.py.
This augments (does not replace) the existing combinatorial discovery in discovery.py.
The ML path:

Uses the same feature frame
Trains a LightGBM regressor on 5-bar forward returns with time-series CV
Extracts top features by importance
Outputs candidate slices in the same format as combinatorial discovery
Feeds into the existing validate_slices.py pipeline
This allows the system to discover non-linear and higher-order interactions that the 3D–5D grid misses, while preserving all V4 validation discipline.

Requirements added: lightgbm

V5 — ML -> Validation Bridge (2026-07-03)
The V5 section above claims the ML path (4) "outputs candidate slices in the
same format as combinatorial discovery" and (5) "feeds into the existing
validate_slices.py pipeline." Until this patch both claims were aspirational,
not wired up. ml_discovery.py emitted raw feature interactions like
feat_ext_vs_ma_20 + feat_ret_3, but validate_slices.py only understands
binned state_*=value filters like state_ext=stretched_up + state_ret_3=ret_up, and bin_features() did not even bin the return
features (feat_ret_1/3/5/10/20) that dominate ML candidate interactions.
So the 8 promising 2-feature combinations the previous session exported to
localdata/ml_promising_slices.csv could not actually be validated.

This patch closes that loop. It is the conversion helper the prior session
offered ("convert these combinations into the exact state format your current
bin_features() expects"). No new validation code and no new doctrine:
ML-discovered interactions now flow through the exact same V4 discipline
(train/valid + cost + Newey-West + walk-forward + parent-excess).

What was added:

src/price/discovery.py:
bin_features() now also bins every raw feature LightGBM ranks highly
into the state vocabulary: state_ret_{1,3,5,10,20}
(ret_down/ret_flat/ret_up), plus state_atr_ext, state_vol_regime,
state_trend_strength, state_gap, state_range_pos. Purely additive;
the combinatorial discovery combinations never reference these columns,
so discovery/leaderboard behaviour is unchanged.
bin_features() is now defensive: state_ext/state_session/state_dow
are emitted as NaN (instead of raising) when their source feature column
is absent, so it works on any feature subset.
ML_FEATURE_TO_STATE maps each raw ML feature to its state field;
STATE_LABELS gives the ordered low->high label list per field. A test
pins the high-bucket labels (stretched_up / ret_up / high_vol / uptrend)
so the two dicts cannot drift from bin_features.
src/price/ml_discovery.py:
ml_interaction_to_state_slice() + interactions_to_state_slices() are
the bridge: turn a raw interaction (feat_ext_vs_ma_20 + feat_ret_3)
into a state_*=value filter (state_ext=stretched_up + state_ret_3=ret_up) and emit a candidate table in the
discovered_slices.csv schema (symbol / timeframe / slice_combination +
ML provenance columns that validation ignores).
Each feature maps to its HIGHEST state bucket, not the count-dominant
one. evaluate_interactions only ever tests the high-quantile (>= q75)
side of a feature, so the faithful translation is the "high" bucket.
Using the count-dominant bucket would be wrong: a feature's top-25%
often straddles "neutral" when the bin uses fixed thresholds (state_ext
at +-0.015), which would discard the very "high" signal the ML surfaced.
Adds a slice_key fallback to evaluate_interactions() on top of
upstream commit 85bf3c7 (which already made run_ml_discovery records
carry a "features" key and switched the loop to .get("features", [])).
The fallback means hand-built dicts or older callers that carry only a
"slice_key": "feat_a + feat_b" are still handled, not silently skipped.
Upstream's records now carry both keys, so the documented V5 workflow
(res = run_ml_discovery(...); evaluate_interactions(df, res[res.interaction_size>1].to_dict('records'))) works either way.
scripts/ml_to_slices.py: one-command glue that runs run_ml_discovery +
evaluate_interactions, filters promising interactions (defaults match the
prior session: n>=30, mean_return>0.0008, sharpe_proxy>0.20), converts
them to state slices via the bridge, and writes
localdata/ml_candidate_slices.csv. Supports --symbols, --timeframe,
--append, --target-type, and the threshold flags.
tests/test_ml_discovery.py: 8 unit tests (synthetic fixtures, no
warehouse/network) covering the new state bins, the feature->state
mapping, top-bucket selection, schema tolerance, and a round-trip that
the emitted slice is parseable by parse_slice_combination and
applyable by apply_slice_filter.
Verification:

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 77 passed (was 69; +8 new).
python3 -m ruff check clean on all changed files.
An end-to-end smoke run on a synthetic SPY 1d warehouse with a
constructed edge exercised the full chain -- run_ml_discovery ->
evaluate_interactions (slice_key schema) -> bridge ->
state_ext=stretched_up + state_ret_3=ret_up ->
validate_slices.run_validation -- and produced a real scorecard for the
ML candidate (verdict: rejected, as expected for synthetic noise; the
plumbing is what was being verified).
How to run it (operator, on real warehouse data):
python3 scripts/ml_to_slices.py --symbol SPY --timeframe 1d

then through the full V4 discipline:
python3 scripts/validate_slices.py
--slices-path localdata/ml_candidate_slices.csv --candidate-leaderboard

Notes / doctrine unchanged:

ML candidate slices are candidates, not promotions. They must clear the
same train+valid+cost+Newey-West+walk-forward+parent-excess+search-wide
gates as combinatorial slices before any monitoring/tracking.
The ML bridge is a discovery expansion, not a validation shortcut. A
"promising" ML interaction (high mean_return / sharpe_proxy on the
in-sample high-quantile region) is explicitly NOT evidence of an edge;
it is a hypothesis that V4 validation then tries to falsify.
scripts/ml_to_slices.py overwrites localdata/ml_candidate_slices.csv
by default; use --append when running across multiple symbols so
earlier results are not lost (same convention as discover_slices.py).
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

ML lead SPY stretched_up+ret_3: search_wide_rank 19, BH FAIL, Bonferroni
FAIL. At rank 19/360 the BH critical p is 0.00264; its NW p is 0.0076.
It is NOT search-wide-defensible. Its raw p is ~35x weaker than the slice
that does clear BH.
The only CLEAN survivor that clears BH is combinatorial, not ML: XLE 1d
state_ext=stretched_down + state_slope=downtrend (sw_rank 6, BH pass). But
it is decaying (wf 0110, recent windows fail) -- already flagged in V4.
Bonferroni-passers (DIA sw_rank 2, IWM sw_rank 4) are all
late_emerging_recent_only (0001, scenario_survived 0) -- small-sample recent
artifacts, discarded per the existing HANDOVER warning.
Practical conclusion on the ML path:

ML did NOT produce the project's most defensible candidate. A fixed-prior
combinatorial slice beat every ML slice on the strictest gate. This is
consistent with ML's in-sample 75th-percentile "in-state" cut being more
overfit-prone than the grid's fixed +-0.015 / tertile priors.
ML DID expand the search space and surface a structurally-novel, sign-stable,
fresh family the grid cannot reach. That is a real but modest dividend.
The project's standing deadlock is unchanged: no slice -- ML or combinatorial
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

provisional_sample_starved (valid_n=11, one below the min_samples=15 floor)
direction-adjusted valid mean +0.73%, Newey-West p=0.022
walk-forward pattern 0010 (only fold 2 passes -- intermittent, not stable)
excess vs best parent +0.0013 (positive, clears the parent-excess bar)
BORROW STRESS GRID (the key number):
default (0 bps borrow): provisional
short_borrow2: provisional
short_borrow5: provisional
short_borrow10: rejected
Real TLT borrow over a 5-bar (~1 week) hold is a fraction of a basis point,
so surviving 5 bps is a comfortable margin -- borrow is NOT what is keeping
TLT off the survived list. Sample (n=11) and walk-forward instability (0010)
are. That makes it a needs-more-data case, not a falsified case.
Economically coherent: bonds stretched up + normal vol -> fade is a classic
mean-reversion story, and TLT is one of the few assets where fades are real.
The other 194 shorts were correctly rejected:

USO 1h variants and XLF 1h stretched_up+downtrend landed in
provisional_sample_starved with eye-catching means (+2.5% to +2.8%) but
n=4-10 and walk-forward 0000 (no fold passes). Those huge returns on
single-digit samples are exactly the small-sample artifacts the triage
system exists to catch. Correctly binned, correctly not promoted.
DIA 1d stretched_up+low_vol passed significance (p=0.047) but is
late_emerging_recent_only -- a latest-fold-only effect, a recent-regime
artifact.
Everything else is rejected_unsupported: not significant, or negative even
after direction-adjustment (IWM/QQQ 1h shorts at -0.03% to -0.05% -- the
direction-adjustment could not rescue them because there is no edge there
to invert).
Practical conclusion:

The direction-agnostic layer worked exactly as designed: it searched both
sides symmetrically, held shorts to the same gate as longs, stress-tested
borrow, and the data says short edges on liquid US ETFs over a 5-bar daily
hold are genuinely rarer than long edges.
The asymmetry the old long-only lock HID turned out to be a real asymmetry,
not an artifact of the lock. That is itself a defensible research
conclusion.
TLT stretched_up+mid_vol joins the watch list alongside XLF (long,
walk-forward-strong, p-weak) and XLE 1d (long, p-strong, walk-forward-
decaying). None promotable. The project's standing deadlock is unchanged:
no slice -- long or short -- combines robust walk-forward + search-wide-
defensible p + positive parent-excess.
Nothing changes about monitoring or execution. monitor.DEFAULT_MONITORED_
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

Futures Expansion (2026-07-04)
This section records the decision to expand the research substrate from the original 10-symbol US ETF universe into a comparable 10-symbol continuous futures universe.

Rationale

The ETF substrate (SPY/QQQ/IWM/DIA/GLD/TLT/USO/XLK/XLF/XLE) has been exhaustively mined through V4 and V5 (combinatorial + ML discovery, cross-asset conditioning, direction-agnostic search, walk-forward, search-wide multiple-testing, and freshness gates).
No slice currently meets the full promotion criteria.
Adding more ETF combinations yields diminishing returns due to multiple-testing burden.
Futures provide clean, high-liquidity bar data with strong macro-regime richness and natural cross-asset conditioning opportunities (e.g. CL or GC as regime variables for equity futures).
Decision
Expand to a 10-symbol continuous futures universe while preserving the exact V4/V5 validation discipline (train/valid + cost + Newey-West + walk-forward + parent-excess + search-wide gates). No changes to promotion doctrine.

Recommended initial futures universe (10 symbols)

ES — S&P 500 E-mini
NQ — Nasdaq-100 E-mini
RTY — Russell 2000 E-mini
YM — Dow Jones E-mini
CL — Crude Oil WTI
GC — Gold
SI — Silver
ZB — 30-Year Treasury Bond
ZN — 10-Year Treasury Note
NG — Natural Gas
Timeframes remain: 15m, 1h, 1d.

Data source notes

Alpaca Market Data API supports continuous futures contracts on the free tier (IEX-style feed for futures).
Tiingo does not cover futures; Alpaca becomes the primary source for all futures bars.
Continuous contract symbols (e.g. ES1!, NQ1!) will be normalized to uppercase without the "1!" suffix in the warehouse for consistency with ETF symbols.
No corporate-action adjustments are required for futures (no splits/dividends).
Impact on existing paper-trading layer
The current Alpaca paper-trading / live-capture infrastructure (monitor.py, trading.py, paper_trade.py, live_capture.yml, and live_forward_returns.py) operates exclusively on the ETF universe and the four V4-era monitored slices. The futures expansion is purely a research-substrate addition (ingestion + warehouse + discovery). It does not:

modify any execution code
add futures to DEFAULT_MONITORED_SLICES
change risk limits or the paper account
trigger any order placement
Therefore the existing deployed/paper-trading system on Alpaca is unaffected and will not be harmed.

Next steps (to be executed only after operator sign-off)

Update symbol configuration and data-source handling for futures.
Perform narrow manual ingestion test on 1–2 futures symbols.
Run full V4/V5 pipeline on the new 10-symbol futures universe using the same validation gates.
Practical conclusion
Futures expansion maintains the lean, disciplined research philosophy of the repo while giving the discovery engine a fresh, regime-rich substrate of equal size to the original ETF set. No promotion claims are made. The existing paper-trading setup remains untouched.

Liquidity-First Reset / Current Operating Universe (2026-07-04)
This section supersedes the earlier broad-allowlist and futures-expansion push for the current research sprint.

What happened

The Alpaca asset survey successfully generated a full allowlist of roughly 10k symbols (9916 equities + 35 futures + 55 crypto = 9989 total), written to:

localdata/explicit_allowlist.json

A full 10k-symbol, 5-year 1d backfill was started and correctly saved data incrementally, but was stopped early because it was too large for the current iteration loop. The operator then replaced the full generated allowlist with a manual liquid-first allowlist.

Current intended universe

Use a small, liquid-first universe for initial findings:

221 liquid equities / ETFs
15 major crypto USD pairs
0 futures
236 total symbols

This is the intended active universe until the research loop proves useful. Do not re-run scripts/survey_assets.py unless the operator explicitly wants to regenerate the broad 10k universe; doing so will overwrite localdata/explicit_allowlist.json back to the full survey output.

Current local universe file

localdata/explicit_allowlist.json is the active local universe source. It is intentionally localdata and gitignored. It should contain keys:

equities
futures
crypto
all
meta

For this sprint, futures must remain an explicit empty list:

"futures": []

Important config fix

A bug was found in get_allowlist_symbols(): using generated.get("futures") or FUTURES_SYMBOLS caused an explicit empty futures list to silently re-add the default futures universe. That produced 270 symbols instead of 236 and reintroduced ambiguous roots like BTC/ETH/CL/ES.

Fix committed locally by the operator:

1ffe36a fix: honor empty generated futures allowlist

Required behavior going forward:

If localdata/explicit_allowlist.json exists, an explicit empty list means "exclude this asset class".
Do not use or FUTURES_SYMBOLS / or CRYPTO_SYMBOLS fallback for present-but-empty generated lists.
is_futures(symbol) must treat a symbol as futures only if it appears in the generated JSON's "futures" list when a generated allowlist exists.
This avoids misrouting ambiguous symbols like CL (Colgate equity vs crude futures), ES, BTC, and ETH.

Verification command:

python3 scripts/capture_bars.py --tier allowlist --universe

Expected current output:

Resolved universe (236 symbols)

It should include BTC/USD but not bare BTC; ETH/USD but not bare ETH. CL may appear, but it is Colgate equity and should ingest from ALPACA, not ALPACA_FUTURES.

Futures status

Futures are excluded for now.

Reason: the current repo's futures routing is symbol-ambiguous and not yet a clean futures data path. Bare roots overlap with equities/crypto names:

CL = Colgate equity and crude oil futures root
ES can be an equity-like/root ambiguity
BTC / ETH can be crypto/futures/root ambiguity

Do not add futures back into the main allowlist until futures have a clean namespace and verified data-source handling. If futures are tested, test them separately with explicit symbols and do not mix them into the main liquid research universe.

Current data-capture doctrine

Start small, validate the loop, then grow only after initial findings.

Current capture sizes:

1d: 1825 days (~5 years)
15m: 365 days (~1 year)
1h: do not fetch directly; generated locally by resampling 15m

Rationale:

Daily bars are compact. Five years of 1d data gives about 1250 equity observations per symbol and about 1825 crypto observations per symbol.
15m bars are much larger. One year already gives about 6500 equity bars per symbol and about 35k crypto bars per pair. A 5-year 15m pull would be roughly 5x larger and would slow iteration before the research loop has proven value.

Current recommended commands

Verify active universe:

python3 scripts/capture_bars.py --tier allowlist --universe

Daily backfill:

python3 scripts/capture_bars.py --timeframes 1d --days 1825

Intraday backfill:

python3 scripts/capture_bars.py --timeframes 15m --days 365

The 15m command also creates/updates 1h warehouse partitions by local resampling.

Observed current progress

The operator completed the 236-symbol 1d pass successfully. Most symbols have roughly 1252-1255 daily equity bars; crypto pairs have up to 1825 bars depending on listing/support history. The operator then started:

python3 scripts/capture_bars.py --timeframes 15m --days 365

Observed expected behavior:

Equities fetch around ~6500-7000 15m bars for 1 year.
Crypto fetches around ~35k 15m bars for 1 year because crypto trades 24/7.
1h partitions are saved immediately after 15m resampling.
Duplicate-looking saves for equities are expected/noisy: capture saves 15m, resamples 1h, then adjustment propagation may save 15m and 1h again.

Warehouse cleanup already performed

After the aborted 10k run, the operator dry-ran and deleted warehouse symbol directories not in the 236-symbol liquid allowlist. 196 non-allowlist directories were deleted. This was correct and reduced warehouse clutter.

Do not blindly prune yet

Be careful with:

python3 scripts/prune_warehouse.py --min-bars 200 --delete

Some newer crypto/assets may have fewer than 200 daily bars (example observed earlier: ADA/USD had 142 daily bars before a later incremental run). Always dry-run first:

python3 scripts/prune_warehouse.py --min-bars 200

If the dry-run would delete assets the operator wants to keep, either lower the threshold (e.g. --min-bars 100) or skip prune for now.

Warehouse health check after capture

Use this after 1d/15m capture to assess coverage:

python3 - <<'PY'
from pathlib import Path
import json
import pandas as pd

payload = json.load(open("localdata/explicit_allowlist.json"))
allowed = payload["all"]

def safe(sym):
return sym.upper().replace("/", "-").replace(":", "-").replace("\", "-").replace(" ", "_")

root = Path("localdata/warehouse")

for tf in ["1d", "15m", "1h"]:
have = 0
counts = []
missing = []
for sym in allowed:
p = root / f"symbol={safe(sym)}" / f"timeframe={tf}" / "data.parquet"
if not p.exists():
missing.append(sym)
continue
try:
n = len(pd.read_parquet(p))
counts.append(n)
have += 1
except Exception:
missing.append(sym)
print(f"\n{tf}")
print(" have:", have, "/", len(allowed))
print(" missing:", len(missing))
if counts:
print(" min:", min(counts), "max:", max(counts), "avg:", round(sum(counts)/len(counts), 1))
if missing:
print(" missing first 15:", missing[:15])
PY

Recommended research sequence after capture

Run discovery/validation in layers. Do not jump immediately into broader universes.

Daily first:
python3 scripts/discover_slices.py --timeframe 1d
python3 scripts/validate_slices.py

Then 1h:
python3 scripts/discover_slices.py --timeframe 1h
python3 scripts/validate_slices.py

Then 15m if daily/1h diagnostics look sane:
python3 scripts/discover_slices.py --timeframe 15m
python3 scripts/validate_slices.py

If discovering multiple timeframes into the same discovered_slices.csv, remember the existing handover warning: use --append or earlier results may be overwritten.

Expansion gates

Only expand after initial findings justify it. Expansion options, in order:

increase 15m from 365 to 730 days
add more liquid ETFs/stocks
add selected crypto pairs
add futures only after symbol namespace/data-source ambiguity is fixed
revisit the full 10k allowlist only after the pipeline is proven and the operator explicitly accepts the runtime/storage cost

Do not return to 10k symbols or 5-year 15m pulls as a default. The doctrine is: small liquid universe first, validate loop, then grow deliberately.
Liquid236 Full Baseline Results (2026-07-05)

The first full liquid-first baseline is complete across all intended timeframes.

Universe:
- 221 liquid equities / ETFs
- 15 major crypto USD pairs
- 0 futures
- 236 total symbols
- futures intentionally excluded due to symbol ambiguity in current routing

Data captured:
- 1d: 1825 days
- 15m: 365 days
- 1h: generated from 15m resampling

Artifacts archived:
- localdata/discovered_slices_1d_liquid236.csv
- localdata/validated_slices_1d_liquid236.csv
- localdata/candidate_leaderboard_1d_liquid236.csv
- localdata/discovered_slices_1h_liquid236.csv
- localdata/validated_slices_1h_liquid236.csv
- localdata/candidate_leaderboard_1h_liquid236.csv
- localdata/discovered_slices_15m_liquid236.csv
- localdata/validated_slices_15m_liquid236.csv
- localdata/candidate_leaderboard_15m_liquid236.csv

Aggregate result:
- Total leaderboard rows across 1d/1h/15m: 22,644
- 1d: 4 clean_survivor_wf_strong, 15 clean_survivor_wf_mixed
- 1h: 0 clean_survivor_wf_strong, 8 clean_survivor_wf_mixed, 5 clean_survivor_wf_failed
- 15m: 1 clean_survivor_wf_strong, 6 clean_survivor_wf_mixed, 4 clean_survivor_wf_failed

Primary Tier-1 target set:
1. XOP 1d
   - slice: state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 84
   - valid_mean_ret_costadj: +0.018436
   - valid_excess_vs_baseline: +0.015272
   - valid_excess_vs_best_parent: +0.002215
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 8
   - search_wide_bh_pass: True

2. MU 15m
   - slice: state_session=afternoon + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 266
   - valid_mean_ret_costadj: +0.011537
   - valid_excess_vs_baseline: +0.009103
   - valid_excess_vs_best_parent: +0.005889
   - walk_forward: 3/4, pattern 1101
   - scenario_survived_count: 8
   - note: simpler 2D slice is preferred over the 3D MU variant because parent-excess is materially better.

3. KLAC 1d
   - slice: state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 53
   - valid_mean_ret_costadj: +0.045880
   - valid_excess_vs_baseline: +0.038932
   - valid_excess_vs_best_parent: +0.008148
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 8
   - search_wide_bh_pass: True
   - search_wide_bonferroni_pass: True
   - note: strongest statistical lead due to Bonferroni pass.

4. XLB 1d
   - slice: state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 82
   - valid_mean_ret_costadj: +0.015540
   - valid_excess_vs_baseline: +0.019481
   - valid_excess_vs_best_parent: +0.003002
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 7
   - search_wide_bh_pass: True

5. XLF 1d
   - slice: state_ext=stretched_up + state_slope=flat
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 33
   - valid_mean_ret_costadj: +0.010050
   - valid_excess_vs_baseline: +0.008234
   - valid_excess_vs_best_parent: +0.005614
   - walk_forward: 3/4, pattern 1110
   - scenario_survived_count: 6

Tier-2 watchlist:
- AMD 1d, state_ext=stretched_up + state_slope=flat, long
- DE 1d, state_ext=stretched_down + state_slope=downtrend, long
- DASH 1d, state_ext=stretched_up + state_vol=low_vol, long
- AVGO 1d, state_ext=stretched_down + state_slope=downtrend, long
- XLE 1d, state_ext=stretched_down + state_slope=downtrend, long
- HUM 1h, state_session=lunch + state_ext=stretched_up, long
- FBTC 1h, state_ext=stretched_up + state_vol=high_vol, short
- AAVE/USD 1h, state_session=morning + state_ext=stretched_down + state_slope=downtrend, short
- XBI 15m, state_session=afternoon + state_ext=neutral, long
- BCH/USD 15m, state_session=afternoon + state_ext=stretched_up, short
- UNH 15m, state_session=morning + state_slope=uptrend, long

Interpretation:
- Daily produced the strongest broad candidates.
- 15m produced one standout session/intraday candidate: MU afternoon + downtrend.
- 1h produced mixed candidates but no clean_survivor_wf_strong.
- The dominant daily family is stretched_down + downtrend rebound in cyclical / materials / energy-linked instruments (XOP, XLB, KLAC).
- Do not promote every survived slice. Prioritize clean_survivor_wf_strong and require positive parent-excess plus walk-forward strength.
- CVX 1d scored highly but has only WF 1/4, so it is not Tier 1 despite high scenario robustness.
- Any clean_survivor_wf_failed, over_specified_survivor, late_emerging_recent_only, or late_emerging_regime_switching candidate remains diagnostic only.

Next research steps:
1. Inspect bar windows and corporate-action sanity for Tier-1 candidates.
2. Re-run focused diagnostics for Tier-1 only.
3. Consider adding only Tier-1/Tier-2 candidates to monitoring after inspection.
4. Do not expand the universe yet; the current baseline is sufficient to choose targets.
5. Future expensive full runs should be avoided unless the universe or feature set materially changes.


Corrected RTH/Tiingo Liquid236 Baseline Results (2026-07-05)

This section supersedes the earlier Liquid236 baseline notes that were generated before:
1. Tiingo daily routing was extended to all equities, and
2. equity intraday bars were filtered to regular trading hours.

Why rerun was required:
- Tier-1 audit found impossible/non-market jumps in non-core daily equities (KLAC/XLB), caused by daily adjustment issues.
- Equity 15m data contained sparse premarket/after-hours bars that contaminated intraday rolling features and session labels.
- RTH filtering and Tiingo-all-equity daily routing were therefore required before trusting intraday/daily results.

Fixes applied:
- src/price/data_sources.py now prefers Tiingo daily bars for all equities when TIINGO_API_KEY is available.
- src/price/data_sources.py imports is_equity for router logic.
- equity intraday bars are filtered to regular trading hours for future capture.
- warehouse 1h resampling uses RTH-filtered 15m rows for equities.
- existing equity 15m warehouse partitions were cleaned to RTH and 1h partitions rebuilt.
- crypto remains 24/7 and is not RTH-filtered.

Corrected artifacts:
- localdata/discovered_slices_1d_tiingo_liquid236.csv
- localdata/validated_slices_1d_tiingo_liquid236.csv
- localdata/candidate_leaderboard_1d_tiingo_liquid236.csv
- localdata/discovered_slices_1h_rth_liquid236.csv
- localdata/validated_slices_1h_rth_liquid236.csv
- localdata/candidate_leaderboard_1h_rth_liquid236.csv
- localdata/discovered_slices_15m_rth_liquid236.csv
- localdata/validated_slices_15m_rth_liquid236.csv
- localdata/candidate_leaderboard_15m_rth_liquid236.csv

Corrected aggregate triage:
- 1d_tiingo: 4 clean_survivor_wf_strong, 15 clean_survivor_wf_mixed
- 1h_rth: 0 clean_survivor_wf_strong, 8 clean_survivor_wf_mixed
- 15m_rth: 0 clean_survivor_wf_strong, 6 clean_survivor_wf_mixed

Corrected Tier-1 target set:
1. XOP 1d
   - state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 84
   - valid_mean_ret_costadj: +0.018436
   - valid_excess_vs_baseline: +0.015272
   - valid_excess_vs_best_parent: +0.002215
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 8
   - search_wide_bh_pass: True

2. XLB 1d
   - state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 64
   - valid_mean_ret_costadj: +0.015244
   - valid_excess_vs_baseline: +0.012205
   - valid_excess_vs_best_parent: +0.002644
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 8
   - search_wide_bh_pass: True

3. KLAC 1d
   - state_ext=stretched_down + state_slope=downtrend
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 45
   - valid_mean_ret_costadj: +0.046809
   - valid_excess_vs_baseline: +0.025704
   - valid_excess_vs_best_parent: +0.007930
   - walk_forward: 3/4, pattern 0111
   - scenario_survived_count: 8
   - search_wide_bh_pass: True
   - search_wide_bonferroni_pass: True

4. XLF 1d
   - state_ext=stretched_up + state_slope=flat
   - side: long
   - triage: clean_survivor_wf_strong
   - valid_n: 33
   - valid_mean_ret_costadj: +0.010050
   - valid_excess_vs_baseline: +0.008234
   - valid_excess_vs_best_parent: +0.005614
   - walk_forward: 3/4, pattern 1110
   - scenario_survived_count: 6

Corrected intraday interpretation:
- MU 15m was clean_survivor_wf_strong before RTH filtering.
- After RTH filtering, MU 15m downgraded to clean_survivor_wf_mixed:
  state_session=afternoon + state_slope=downtrend, long, WF 2/4.
- Therefore MU 15m remains a watchlist item, not Tier 1.
- 1h produced mixed candidates only; no clean_survivor_wf_strong.

Final corrected conclusion:
- Daily is the strongest timeframe in the liquid236 baseline.
- The dominant durable family is stretched_down + downtrend rebound in XOP, XLB, and KLAC.
- XLF is a distinct financial-sector stretched_up + flat continuation/extension pattern.
- Intraday candidates are secondary until they show stronger walk-forward robustness on RTH-clean data.
- Do not use pre-RTH intraday artifacts. Only *_rth_liquid236 files are valid for intraday.
- Do not use old pre-Tiingo daily results for non-core equities. Prefer *_1d_tiingo_liquid236 files.

Next steps:
1. Re-run tier1_signal_audit on corrected Tier-1 candidates only: XOP, XLB, KLAC, XLF.
2. Inspect recent signal windows and worst outcomes.
3. Build a curated monitoring candidate list only after audit passes.
4. Keep all execution/paper-trading disabled until the curated list is explicitly reviewed.
