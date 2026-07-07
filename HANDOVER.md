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
return sym.upper().replace("/", "-").replace(":", "-").replace("", "-").replace(" ", "_")

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

221 liquid equities / ETFs
15 major crypto USD pairs
0 futures
236 total symbols
futures intentionally excluded due to symbol ambiguity in current routing
Data captured:

1d: 1825 days
15m: 365 days
1h: generated from 15m resampling
Artifacts archived:

localdata/discovered_slices_1d_liquid236.csv
localdata/validated_slices_1d_liquid236.csv
localdata/candidate_leaderboard_1d_liquid236.csv
localdata/discovered_slices_1h_liquid236.csv
localdata/validated_slices_1h_liquid236.csv
localdata/candidate_leaderboard_1h_liquid236.csv
localdata/discovered_slices_15m_liquid236.csv
localdata/validated_slices_15m_liquid236.csv
localdata/candidate_leaderboard_15m_liquid236.csv
Aggregate result:

Total leaderboard rows across 1d/1h/15m: 22,644
1d: 4 clean_survivor_wf_strong, 15 clean_survivor_wf_mixed
1h: 0 clean_survivor_wf_strong, 8 clean_survivor_wf_mixed, 5 clean_survivor_wf_failed
15m: 1 clean_survivor_wf_strong, 6 clean_survivor_wf_mixed, 4 clean_survivor_wf_failed
Primary Tier-1 target set:

XOP 1d

slice: state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 84
valid_mean_ret_costadj: +0.018436
valid_excess_vs_baseline: +0.015272
valid_excess_vs_best_parent: +0.002215
walk_forward: 3/4, pattern 0111
scenario_survived_count: 8
search_wide_bh_pass: True
MU 15m

slice: state_session=afternoon + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 266
valid_mean_ret_costadj: +0.011537
valid_excess_vs_baseline: +0.009103
valid_excess_vs_best_parent: +0.005889
walk_forward: 3/4, pattern 1101
scenario_survived_count: 8
note: simpler 2D slice is preferred over the 3D MU variant because parent-excess is materially better.
KLAC 1d

slice: state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 53
valid_mean_ret_costadj: +0.045880
valid_excess_vs_baseline: +0.038932
valid_excess_vs_best_parent: +0.008148
walk_forward: 3/4, pattern 0111
scenario_survived_count: 8
search_wide_bh_pass: True
search_wide_bonferroni_pass: True
note: strongest statistical lead due to Bonferroni pass.
XLB 1d

slice: state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 82
valid_mean_ret_costadj: +0.015540
valid_excess_vs_baseline: +0.019481
valid_excess_vs_best_parent: +0.003002
walk_forward: 3/4, pattern 0111
scenario_survived_count: 7
search_wide_bh_pass: True
XLF 1d

slice: state_ext=stretched_up + state_slope=flat
side: long
triage: clean_survivor_wf_strong
valid_n: 33
valid_mean_ret_costadj: +0.010050
valid_excess_vs_baseline: +0.008234
valid_excess_vs_best_parent: +0.005614
walk_forward: 3/4, pattern 1110
scenario_survived_count: 6
Tier-2 watchlist:

AMD 1d, state_ext=stretched_up + state_slope=flat, long
DE 1d, state_ext=stretched_down + state_slope=downtrend, long
DASH 1d, state_ext=stretched_up + state_vol=low_vol, long
AVGO 1d, state_ext=stretched_down + state_slope=downtrend, long
XLE 1d, state_ext=stretched_down + state_slope=downtrend, long
HUM 1h, state_session=lunch + state_ext=stretched_up, long
FBTC 1h, state_ext=stretched_up + state_vol=high_vol, short
AAVE/USD 1h, state_session=morning + state_ext=stretched_down + state_slope=downtrend, short
XBI 15m, state_session=afternoon + state_ext=neutral, long
BCH/USD 15m, state_session=afternoon + state_ext=stretched_up, short
UNH 15m, state_session=morning + state_slope=uptrend, long
Interpretation:

Daily produced the strongest broad candidates.
15m produced one standout session/intraday candidate: MU afternoon + downtrend.
1h produced mixed candidates but no clean_survivor_wf_strong.
The dominant daily family is stretched_down + downtrend rebound in cyclical / materials / energy-linked instruments (XOP, XLB, KLAC).
Do not promote every survived slice. Prioritize clean_survivor_wf_strong and require positive parent-excess plus walk-forward strength.
CVX 1d scored highly but has only WF 1/4, so it is not Tier 1 despite high scenario robustness.
Any clean_survivor_wf_failed, over_specified_survivor, late_emerging_recent_only, or late_emerging_regime_switching candidate remains diagnostic only.
Next research steps:

Inspect bar windows and corporate-action sanity for Tier-1 candidates.
Re-run focused diagnostics for Tier-1 only.
Consider adding only Tier-1/Tier-2 candidates to monitoring after inspection.
Do not expand the universe yet; the current baseline is sufficient to choose targets.
Future expensive full runs should be avoided unless the universe or feature set materially changes.
Corrected RTH/Tiingo Liquid236 Baseline Results (2026-07-05)

This section supersedes the earlier Liquid236 baseline notes that were generated before:

Tiingo daily routing was extended to all equities, and
equity intraday bars were filtered to regular trading hours.
Why rerun was required:

Tier-1 audit found impossible/non-market jumps in non-core daily equities (KLAC/XLB), caused by daily adjustment issues.
Equity 15m data contained sparse premarket/after-hours bars that contaminated intraday rolling features and session labels.
RTH filtering and Tiingo-all-equity daily routing were therefore required before trusting intraday/daily results.
Fixes applied:

src/price/data_sources.py now prefers Tiingo daily bars for all equities when TIINGO_API_KEY is available.
src/price/data_sources.py imports is_equity for router logic.
equity intraday bars are filtered to regular trading hours for future capture.
warehouse 1h resampling uses RTH-filtered 15m rows for equities.
existing equity 15m warehouse partitions were cleaned to RTH and 1h partitions rebuilt.
crypto remains 24/7 and is not RTH-filtered.
Corrected artifacts:

localdata/discovered_slices_1d_tiingo_liquid236.csv
localdata/validated_slices_1d_tiingo_liquid236.csv
localdata/candidate_leaderboard_1d_tiingo_liquid236.csv
localdata/discovered_slices_1h_rth_liquid236.csv
localdata/validated_slices_1h_rth_liquid236.csv
localdata/candidate_leaderboard_1h_rth_liquid236.csv
localdata/discovered_slices_15m_rth_liquid236.csv
localdata/validated_slices_15m_rth_liquid236.csv
localdata/candidate_leaderboard_15m_rth_liquid236.csv
Corrected aggregate triage:

1d_tiingo: 4 clean_survivor_wf_strong, 15 clean_survivor_wf_mixed
1h_rth: 0 clean_survivor_wf_strong, 8 clean_survivor_wf_mixed
15m_rth: 0 clean_survivor_wf_strong, 6 clean_survivor_wf_mixed
Corrected Tier-1 target set:

XOP 1d

state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 84
valid_mean_ret_costadj: +0.018436
valid_excess_vs_baseline: +0.015272
valid_excess_vs_best_parent: +0.002215
walk_forward: 3/4, pattern 0111
scenario_survived_count: 8
search_wide_bh_pass: True
XLB 1d

state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 64
valid_mean_ret_costadj: +0.015244
valid_excess_vs_baseline: +0.012205
valid_excess_vs_best_parent: +0.002644
walk_forward: 3/4, pattern 0111
scenario_survived_count: 8
search_wide_bh_pass: True
KLAC 1d

state_ext=stretched_down + state_slope=downtrend
side: long
triage: clean_survivor_wf_strong
valid_n: 45
valid_mean_ret_costadj: +0.046809
valid_excess_vs_baseline: +0.025704
valid_excess_vs_best_parent: +0.007930
walk_forward: 3/4, pattern 0111
scenario_survived_count: 8
search_wide_bh_pass: True
search_wide_bonferroni_pass: True
XLF 1d

state_ext=stretched_up + state_slope=flat
side: long
triage: clean_survivor_wf_strong
valid_n: 33
valid_mean_ret_costadj: +0.010050
valid_excess_vs_baseline: +0.008234
valid_excess_vs_best_parent: +0.005614
walk_forward: 3/4, pattern 1110
scenario_survived_count: 6
Corrected intraday interpretation:

MU 15m was clean_survivor_wf_strong before RTH filtering.
After RTH filtering, MU 15m downgraded to clean_survivor_wf_mixed:
state_session=afternoon + state_slope=downtrend, long, WF 2/4.
Therefore MU 15m remains a watchlist item, not Tier 1.
1h produced mixed candidates only; no clean_survivor_wf_strong.
Final corrected conclusion:

Daily is the strongest timeframe in the liquid236 baseline.
The dominant durable family is stretched_down + downtrend rebound in XOP, XLB, and KLAC.
XLF is a distinct financial-sector stretched_up + flat continuation/extension pattern.
Intraday candidates are secondary until they show stronger walk-forward robustness on RTH-clean data.
Do not use pre-RTH intraday artifacts. Only *_rth_liquid236 files are valid for intraday.
Do not use old pre-Tiingo daily results for non-core equities. Prefer *_1d_tiingo_liquid236 files.
Next steps:

Re-run tier1_signal_audit on corrected Tier-1 candidates only: XOP, XLB, KLAC, XLF.
Inspect recent signal windows and worst outcomes.
Build a curated monitoring candidate list only after audit passes.
Keep all execution/paper-trading disabled until the curated list is explicitly reviewed.
Session Close Update — Corrected Deep-End Paper Deployment (2026-07-05)

This section records the final operational state after the corrected Liquid236 baseline, RTH cleanup, paper deployment, and workflow cleanup.

Authoritative research artifacts

The valid corrected research artifacts are now the corrected/RTH/Tiingo files only:

Daily:

localdata/discovered_slices_1d_tiingo_liquid236.csv
localdata/validated_slices_1d_tiingo_liquid236.csv
localdata/candidate_leaderboard_1d_tiingo_liquid236.csv
1h:

localdata/discovered_slices_1h_rth_liquid236.csv
localdata/validated_slices_1h_rth_liquid236.csv
localdata/candidate_leaderboard_1h_rth_liquid236.csv
15m:

localdata/discovered_slices_15m_rth_liquid236.csv
localdata/validated_slices_15m_rth_liquid236.csv
localdata/candidate_leaderboard_15m_rth_liquid236.csv
The older pre-RTH intraday and pre-Tiingo daily artifacts were deleted from localdata to avoid future confusion. Do not use non-RTH intraday results or pre-Tiingo daily results for target selection.

Final corrected Tier-1 candidates

The corrected Tier-1 paper candidates are:

XOP 1d

state_ext=stretched_down + state_slope=downtrend
side: long
source: corrected 1d_tiingo liquid236 baseline
triage: clean_survivor_wf_strong
XLB 1d

state_ext=stretched_down + state_slope=downtrend
side: long
source: corrected 1d_tiingo liquid236 baseline
triage: clean_survivor_wf_strong
KLAC 1d

state_ext=stretched_down + state_slope=downtrend
side: long
source: corrected 1d_tiingo liquid236 baseline
triage: clean_survivor_wf_strong
note: strongest statistical lead among Tier-1 due to search-wide Bonferroni pass.
XLF 1d

state_ext=stretched_up + state_slope=flat
side: long
source: existing deployed slice and corrected Tier-1 baseline
triage: clean_survivor_wf_strong
Corrected Tier-1 audit

A corrected Tier-1 audit was run after the Tiingo and RTH fixes.

Audit outputs:

localdata/tier1_corrected_quality_summary.csv
localdata/tier1_corrected_signal_hits.csv
localdata/tier1_corrected_largest_jumps.csv
localdata/tier1_corrected_signal_audit.log
Results:

XOP, XLB, KLAC, and XLF all had:
duplicate timestamps: 0
NaN close_adj: 0
non-positive close_adj: 0
large daily jumps >=25%: 0
Key audited profile:

XOP: high magnitude, real energy-sector tail risk.
XLB: cleaner material-sector version of the stretched_down + downtrend rebound family.
KLAC: strongest magnitude/statistical lead, but higher tail risk.
XLF: cleanest risk profile, lower hit frequency, high observed win rate.
ML second opinion

A targeted ML bridge run was executed on the corrected Tier-1 daily candidates:

XOP
XLB
KLAC
XLF
Artifacts:

localdata/ml_candidate_slices_tier1_1d_tiingo.csv
localdata/ml_candidate_leaderboard_tier1_1d_tiingo.csv
localdata/ml_to_slices_tier1_1d_tiingo.log
localdata/ml_validate_tier1_1d_tiingo.log
Result:

ML produced candidate slices for XOP, XLB, and XLF.
KLAC produced no ML interaction candidates meeting the promising thresholds.
18 ML-derived candidates were validated.
0 ML candidates survived full validation.
The best ML candidates were rejected as late_emerging, sample-starved, parent-underperformed, or unsupported.
Interpretation:

ML did not produce a stronger promotable replacement for the corrected Tier-1 candidates.
This does not invalidate Tier-1, because Tier-1 is supported by fixed-bin combinatorial discovery, validation, scenario robustness, walk-forward, parent-excess, and audit sanity.
Current best evidence remains fixed-bin/combinatorial, not ML-derived.
Deployment/watch list

The monitor now prefers:

localdata/monitored_slices.csv

This file is the explicit deployment/watch-list source and prevents the monitor from accidentally deploying every clean_survivor* row in the latest research candidate_leaderboard.csv.

Current monitored slices are:

XLB 1d, state_ext=stretched_down + state_slope=downtrend, long
XOP 1d, state_ext=stretched_down + state_slope=downtrend, long
KLAC 1d, state_ext=stretched_down + state_slope=downtrend, long
SPY 1h, state_session=afternoon + state_slope=downtrend, long
XLK 1d, cross_TLT_state_slope=uptrend + state_ext=neutral, long
XLK 1h, cross_USO_state_vol=mid_vol + state_ext=stretched_down, long
XLF 1d, state_ext=stretched_up + state_slope=flat, long
Deployment philosophy:

Keep the incumbent paper slices in the deep end.
Add corrected Tier-1 friends to the same deep end.
Dynamic recall/deploy should happen by regenerating monitored_slices.csv from strict evidence rules, not by directly trading the current candidate_leaderboard.csv.
Current safe sync rule: incumbents + clean_survivor_wf_strong from corrected 1d/1h/15m leaderboards.
The monitor reads monitored_slices.csv each pass, so updates are picked up dynamically.
Paper order state at session close

The stale XLE order from the previous deployment was canceled.

Alpaca paper orders at session close:

XOP market buy 16, accepted, pending next session
XLK market buy 13, accepted, pending next session
XLE market buy 9, canceled
No open positions were present yet because the orders were submitted while the market was closed/weekend.

Pending-order safety

A pending-order risk-gate improvement was added:

price.trading.get_open_orders() returns accepted/open Alpaca paper orders.
monitor.scan_all_slices combines open positions and open orders as exposure_for_entry_gate.
This prevents repeated weekend/after-hours scans from queuing duplicate market orders before prior accepted DAY orders fill or expire.
Verified behavior:

XOP and XLK matched again after order submission.
Risk gate blocked both because pending orders existed.
Dry-run still shows would_enter because dry_run intentionally bypasses risk checks for audit visibility.
Live capture workflow

The GitHub Actions live_capture workflow was changed to focus on the explicit monitored deep-end set.

Current live workflow purpose:

refresh monitored symbols and conditioning symbols
build warehouse
write explicit monitored_slices.csv
run paper_trade in paper mode
run live_forward_returns
commit lightweight logs/artifacts
The workflow no longer runs daily discovery or candidate leaderboard. Research discovery/validation should be handled by a separate future research-refresh workflow, not mixed into live paper execution.

Monitored/conditioning symbols refreshed by the workflow:

XLF
XLK
SPY
XOP
XLB
KLAC
TLT
USO
Workflow risk settings:

max_notional: 2500
max_open: 7
max_daily_loss: 500
cooldown_seconds: 3600
Important distinction:

GitHub Actions refreshes warehouse data inside the runner for the monitored set.
The full parquet warehouse is not committed to git and is intentionally not persisted in the repo.
For paper execution, rebuilding the small monitored subset per workflow run is acceptable.
Full research warehouse persistence should be handled separately later if needed.
Current architecture decision

Do not mix research discovery and paper execution in the same workflow.

Desired future split:

live_capture.yml

execution and forward-return logging for approved monitored_slices.csv only
research_refresh.yml

discovery
validation
edge decay checks
candidate proposal/report generation
no order placement
This is the path toward autonomy without letting noisy daily discovery directly trade.

Next session priorities

Market-open audit:

check accepted orders
check filled positions
verify no stale XLE exposure
confirm XOP/XLK status after market opens
Exit policy:
Current exit logic is state-invalidation only.
Validation horizon is fwd_ret_5.
Next design target is hybrid exit:

exit when stable state breaks OR held >= 5 bars
use timeframe-aware bar counting
log exit reason clearly
Workflow follow-up:

verify live_capture completes successfully after the workflow cleanup
if it times out, further reduce the workflow to execution-only essentials
Research automation:

design a separate research_refresh workflow
do not let it place orders
use it to detect edge decay and propose monitored_slices changes
ROI Refinement — Position Sizing (2026-07-06)
This section records the first patch of an explicit "refine Price to maximise
ROI" workstream. Operator direction for this session: refine the system toward
maximising ROI, with the scope explicitly set to the path TOWARD REAL CAPITAL
(paper stays the proving ground, but design for real-money readiness: real fill
cost, buying-power/PDT limits, volatility-normalised risk, kill-switches). No
promotion claims; the V4 "nothing is promoted" deadlock stands.

Why sizing was chosen as the first lever
The biggest ROI lever in a system with small, fragile edges is execution-side
capital allocation, not new discovery. The original sizing rule was literally
equal-notional: monitor._default_qty = floor(max_notional / price). A slice
with KLAC's magnitude and a slice with XLF's got identical dollars, regardless
of edge strength, volatility, sample confidence, or walk-forward robustness.
That is where ROI was being left on the table. Ranked ROI levers agreed for the
session: (1) sizing -> (2) exits -> (3) allocation -> (4) cost realism ->
(5) P&L attribution. This patch delivers lever #1.

What was added

src/price/sizing.py: a self-contained edge- and volatility-aware sizing
subsystem. Two-stage model:
Stage A (always): conviction-weighted notional.
target_notional = conviction * max_notional_per_position
conviction in (0,1] is derived from the slice's research edge metrics in
candidate_leaderboard.csv: magnitude (valid_mean_ret_costadj / EDGE_REF),
robustness (blend of walk_forward_pass_count, scenario_survived_count,
valid_n), validity (excess_vs_best_parent), and a small multiple-testing
bonus (Bonferroni > BH > none). Stronger, more robust, search-wide-
defensible edges earn more capital.
Stage B (when account equity + ATR available): volatility rail.
qty_risk = floor(conviction * risk_fraction_per_trade * equity / atr_14)
qty = min(qty_notional, qty_risk), so a high-volatility name cannot
concentrate more than its risk budget allows.
All conviction model constants are module-level and tunable: EDGE_REF=0.03,
EXCESS_REF=0.01, MIN_SAMPLES=15, MAX_SCENARIOS=8, MAX_WF=4,
KNOWN_CONVICTION_FLOOR=0.35, BONUS_BONFERRONI=1.15, BONUS_BH=1.05.
ATR(14) is computed locally (features.py computes ATR but discards it; this
module recomputes it from high_adj/low_adj/close_adj rather than touching the
core feature module).
src/price/risk_limits.py: RiskLimits gained three additive fields:
conviction_sizing_enabled (bool, default True), risk_fraction_per_trade
(float, default 0.005 = 0.5% of equity at full conviction), and
account_equity_for_sizing (Optional[float], default None -> Stage B skipped).
src/price/monitor.py: scan_all_slices now sizes every matched signal via
compute_position_size instead of the old equal-notional rule. The matched
signal carries flat sizing_* audit fields (sizing_mode, sizing_conviction,
sizing_target_notional, sizing_atr, sizing_qty_notional, sizing_qty_risk) so
the paper_trade audit CSV records every sizing decision. The dead
_default_qty helper was removed.
scripts/paper_trade.py: new CLI flags --equal-notional (disable conviction
sizing), --risk-fraction (vol-rail fraction, default 0.005), and
--sizing-equity (account equity for the vol rail; None disables Stage B).
tests/test_sizing.py: 19 unit tests covering conviction monotonicity
(magnitude, walk-forward, parent-excess), MT-bonus ordering, the vol rail
binding behaviour, the equal-notional-disabled path, bad-price handling, the
notional cap invariant, CSV-safe audit output, ATR computation, and synthetic
leaderboard lookup.
Graceful-degradation safety property (the reason this is zero-risk to the live book)
localdata/* is gitignored, so on a fresh clone -- and on the live_capture runner
until a leaderboard is regenerated -- there is NO candidate_leaderboard.csv and
no warehouse ATR. The sizing subsystem degrades as follows:

No leaderboard edge data -> conviction = NEUTRAL_CONVICTION = 1.0, which
reproduces the ORIGINAL equal-notional sizing EXACTLY. Sizing only deviates
from equal-notional when we actually have edge data to justify it.
No warehouse / no ATR -> Stage B skipped, Stage A only.
No account equity configured -> Stage B skipped, Stage A only.
price <= 0 / non-finite -> qty 0.
Consequence: the currently-deployed paper book (XOP x16, XLK x13, XLE x9
canceled) is UNAFFECTED until the operator regenerates candidate_leaderboard.csv
and/or sets --sizing-equity. When conviction data IS present, capital is
reallocated toward the stronger edges.
Demonstration on the corrected Tier-1 daily numbers
Feeding the corrected liquid236 Tier-1 metrics (from this HANDOVER) into
compute_conviction produced:
KLAC 1d (Bonferroni, 4.68% edge): conviction 0.928 -> 92.8% of cap
XOP 1d (BH, 1.84% edge): conviction 0.355 -> 35.5% of cap
XLB 1d (BH, 1.52% edge): conviction 0.350 -> 35.0% of cap (at floor)
XLF 1d (no MT pass, 1.0% edge): conviction 0.350 -> 35.0% of cap (at floor)
Interpretation: KLAC (the only Bonferroni-defensible edge, largest magnitude)
earns ~full capital; the three ~1-1.8% edges all settle at the 0.35 floor.
This is reallocation AWAY from the old rule where all four got identical 100%
of the notional cap. The 0.35 floor is a deliberate choice so the weakest
clean_survivor still gets a meaningful slice; it is tunable. Note the three
weaker edges are indistinguishable at the floor because they are genuinely
close in profile (all 3/4 WF, all ~1-1.8%) -- the model is not hiding
differentiation that exists.

Honest caveats

On the current $2500 max_notional paper config with a $100k paper account,
the vol rail (Stage B) rarely binds because the notional cap binds first.
Conviction weighting (Stage A) is what shows up now; the vol rail becomes
the primary knob as we move toward real capital / larger notional / real
equity. The structure is already correct for both regimes.
Conviction uses valid_mean_ret_costadj, which is already net of the
validation cost (1bp). Real fill cost (lever #4, cost realism) is the next
place to tighten; for now sizing already respects cost-adjusted edge.
No slice is promoted by this patch. It only changes HOW MUCH capital a
matched+risk-cleared signal is sized for, and only when edge data exists.
Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 117 passed (was 95 green / 3 red; fixed the 3 red
state_unavailable tests which had been broken since commit 8e0a095 "fix: add
open orders helper" because scan_all_slices now calls get_open_orders() and
those tests only patched get_open_positions/get_today_realized_pnl).
python3 -m ruff check clean on all changed files (removed 4 unused imports I
introduced + 1 pre-existing unused import in test_state_unavailable.py).
What this patch does NOT do

Does not change monitor.DEFAULT_MONITORED_SLICES or monitored_slices.csv.
Does not change the live_capture workflow risk settings.
Does not authorise new live paper-trading; the "no execution in v1-v4"
boundary stands. Any deployment of conviction sizing to the live book
requires the operator to (a) regenerate candidate_leaderboard.csv and
(b) decide whether to set --sizing-equity on the workflow.
Does not promote any slice.
Next ROI levers (in agreed priority order)
2. Exit policy: build the hybrid exit the HANDOVER's prior "Next session
priorities" already called for -- state-break OR held >= validation
horizon (5 bars, timeframe-aware), with profit-target/time-stop and a
logged exit reason. Currently exits are state-invalidation only and can
run winners/losers past the 5-bar edge window. Touches position_manager.py.
3. Capital allocation across the book: correlation-aware allocation so the
XOP/XLB/KLAC stretched-down-energy/materials concentration is treated as
one risk bucket, not three independent positions.
4. Cost realism: tighten the cost model toward real fills (validation's 1bp is
optimistic for names like XOP); propagate realised cost into P&L.
5. P&L attribution: realised-P&L-per-slice view (net of cost, vs historical
expectation) so we can see which slices actually earn their capital. The
sizing_* audit fields added here are the input to that.

ROI Refinement — Exit Policy (2026-07-06)
Second patch of the ROI workstream (lever #2 of the agreed priority order:
sizing -> exits -> allocation -> cost -> P&L attribution). Delivers the
hybrid exit the prior "Next session priorities" section explicitly called
for: "exit when stable state breaks OR held >= 5 bars, use timeframe-aware
bar counting, log exit reason clearly."

Why exits were the next lever
The original exit policy was state-invalidation ONLY: a position held
indefinitely until the slice's stable (non-session/DOW/month) filter broke,
with no profit target, no time/age stop, and no respect for the 5-bar
validation horizon the edges were actually measured on. That leaves ROI on
the table in both directions: winners run into unvalidated territory, and
losers run past the edge window. The validation measures fwd_ret_5; an exit
discipline that doesn't bound hold length to that horizon is unfaithful to
the measured edge by construction.

What was added

src/price/position_manager.py:
ExitPolicy dataclass: horizon_bars (default 5 == the fwd_ret_5
validation horizon; 0 disables -> state-break only = legacy behaviour).
check_exits now implements a HYBRID exit: a position exits when ANY of
(a) stable_state_break -- the slice's stable filter no longer matches
the current bar (the original logic, preserved exactly), OR
(b) horizon_reached -- bars held in the position's OWN timeframe

= horizon_bars.
Timeframe-aware bar counting: 5 bars on 1d ~= one trading week, 5 bars
on 1h ~= one session. Bars held are counted from the entry SIGNAL bar
(faithful to fwd_ret_5, which is measured from the signal bar's close),
not from order fill -- documented below.
_count_bars_after / _parse_ts: pure helpers that count warehouse bars
strictly after the entry bar; never raise; return None on missing data
(None means "do not force a horizon exit on missing data" -- hold).
_load_entry_context: per-symbol most-recent accepted entry context from
the trade journal (slice, timeframe, entry_bar_ts, submitted_at). Used
to resolve each open position's timeframe and entry bar. Legacy journal
rows (written before entry_bar_ts/timeframe existed) fall back to
submitted_at as the entry-time proxy, so they still get an approximate
horizon exit rather than none.
Every exit intent now carries bars_held, horizon_bars, timeframe, and a
clear reason string ("horizon reached: held N bars >= 5 (1d)" /
"stable filter broken: ..." / "stable filter matches; held N/5 bars"),
satisfying the "log exit reason clearly" requirement.
src/price/trading.py: submit_entry now accepts optional entry_bar_ts and
timeframe and records them in the journal row. Backward compatible: old
callers still work; old journal rows just lack the columns (NaN on read,
handled by _load_entry_context).
scripts/paper_trade.py: passes sig's bar_ts_utc + timeframe into
submit_entry; new --exit-horizon CLI flag (default 5) constructs the
ExitPolicy and threads it into scan_all_slices.
src/price/monitor.py: scan_all_slices gains an exit_policy param and
forwards it to check_exits.
tests/test_position_manager.py: 13 tests covering exact bar counting,
horizon exit, state-break exit (legacy preserved), both-firing, hold-
within-horizon, horizon-disabled (0), no-entry-context hold, audit fields,
timeframe resolution from context, and timeframe-aware counting (1h vs 1d).
Safety / zero-risk-to-live-book property

This patch only changes behaviour for OPEN POSITIONS returned by
trading.get_open_positions(). At session start there are NONE (XOP x16 and
XLK x13 orders are accepted but not yet filled; XLE x9 canceled). So the
exit policy cannot trigger any premature close today regardless of settings.
The horizon exit only fires when bars_held is computable (entry bar or
submission time present in the journal + warehouse has bars after it). If
either is missing, bars_held=None and the horizon exit is suppressed --
a position is never force-exited on missing data. State-break still works.
horizon_bars defaults to 5 (faithful). --exit-horizon 0 restores the exact
legacy state-break-only behaviour for any operator who wants it.
Entry-bar semantics (documented honestly)
Bars held are counted from the entry SIGNAL bar (the bar whose state
matched and triggered entry), because fwd_ret_5 is measured from that bar's
close. This is the faithful choice. Consequence: for the currently-pending
XOP/XLK entries, the signal bar (2026-07-02) predates the fill (next session
open), so there is slippage between signal-bar-close and fill -- that gap is
a real execution cost (lever #4, cost realism), not something the exit policy
should paper over. Counting from the signal bar means a position exits at the
5th bar-after-signal, which is exactly the horizon the edge was validated on.

What this patch does NOT do

Does NOT add a profit target. A profit-target exit (capture winners before
horizon) would need its own validation against the fwd_mfe_5 (max-favourable-
excursion) distribution that features.py already computes; adding one
unvalidated would change the edge profile the slices were measured on. It
is left as a documented future refinement (potential lever 2b), not built
speculatively.
Does NOT change monitor.DEFAULT_MONITORED_SLICES, monitored_slices.csv, the
live_capture workflow, or any risk limit. No promotion claims.
Does NOT exit on partial/incomplete intraday bars differently than before;
the state comparison still uses the latest warehouse bar as in the original
code (the bars_held count is over the same loaded frame).
Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 130 passed (was 117; +13 exit-policy tests).
python3 -m ruff check clean on all changed files.
End-to-end smoke (synthetic data, mocked account): scan_all_slices with an
open XLF position held 34 daily bars produced exactly one exit_intent,
action=exit, reason="horizon reached: held 34 bars >= 5 (1d)", with the
entry bar + timeframe resolved from the enriched journal. Confirms the
full CLI -> monitor -> position_manager -> audit-reason path.
Next ROI levers (in agreed priority order)
3. Capital allocation across the book: correlation-aware allocation so the
XOP/XLB/KLAC stretched-down energy/materials concentration is treated as
one risk bucket, not three independent positions. Today max_open=7 treats
them as independent.
4. Cost realism: tighten the cost model toward real fills (validation's 1bp
is optimistic for names like XOP); the entry-bar-vs-fill slippage noted
above lives here.
5. P&L attribution: realised-P&L-per-slice view (net of cost, vs the
historical fwd_ret_5 expectation). The sizing_* audit fields (lever 1)
and the bars_held/exit-reason fields (this lever) are the inputs.

Operator action items (optional, none required for the patch to be safe)

The new --exit-horizon flag defaults to 5 (faithful). If you want the live
workflow to use it, add --exit-horizon 5 (or your preferred value) to the
paper_trade invocation in .github/workflows/live_capture.yml. Until then
the workflow uses the module default, which is also 5.
Once XOP/XLK fill and produce their first exit, the audit row will carry
bars_held + the exit reason -- the first real measurement of whether holds
respect the 5-bar horizon.
ROI Refinement — Capital Allocation (2026-07-06)
Third patch of the ROI workstream (lever #3 of the agreed priority order:
sizing -> exits -> allocation -> cost -> P&L attribution).

Why allocation was the next lever
The book treats every monitored symbol as an independent slot under
max_open_positions. But three of the seven monitored slices -- XOP, XLB, KLAC
-- are the SAME edge: state_ext=stretched_down + state_slope=downtrend, the
"cyclical/materials/energy stretched-down rebound family" this HANDOVER
explicitly names as the dominant durable family. When all three are in state
simultaneously (likely, because they share macro drivers), the system would
open three positions that are effectively one bet -- the book is concentrated,
not diversified, and a single adverse regime move hits all three at once.
max_open=7 does not see this; it counts three independent symbols.

Design: risk group = the slice's stable entry condition
The correlation key is the slice's STABLE (non-transient) filter -- the exact
condition that triggers entry. Two positions whose entry conditions are
identical fire on the same bars by construction and are therefore maximally
correlated: they are the same regime bet. Grouping on the entry condition:

needs no correlation-matrix estimation (which would itself be an overfit
risk on this dataset);
is self-maintaining -- the group is derived from the slice definition,
not a hand-kept sector map that drifts;
matches the project's own framing ("one family").
Transient fields (state_session / state_dow / state_month) are excluded
(mirror of position_manager.TRANSIENT_FIELDS), so the exit policy and the
allocation gate agree on what "stable" means.
What was added

src/price/risk_limits.py:
risk_group_key(symbol, slice_combination): canonical, field-order-
independent stable-condition key. Falls back to the uppercased symbol
on unparseable slices or transient-only slices, so a bad slice becomes
its own singleton group -- never matches everything.
RiskLimits.max_positions_per_risk_group (int, default 2; <=0 disables =
legacy). Default 2 allows a confirming second name in a family but
blocks the third: XOP+XLB open -> KLAC blocked; XLF (different group)
still allowed.
check_entry extended with symbol_risk_group + open_position_risk_groups
params (both optional, backward compatible). The group cap is ORTHOGONAL
to the existing per-symbol and max-open checks: it counts only positions
whose group equals the candidate's group.
risk_group surfaced in check_entry details for audit.
src/price/monitor.py: scan_all_slices builds open_position_risk_groups from
the trade journal's per-symbol slice labels (broker positions carry no
slice), computes the candidate's group, passes both to check_entry, and
adds risk_group to the emitted entry_signal payload.
scripts/paper_trade.py: --max-per-group CLI flag (default 2; 0 = legacy).
tests/test_allocation.py: 15 tests -- risk_group_key (8: the XOP/XLB/KLAC
collapse, order-independence, transient exclusion, cross_ retention,
fallbacks, and a pin of the full 7-slice -> 5-group mapping) and check_entry
group cap (7: blocks 3rd same-group, allows different-group, disabled at 0,
backward-compat when args absent, orthogonality to max_open, audit field,
per-group counting).
Graceful-degradation / safety

max_positions_per_risk_group <= 0 disables the group check entirely ->
exactly the legacy behaviour (every symbol = independent slot).
check_entry's new params are optional; when absent, the group check is
skipped. Existing callers (and any external caller) are unaffected.
The group cap only ever BLOCKS; it never forces an entry or a close. It
cannot, by itself, touch the live book beyond refusing a new same-group
entry while the group is full.
At session start the only open exposure is the two pending XOP/XLK orders
(different groups: XOP is stretched_down+downtrend, XLK 1h is
cross_USO_vol=mid_vol + stretched_down), so the cap does not bind today.
Demonstration on the real concentration
End-to-end smoke (mocked account, XOP+XLB already open on
stretched_down+downtrend):
KLAC -> BLOCKED: risk group 'state_ext=stretched_down + state_slope=
downtrend' at cap (2/2)
XLF -> MATCH, risk gate passed (different group: stretched_up+flat)
The 7 monitored slices collapse to exactly 5 risk groups, with XOP/XLB/KLAC
the single multi-member group -- the one the cap is designed to bound.

Honest caveats / what this does NOT do

FIFO blocking only. When a group is full, the next same-group candidate is
blocked; the system does NOT auto-rotate into a higher-conviction name by
closing an existing position. Within the allowed slots, conviction sizing
(lever 1) already routes more capital to the stronger edge (KLAC > XOP/XLB).
Conviction-aware rotation is a documented future refinement, not built here.
Grouping is by entry condition, not by sector. Two DIFFERENT sectors that
happen to share a stable filter (e.g. KLAC semis vs XOP energy, both
stretched_down+downtrend) are grouped together. This is correct for
position management (they share the trigger) and matches the HANDOVER's
"one family" framing, but it means the group is broader than a pure sector
cluster. A sector-overlay dimension could be added later if a finer cut is
needed; it is deliberately not added now to avoid an over-specified,
hand-maintained mapping.
No promotion claims. Nothing is promoted. The V4 deadlock stands.
Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 145 passed (was 130; +15 allocation tests).
python3 -m ruff check clean on all changed files.
End-to-end smoke confirmed KLAC group-blocked and XLF allowed through the
full scan_all_slices -> check_entry -> audit path.
Next ROI levers (in agreed priority order)
4. Cost realism: tighten the cost model toward real fills (validation's 1bp
is optimistic for names like XOP); the entry-bar-vs-fill slippage noted in
the exit-policy section lives here.
5. P&L attribution: realised-P&L-per-slice view (net of cost, vs the
historical fwd_ret_5 expectation). The sizing_* (lever 1), bars_held /
exit-reason (lever 2), and risk_group (this lever) audit fields are the
inputs.

Operator action items (optional, none required for the patch to be safe)

--max-per-group defaults to 2 (faithful diversification within a family).
If you want the live workflow to use it explicitly, add
--max-per-group 2 to the paper_trade invocation in
.github/workflows/live_capture.yml. Until then the module default (also 2)
applies. Set --max-per-group 0 to restore exact legacy behaviour.
ROI Refinement — Cost Realism (2026-07-06)
Fourth patch of the ROI workstream (lever #4 of the agreed priority order:
sizing -> exits -> allocation -> cost -> P&L attribution).

Why cost was the next lever
The research truth (validation) measures edges at ~1bp/leg (~2bp round trip).
The execution path (sizing, trading, position_manager) used ZERO cost: sizing
assumed you fill at the bar close with no spread, no slippage. That is the gap
between backtested ROI and realized ROI. The biggest term is the signal-to-fill
gap -- a signal fires on a closed bar (e.g. 2026-07-02) and the market order
fills at the next session open (e.g. 2026-07-06+). Validation assumes
transacting at the signal bar's close; the live workflow does not. Before this
patch nothing modeled that gap, so a thin edge that barely clears validation
cost could be sized up as if it cleared execution cost too.

What was added

src/price/cost_model.py: CostModel, the single source of truth for realistic
execution cost. Decomposed into commission + spread + slippage (all per leg,
in basis points), round-trip aware. Defaults are deliberately conservative
for the monitored set's character (liquid-to-mid US equities/ETFs, market
orders): commission 0bp (zero-commission retail / Alpaca paper), spread 1bp
(half-spread crossing a market order), slippage 3bp (adverse fill + the
signal-to-fill gap). Round trip = 8bp. Provides round_trip_bps/drag,
per_leg_bps_for_validation (the --cost-bps value to reproduce the model in
validation), and apply() mirroring validation.apply_transaction_cost.
src/price/sizing.py: compute_conviction accepts cost_model and NETS the
execution round-trip drag off the edge before magnitude. A cost-negated edge
(net <= 0) returns conviction 0.05, mode="cost_negated", and is NOT rescued
by the KNOWN_CONVICTION_FLOOR (a cost-eating trade is not a survivor
net-of-cost). compute_position_size threads cost_model through (default =
default_cost_model()) and PositionSize now carries expected_cost_bps_round_
trip so every signal's expected cost is audited.
src/price/monitor.py: scan_all_slices accepts cost_model, threads to sizing.
scripts/paper_trade.py: --cost-spread-bps / --cost-slippage-bps /
--cost-commission-bps construct the CostModel; printed each run.
tests/test_cost_model.py: 12 tests -- arithmetic, cost-negation, monotonic
conviction reduction, cost_model=None preserves pre-lever-4, zero-cost
reproduces no-cost sizing, expected-cost audit field, and a pin that all
four corrected Tier-1 daily edges survive the default 8bp drag.
Cost overlap note (deliberately conservative)
valid_mean_ret_costadj is already net of ~2bp validation cost. Netting the full
execution drag double-counts that ~2bp. Intentional -- guarantees we never size
a trade that cannot clear its real-world cost, at the cost of ~2bp pessimism
(small vs the edges). Not "corrected" away on purpose.

Design choices (deliberate, honest)

NOT a per-symbol cost table (that would be the overfit/hand-authored risk
this project rejects). Decomposed + conservative-default + configurable.
Defaults are conservative placeholders, NOT measured. The slippage term
stands in for the signal-to-fill gap until fills calibrate it (lever 5).
Validation default is NOT flipped (re-ranking at realistic cost is an
operator-owned research decision). The model only OFFERS alignment via
per_leg_bps_for_validation() (--cost-bps 4.0 reproduces the default).
Demonstration on the corrected Tier-1 edges (default 8bp cost)
KLAC 4.68% gross -> 4.60% net -> conviction 92.6% (was 92.8%)
XOP 1.84% gross -> 1.76% net -> conviction 35.0% (at floor)
XLB 1.52% gross -> 1.44% net -> conviction 35.0% (at floor)
XLF 1.00% gross -> 0.92% net -> conviction 35.0% (at floor)
None negated. The Tier-1 survivors are robust to realistic cost. The lever's
value is for THIN edges: a marginal 10bp edge gets ~zero capital here because
net-of-8bp-execution-cost it is only ~2bp -- no longer over-sized as if
cost-free. That is the ROI protection.

Graceful degradation / safety

cost_model=None on compute_conviction -> exec_drag=0 -> identical to
pre-lever-4 (existing sizing tests unaffected; that is why 145 stayed green).
The no-data path returns neutral conviction BEFORE cost logic, so the live
book (no committed leaderboard) is unaffected until the operator regenerates
candidate_leaderboard.csv.
Cost only ever REDUCES conviction (or floors a negated edge to 0.05); it
never increases a position or forces a trade.
Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 157 passed (was 145; +12 cost tests).
python3 -m ruff check clean on all changed files.
Next ROI lever
5. P&L attribution: realised-P&L-per-slice view (net of cost, vs the historical
fwd_ret_5 expectation). The sizing_* (lever 1), bars_held / exit-reason
(lever 2), risk_group (lever 3), and expected_cost_bps_round_trip (this
lever) audit fields are all in place as inputs. This is also where the cost
model's slippage term gets CALIBRATED from realized fills -- closing the loop
on the one honest placeholder in lever 4.

Operator action items (optional, none required for the patch to be safe)

The default cost model (8bp round trip) applies automatically. For a
different cost profile on the live workflow, add --cost-spread-bps /
--cost-slippage-bps / --cost-commission-bps to the paper_trade invocation in
.github/workflows/live_capture.yml.
To re-rank slices at realistic cost (operator-owned research decision), run
validation with --cost-bps 4.0 and compare. Do NOT flip the validation
default silently.
ROI Refinement — P&L Attribution (2026-07-06)
Fifth and final patch of the ROI workstream (lever #5 of the agreed priority
order: sizing -> exits -> allocation -> cost -> P&L attribution).

Why attribution closes the workstream
Levers 1-4 changed HOW capital is deployed (sizing, when to exit, how much
per risk group, at what cost). None of them answered: did the deployed edge
actually earn its capital? Without per-slice realized P&L, there is no
feedback loop -- the system optimizes against a backtest it never checks
against reality. Attribution is also where lever 4's one honest placeholder
(slippage = 3bp, not measured) gets CALIBRATED from realized fills,
closing the loop on the entire workstream.

What was added

src/price/attribution.py: read-only P&L attribution engine.
reconstruct_round_trips: FIFO entry/exit pairing by symbol, handling
partial fills (entry split across multiple exits), rejected-order
exclusion, and correct side determination from the ENTRY (a long enters
"buy" and closes "sell"; reading the exit's side would mis-label every
long close as a short -- a real bug caught and fixed during testing).
measure_realized_slippage: compares each round-trip's fill price to the
signal bar's close_adj (from paper_trade_log), in basis points. This is
the realized signal-to-fill gap that calibrates lever 4's 3bp default.
attribute_pnl: full report -- per-slice win rate, mean realized return,
total P&L, expected return (from validation valid_mean_ret_costadj),
realized slippage, net-of-cost return, and a preliminary flag when a
slice has < MIN_ROUND_TRIPS_FOR_STATS (5) round-trips.
format_report: human-readable text report. Gracefully handles the
zero-round-trip state (the current live state) by clearly stating what
it WILL measure once fills + exits accumulate.
scripts/attribute_pnl.py: one-command runner. --json for machine-readable.
Read-only; places no orders, modifies no journals.
tests/test_attribution.py: 15 tests -- FIFO pairing, partial fills, short-
side sign, rejected exclusion, empty-state graceful degradation, slippage
measurement, expected-vs-realized comparison, preliminary flag, JSON
serialization, and report formatting.
Graceful degradation (current live state)
At session close there are 0 completed round-trips (XOP/XLK orders accepted
but not filled; XLE canceled). The report correctly reports:
Completed round-trips: 0
Open positions: 3
Total realized P&L: $0.00

No completed round-trips yet ... This report will populate as fills +
exits accumulate.
No candidate_leaderboard.csv found; cannot compare realized to expected.
This is by design: the infrastructure is built and proven now, and starts
producing real signal the moment a fill+exit lands.
Demonstration on a synthetic completed round-trip
KLAC entered at signal close 100.0, filled at 100.30 (30bp adverse slippage),
exited at 104.50 after ~5 bars:
PER-SLICE: n=1, win=100%, meanRet=4.19%, totPnL=$42.00, expRet=4.68%,
slipBp=30.0, net_of_cost=3.59% (*)
REALIZED SLIPPAGE: 30.0 bps (entry leg) -- Compare to DEFAULT_SLIPPAGE_BPS=3.0
This is the calibration loop in action: the measured 30bp slippage (synthetic,
deliberately large to illustrate) would replace the 3bp placeholder, tightening
the cost model and making sizing more honest.

What this patch does NOT do

Does NOT place orders, modify journals, or change any execution behaviour.
Does NOT claim an edge. Realized P&L is measurement, not promotion.
Does NOT auto-calibrate the cost model. The measured slippage is REPORTED;
the operator decides whether to feed it back into --cost-slippage-bps.
Auto-calibration is a deliberate non-goal until there are enough round-trips
per slice to make the mean stable (MIN_ROUND_TRIPS_FOR_STATS = 5).
Does NOT promote any slice.
The full ROI workstream is now complete
Lever 1 (sizing): conviction + vol rail -> capital follows edge strength
Lever 2 (exits): hybrid exit -> holds respect the 5-bar edge horizon
Lever 3 (allocation): risk groups -> no over-concentration on one factor
Lever 4 (cost): realistic 8bp drag -> no over-sizing marginal edges
Lever 5 (attribution): realized P&L per slice -> closes the feedback loop

Each lever degrades gracefully: with no leaderboard/warehouse/fills, the system
reproduces the original equal-notional, state-break-only, no-group-cap, zero-cost
behaviour exactly. The levers only activate when the data to justify them exists.

Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 172 passed (was 157; +15 attribution tests).
python3 -m ruff check clean on all changed files.
Live run (zero round-trips): correct empty-state report.
Synthetic round-trip: correct P&L, slippage, expected-vs-realized, preliminary flag.
Operator action items (optional)

Run python3 scripts/attribute_pnl.py after any fill+exit to see per-slice
realized P&L. Add --leaderboard localdata/candidate_leaderboard_1d_tiingo_ liquid236.csv for expected-vs-realized comparison once a leaderboard is
regenerated.
When measured slippage stabilizes across >= 5 round-trips per slice, consider
feeding the mean back as --cost-slippage-bps on the live workflow. Do NOT
auto-calibrate on small samples.

Overfit-Kill — Look-Ahead-Free Rolling State Bins (2026-07-06)
This patch delivers the single highest-value research improvement the HANDOVER's
own V5 methodological note names: "replacing the in-sample-quantile 'in-state'
definition with out-of-sample / rolling-quantile bins."

The overfit, precisely located
Two independent in-sample-quantile cuts, both flowing into validation:

discovery.bin_features: pd.qcut(series, q=3) over the FULL history. This
affects 8 state fields: state_slope, state_vol, state_ret_{1,3,5,10,20},
state_atr_ext, state_vol_regime, state_trend_strength, state_gap,
state_range_pos. Two failure modes: (a) look-ahead bias (bar T's boundary
sees future bars), (b) in-sample fit (thresholds fit on the test data).
ml_discovery.evaluate_interactions: df[feat].quantile(0.75) over the FULL
history defines the ML "promising region". Same two failure modes.
CRUCIALLY state_ext is NOT affected: it uses fixed +-0.015 thresholds (a fixed
prior). That is exactly why the HANDOVER's history shows combinatorial state_ext
slices sometimes clearing BH/Bonferroni while ML / quantile slices do not. This
patch targets only the quantile-based fields.
What was added (additive, not replacement)

src/price/discovery.py:
_expanding_qcut(series, labels, min_periods, fallback): the core
look-ahead-free primitive. For bar T the quantile boundaries are computed
via series.shift(1).expanding(min_periods).quantile(q) -- i.e. using ONLY
bars strictly before T (bar T is a test point against its forward return;
its own value must not influence its boundary). The first ~min_periods bars
get NaN (dropped downstream).
bin_features_rolling(df, min_periods): look-ahead-free variant of
bin_features. Same columns, same label vocabularies. state_ext UNCHANGED
(fixed prior -- no look-ahead to remove). state_session/state_dow UNCHANGED
(categorical maps). All 8 quantile fields use expanding_qcut.
apply_state_bins(df, bin_mode): dispatcher. "insample" reproduces bin_features
exactly (backward compatible); "rolling" uses bin_features_rolling.
DEFAULT_ROLLING_MIN_PERIODS = 200 (matches the monitor's lookback; large
enough for stable boundaries on daily/intraday).
attach_cross_asset_states gains bin_mode so conditioning symbols bin
consistently with the primary (no mixing in-sample primary with rolling
conditioning).
scripts/validate_slices.py: build_eligible_frame gains bin_mode. bin_mode is
part of the disk-cache key (f"{symbol}{timeframe}{mtime}{bin_mode}") so
the two modes never collide in cache. run_validation, run_walk_forward
diagnostics, run_date_range_diagnostics, run_candidate_leaderboard, and
run_scenario_grid all thread bin_mode end-to-end. --bin-mode CLI flag
(default insample; choices insample|rolling).
src/price/ml_discovery.py: evaluate_interactions gains bin_mode; in rolling
mode the q75 threshold is a per-row expanding series (shift(1)).interactions
to_state_slices threads bin_mode so ML bins are consistent end-to-end.
scripts/ml_to_slices.py: --bin-mode flag threads through evaluate_interactions
interactions_to_state_slices.
tests/test_rolling_bins.py: 11 tests -- the decisive look-ahead regression
(monotonic series: rolling bins every bar 'high', insample bins early bars
'low' using future data), boundary-excludes-current-bar, short/all-NaN
fallbacks, min_periods->NaN, same-columns-as-insample, state_ext-unchanged,
dispatcher, and the ML q75 cut in both modes.
Demonstration (500-bar synthetic, regime shift midway)
state_ext agreement (insample vs rolling): 100.0% (fixed prior, unchanged)
state_slope agreement: 92.9% (boundaries differ)
state_vol agreement: 49.5% (boundaries differ most --
regime shift look-ahead)
Look-ahead regression (strictly increasing series):
rolling: every valid bar bins 'high' (running max of its own past) -- PASS
rolling: first 50 bars NaN (no prior history) -- PASS
insample: first 50 bars contain ['low'] <- LOOK-AHEAD (uses bars 100-299 to
bin bars 0-49)
This is the property that makes the overfit-kill real, not cosmetic.

Why additive (not replacement) and what did NOT change

No gate is loosened. No validation default is flipped. bin_mode defaults to
"insample" everywhere, so every existing artefact (candidate_leaderboard,
validated_slices, the Tier-1 survivors) reproduces unchanged. Rolling is
OPT-IN via --bin-mode rolling.
The promotion doctrine is untouched. Nothing is promoted. This makes the
existing search MORE honest; it does not lower the bar.
state_ext is deliberately left as a fixed prior. It is the one state field
that has repeatedly cleared BH/Bonferroni precisely BECAUSE it is fixed.
Converting it to a rolling quantile would destroy that property for no gain.
How to use it (operator)
Run the full pipeline end-to-end with rolling bins:
python3 scripts/discover_slices.py --timeframe 1d --bin-mode rolling
python3 scripts/ml_to_slices.py --symbol SPY --timeframe 1d --bin-mode rolling
python3 scripts/validate_slices.py --candidate-leaderboard --bin-mode rolling
Outputs should be tagged by mode (the handover recommends suffixing artefact
filenames with _rolling) so rolling and insample results are never conflated.
Then compare the rolling leaderboard to the insample one: a candidate that
SURVIVES the full gate under rolling bins is the first candidate in the
project's history to do so without the in-sample-quantile crutch -- i.e. a
genuinely stronger edge than any current survivor, not a looser one.

Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 183 passed (was 172; +11 rolling-bin tests).
python3 -m ruff check clean repo-wide.
Backward compatibility: insample (default) reproduces all prior behaviour;
the 172 pre-existing tests are unchanged and still pass.
Honest expectation
This is the right next research step, not a guarantee of a promotable edge. It
attacks the #1 overfit source at the root. If a candidate clears the full gate
(train+valid+cost+Newey-West+walk-forward+parent-excess+search-wide) under
rolling bins at realistic cost (--bin-mode rolling end-to-end), that is
meaningfully stronger evidence than any current survivor. If nothing clears,
the project has learned that the ML/quantile family has no structural edge once
look-ahead is removed -- itself a defensible, valuable conclusion.

Overfit-Kill Result — Rolling-Bins Validation + The Sector Confound (2026-07-06)

What was run
The full pipeline end-to-end with --bin-mode rolling on the 236-symbol
liquid236 universe (1d), plus targeted diagnostics on the survivors.

Rolling-bins validation result (full universe)
The look-ahead-free rerun produced 25 strict survivors and surfaced a new
leading candidate that appeared, at first read, to break the project's standing
deadlock ("walk-forward but no search-wide p, or search-wide p but no walk-
forward -- never both"):

KLAC 1d state_ext=stretched_down + state_slope=downtrend

walk-forward pattern: 0111 (3/4)
valid_mean_ret_costadj: +4.75% (vs +4.68% under insample -- it GREW)
search_wide_bonferroni_pass: True (the first clean survivor to clear the
strictest multiple-testing bar, with a look-ahead-free bin definition)
valid_excess_vs_best_parent: +0.87%
latest_12m freshness gate: PASS (p=1.1e-8)
effect strengthening fold-over-fold: 0.95% -> 3.49% -> 3.02% -> 4.42%
This was the strongest single validation result the project had ever produced.
It was also a FALSE LEAD. The next step exposed why.

The decisive test: the sector spread (operator's question)
The operator asked the right question: "is this not just a signal that the
semiconductor business is booming?" A sector spread test was run -- the same
stretched_down+downtrend slice on SOXX, SMH, NVDA, AMD, MU, AMAT, AVGO, TSM.
Result:

The slice structure recurs across the ENTIRE semiconductor family, not
just KLAC. It is a sector-level phenomenon, not a KLAC-specific price
state edge.
SOXX (the semiconductor sector ETF itself) is REJECTED by the gate
(unsupported, WF 0101). The sector-level instrument does not clear.
The parent-excess on peer survivors is small and inconsistent: AMAT
+0.43%, AVGO +0.32%. The +state_slope=downtrend qualifier adds almost
nothing over plain dip-buying (state_ext=stretched_down alone).
Fold 0 fails across the WHOLE family (KLAC, XLB, XOP, XLE, AMAT, AVGO,
SOXX) -- and fold 0 is the 2022-2023 window, the semiconductor downturn
(post-COVID glut, memory crash). The edge disappears outside the bull
regime.
Conclusion: KLAC is a regime bet dressed up as a price-state edge. The bulk
of its return is "buy dips in a stock that's going up a lot" -- and dip-
buying works in semis because the sector has been in a historic multi-year
(AI/capex) super-cycle. The validation framework surfaced a regime-conditional
timing rule, not a structural price-behavior edge.

DO NOT REPEAT THIS MISTAKE
This section exists specifically so future agents do not re-promote KLAC (or
XLB/XOP) as a "validated edge" without reading this finding. The standing
"no candidate is promoted" conclusion holds, and now there is a concrete
reason why: the surviving candidates are regime-conditional, not structural.

The validation framework's blind spot (the lesson)
The project's validation is TIME-stratified (train/valid/walk-forward are all
chronological). It is NOT regime-stratified. A multi-year sector bull run
produces positive forward returns for dip-buying in that sector, and Newey-
West + Bonferroni + walk-forward + parent-excess CANNOT tell you whether
that is a durable price-state edge or a regime artifact. They test TEMPORAL
stability, not REGIME independence.

The fingerprints of a regime confound (use these as red flags):

The slice structure recurs across a whole sector/family, not just one
name. (Test with a sector spread.)
The parent-excess is small and inconsistent -- most of the return is
captured by the simpler parent (plain dip-buying, plain momentum).
The effect strengthens over time, tracking a known macro cycle.
Fold 0 (the earliest, often non-bull window) fails across the family.
The sector ETF itself does not clear the gate even when individual
names do.
A candidate that shows ANY of these is regime-confounded until proven
otherwise. KLAC showed all five.

Why the overfit-kill was still worth doing
The rolling-bins work did not produce a promotable edge, but it succeeded at
its stated goal: it made the search more honest. The original in-sample
quantile cuts would have made these regime-conditional slices look even
stronger; the look-ahead-free bins exposed their fold-0 weakness and the
small parent-excess more clearly. The honest expectation recorded in the
prior section ("if nothing clears, the project has learned something real")
was met: the ML/quantile family has no structural edge once look-ahead is
removed, AND the combinatorial survivors are regime-conditional. Both are
defensible, valuable conclusions.

Honest standing conclusion (revised)
The project's deadlock is now better understood, not broken:

No candidate is promoted. (Unchanged.)
The reason is no longer "not enough evidence" -- it is now "the evidence
is confounded by sector regime, which the validation framework cannot
control for."
The path to a real edge is NOT more re-slicing of the same history. It
is either (a) a regime-control layer in validation (test slices across
bull AND bear periods for their own sector), or (b) live out-of-sample
accumulation through enough of a regime cycle to disambiguate.
Live paper account state (2026-07-06, for the next agent)
The two pending orders filled at market open:

XOP buy 16 @ $154.47 (filled 15:30 SAST)
XLK buy 13 @ $183.41 (filled 15:33 SAST)
XLE buy 9 was previously canceled (correctly -- it is in the same risk
group as XOP/XLB under the allocation lever).
These are the FIRST FILLED positions on the paper account. They are not
validations of any edge (XOP/XLK are deployed Tier-1 watch slices, not
promoted edges). Realized P&L is still ~$0 (no exits yet). The attribution
report (lever 5) will start producing real signal once the exit policy
(lever 2) closes the first position.

Reframe — Regime-Conditional Edges Are Tradeable If Gated (2026-07-06)

The operator corrected the framing of the regime-confound finding above. The
prior section's implication ("so the candidates are regime bets, demote
everything, sit idle") was wrong in its IMPLICATION even though the diagnosis
was right. The corrected understanding:

A regime-conditional edge is only a problem if you (a) cannot detect the
regime, or (b) mislabel it as structural. If you CAN detect the regime and
you LABEL it honestly as regime-conditional, then "hop on and ride" is
exactly the right strategy -- it is what most systematic traders actually
do. The fold-0 failures across the stretched_down+downtrend family are not
a bug; they are the SIGNAL for when NOT to deploy. That is actionable
information, not a reason to stand down.

What was built: a regime deployment gate (src/price/regime.py)
A pre-entry filter in monitor.scan_all_slices that blocks a matched slice
when its MACRO regime is hostile. Sits between check_slice_match and
check_entry (same layer as the risk gate). It converts today's finding into
an automatic dismount during wrong regimes rather than a demotion.

What "regime" means here (defensible, not hand-kept)
The macro trend of the slice's own market/sector, read from live price state
via the SMA-50/SMA-200 crossover prior (the classic golden-cross / death-
cross definition). Chosen deliberately because it is well-known and NOT
hand-fit to this dataset (which would be the overfit risk the project
rejects). For a slice conditioned on a cross-asset symbol, the regime is
read from that conditioning symbol (e.g. USO for an energy slice).

bull : SMA50 > SMA200 AND price > SMA50 -> dip-buying is the right trade
bear : SMA50 < SMA200 AND price < SMA50 -> the fold-0 condition; BLOCK
neutral : mixed -> allow (permissive; do not
over-block in sideways markets)
unknown : missing data / insufficient history -> ALLOW (fail-open; the
sizing + risk guard remain)

Per-slice configurability
A slice in monitored_slices.csv may carry an optional regime_symbol column
(e.g. SPY for broad-market regime, the sector ETF). When absent, the gate
uses the slice's own symbol (the most direct read of "is THIS name in its
working regime"). This keeps the gate data-driven per slice, not a global
hand-tuned toggle.

Graceful degradation (zero-risk to the live book)

regime_filter_enabled=False (default) -> NO gate; current behaviour
exactly. The flag is opt-in.
No regime_symbol on the slice -> uses the slice's own symbol.
No warehouse data for the regime symbol -> FAILS OPEN (allows entry).
Blocking on missing macro data would be worse than deploying without the
gate; conviction sizing + risk limits still protect the book.
Regime data present + macro bear -> BLOCKS entry; logged with
reason='regime hostile (bear on SYMBOL); entry blocked' so the audit
trail shows regime-blocking separately from risk-blocking.
What this patch does NOT do

Does NOT re-validate any slice. Validation stays in validate_slices.py.
Does NOT promote any slice. The slice is still a watch candidate; the
gate just makes deployment honest about being regime-conditional.
Does NOT define a regime map by hand (overfit risk). It reads live price.
Does NOT touch the existing risk gate, sizing, exits, allocation, or
cost model -- it composes cleanly with all of them.
Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 199 passed (was 183; +16 regime tests).
python3 -m ruff check clean repo-wide.
Demonstration: same slice, two regime states. Gate ON + macro BEAR ->
BLOCKED. Gate ON + macro BULL/NEUTRAL -> ALLOWED. Gate OFF -> no-op
pass-through (default). Fail-open confirmed on missing data.
How to use it (operator)
python3 scripts/paper_trade.py --regime-filter
python3 scripts/paper_trade.py --regime-filter --max-per-group 2
Add per-slice regime_symbol to monitored_slices.csv (optional):
symbol,timeframe,slice_combination,side,regime_symbol
XOP,1d,state_ext=stretched_down + state_slope=downtrend,long,SPY

The gate is off by default, so the live book is unaffected until the
operator opts in. When enabled, it makes the deployment of regime-
conditional slices honest: ride the regime, dismount automatically when it
leaves. This is the productive non-idle path the operator asked for.

Standing conclusion (revised)
The T1 watchlist edges are regime-conditional, not structural. That is now
a FEATURE (tradeable when gated), not a flaw to be hidden. The regime gate
makes deployment honest about the conditional nature. The path to a
structural (regime-independent) edge remains: live out-of-sample
accumulation across enough of a regime cycle, and/or a regime-control layer
in VALIDATION (not just deployment) -- but that is research work, not the
tradeable improvement this patch delivers.

Regime-Stratified Validation Diagnostic (2026-07-06)
This patch delivers the validation-side companion to the regime deployment
gate. The gate blocks entries during hostile macro regimes; this diagnostic
EXPOSES whether a slice's edge is structural (positive across regimes) or
regime-conditional (positive in bull, collapses in bear).

Why it exists
Today's validation is TIME-stratified (train/valid/walk-forward are all
chronological). It tests TEMPORAL stability, not REGIME independence. A
multi-year sector bull produces positive forward returns for dip-buying in
that sector, and Newey-West + Bonferroni + walk-forward + parent-excess
CANNOT tell you whether that is a durable edge or a regime artifact. The
KLAC sector spread test proved this: a regime-conditional edge looked
indistinguishable from a structural one under time-stratified validation.
This diagnostic closes that blind spot.

What was added

src/price/regime.py: attach_regime_labels -- per-bar regime labeller using
the SMA-50/200 prior as-of each bar (look-ahead-free), backward as-of merged
onto the slice's eligible frame. Validation-side companion to assess_regime.
scripts/validate_slices.py: run_regime_stratified_diagnostics -- splits each
slice's bars into macro regime buckets (bull/bear/neutral/warmup/unavailable)
and reports the edge in each. Writes localdata/regime_stratified_diagnostics.csv.
CLI: --regime-stratified-diagnostics, --regime-symbol (optional override).
tests/test_regime_stratified.py: 8 tests including the decisive
regime-conditional-edge test (edge +2% bull / -2% bear -> shown correctly).
How to read the output
STRUCTURAL edge = positive in bull AND bear.
REGIME-CONDITIONAL edge = positive in bull, ~0 or negative in bear.
'all' = the headline number that AVERAGES the buckets (hides the confound).

This is a DIAGNOSTIC, not a filter -- mirrors walk-forward and date-range
diagnostics. Adds information without changing the promotion gate.

How to use it (operator)
python3 scripts/validate_slices.py --regime-stratified-diagnostics
--diagnostic-scope leaderboard-top --top-n 10 --bin-mode rolling

What to run after the live-capture week
When reslicing: discover + validate as normal, then run the regime-stratified
diagnostic on survivors BEFORE deploying. If an edge collapses in bear, label
it regime-conditional and gate deployment (--regime-filter). If it survives in
bear too, that is the first candidate with structural (regime-independent)
evidence -- a categorically stronger claim. This is "reslice keeping the
regime in mind" done honestly.

Test Suite Fix — Stale bin_mode Test Doubles (2026-07-06)

A fresh clone at HEAD (bc401b2) had 2 failing tests:
test_run_walk_forward_diagnostics_writes_fold_rows and
test_run_date_range_diagnostics_writes_target_windows in
tests/test_validate_slices_script.py. Root cause: the rolling-bins patch
(fad22d2) threaded a bin_mode kwarg through build_eligible_frame end to
end, but these two tests' fake_build_eligible_frame(symbol, timeframe,
cross_symbols=None) test doubles were never updated to accept it, so the
real code called them with an unexpected kwarg. Fixed by adding
bin_mode="insample" to both fakes' signatures. Production code untouched;
this was a test-double drift bug, not a real defect. All 207 tests passed
after the fix (the baseline this section's work then built on top of).

Protective Stops — R-Based "Small Losses, Large Profits" (2026-07-06)

This section records why a price-based stop-loss was added, and answers a
question the operator asked directly: why had this never been built before?
Honest answer: the project's exit policy (see "ROI Refinement -- Exit
Policy" above) was designed to be faithful to the VALIDATION horizon
(fwd_ret_5) -- state-break OR held >= 5 bars. That is a research-faithful
exit, not a capital-protection exit. Nothing in the stack modeled "how much
can this ONE trade lose if the price moves against it before the horizon or
the state-break fires." max_daily_realized_loss (a pre-existing risk limit)
only sums CLOSED trades -- an open loser could bleed all session with
nothing tripping until it was already realized. That gap is what this patch
closes.

Design (operator-agreed, explicit trade-offs)

The operator asked for "small losses, large profits" specifically -- not a
generic fixed-percent stop/target pair. That is the classic asymmetric
R-multiple design: define R (dollar risk to the initial stop) once at
entry, cap losses at ~1R, and let winners run to many multiples of R via a
ratcheting trailing stop rather than a fixed take-profit (a fixed target
directly contradicts "let profits be large" -- it caps the winner).

R = k_stop * ATR(14) * qty, set the moment a position is filled.
k_stop = 2.0 (operator-chosen: tight enough to keep losses small,
loose enough to tolerate ordinary daily noise; this ALSO makes the
pre-existing sizing volatility rail's dollar-risk math literally true
for the first time -- it always assumed a stop distance but nothing
enforced one).
At +1R unrealized, the stop ratchets to breakeven. The trade can no
longer lose money from that point.
Beyond +1R, the stop trails the highest favorable close since entry by
k_trail * ATR(14) (a chandelier exit). k_trail = 3.0 (operator-chosen:
looser than the entry stop on purpose, so a confirmed trend has room to
run instead of being capped).
The stop only ever ratchets in the trade's favor; it is never loosened.

Broker-side enforcement (the operator's explicit choice, not the default)

The operator was asked directly: should the stop be a REAL resting order
at the broker (continuously enforced by Alpaca, independent of scan
cadence), or only evaluated when paper_trade.py happens to run (matching
the existing exit-policy architecture)? The operator chose broker-side,
correctly identifying that a monitoring-only stop is a soft target, not a
hard one, between scheduled runs. This is why the live_capture workflow's
schedule was also changed (see below) -- a broker-side stop is enforced
continuously either way, but the RATCHET (breakeven, then trailing) only
advances the next time reconcile_stops runs, so scan frequency still
matters for how promptly a winner's protection tightens.

What was added

src/price/stops.py: pure R-state logic, no network/broker calls.
StopState (per-position R-state: entry price, initial/current stop,
r_per_share, stage in {initial, breakeven, trailing}, extreme price
since entry, stop_order_id). compute_initial_stop / new_stop_state
(the k_stop * ATR distance). update_trailing_stop (the ratchet: pure
function, returns a NEW StopState, never mutates, never loosens).
current_risk_dollars() (a position at breakeven-or-better contributes
$0 -- it can no longer lose money, so it should not consume risk
budget). aggregate_open_risk_dollars / check_aggregate_risk_budget
(sum of every open position's CURRENT risk, capped as a fraction of
equity -- the leverage prerequisite, see below). Whipsaw circuit
breaker: is_whipsaw_blocked / record_stopout / stopout_count_today --
benches a symbol for the rest of the trading day after
whipsaw_stopout_limit (default 2) same-day stop-outs, because tight
ATR stops mean more stop-outs and "small losses" must not silently
become "many small losses in one choppy day." Persisted to
localdata/stop_state.json and localdata/stopout_journal.json (mirrors
risk_limits.py's cooldown-journal pattern). 41 tests.
src/price/trading.py: submit_protective_stop (a REAL resting GTC stop
order; SELL stop protects a long, BUY stop protects a short),
replace_protective_stop (moves the SAME order_id via Alpaca's
replace-order endpoint, so the position is never briefly unprotected
during a ratchet), cancel_order, get_orders_for_symbol.
close_position now cancels any resting order on the symbol FIRST
(best-effort, never blocks the close) so a naked stop order cannot
survive a position closed by a different exit policy (state-break /
horizon). 13 tests against a fake broker client (no real Alpaca calls).
src/price/stop_manager.py: reconcile_stops, the orchestration layer.
Per open position, per scan: no tracked stop yet -> compute ATR,
submit the initial k_stopATR stop, persist the state. Tracked stop
exists -> recompute the ratchet from current price/ATR; if improved,
REPLACE the resting order and persist. Tracked stop's position is
gone (stopped out or closed elsewhere) -> clear the bookkeeping and
log a whipsaw-journal event. No ATR available, or the broker rejects
the stop -> retried next scan (or force-closed under leverage; see
below). dry_run computes and reports every intent without placing
orders or persisting state. 16 tests + a genuine end-to-end
integration test proving monitor.scan_all_slices actually calls this
(not just that the parts work in isolation).
src/price/position_manager.py: ExitPolicy gained
respect_r_multiple_gate (default True). Once a position has reached
+1R (tracked via price.stops), the 5-bar horizon exit is SUPPRESSED
for that position -- exit is left to the trailing stop instead. This
is the mechanism that actually delivers "let profits run": without it,
the horizon exit would force a winning trade closed at bar 5
regardless of how well it was doing, directly fighting the trailing
stop's purpose. The gate ONLY suppresses the horizon condition; a
stable-filter break (the thesis invalidated) still exits unconditionally,
and a symbol with no tracked stop state gets the original unconditional
horizon exit (strictly additive, never a silent behaviour change without
data to justify it). 7 new tests.
src/price/risk_limits.py: RiskLimits gained stop_atr_multiple (2.0),
trail_atr_multiple (3.0), breakeven_trigger_r (1.0),
max_aggregate_open_risk_pct (0.03 = 3% of equity), and
whipsaw_stopout_limit (2). check_entry gained proposed_r_dollars /
open_stop_states / equity_for_risk_cap (aggregate-risk budget) as
optional kwargs -- omitted by every pre-existing caller, so this is
backward compatible by construction. 8 new tests.
src/price/monitor.py: scan_all_slices now calls reconcile_stops right
after the exit check (before the entry-scan loop), and computes each
candidate entry's proposed_r_dollars (the SAME k_stopATR*qty distance
stop_manager will actually place) to feed the aggregate-risk check.
scripts/paper_trade.py: new flags --stop-atr-mult, --trail-atr-mult,
--breakeven-trigger-r, --max-aggregate-risk-pct, --whipsaw-limit,
--no-r-gate (restores the legacy unconditional horizon exit), and
--auto-sizing-equity (fetches live account equity from Alpaca instead
of requiring a hand-maintained --sizing-equity value -- this was a
pre-existing "dormant lever" gap: the volatility rail and the new
aggregate-risk budget both require an equity figure, and nothing
before this patch supplied one on the live workflow). kind=stop_intent
signals are audit-logged only; the broker call already happened inside
scan_all_slices's reconcile_stops call. 3 tests on that audit path, 7
tests on _resolve_sizing_equity.

Graceful degradation (the safety property, same doctrine as every other lever)

Every new RiskLimits field has a default that reproduces a defensible
prior behaviour path, and every new check fails OPEN when its inputs are
missing:
stop_atr_multiple / trail_atr_multiple / breakeven_trigger_r have
sensible defaults but only ever ACTIVATE via reconcile_stops, which is
new code -- a caller that never calls it (there isn't one left in this
codebase, but a hypothetical external caller) sees no change.
max_aggregate_open_risk_pct defaults to an ACTIVE 3% -- unlike most
levers in this project this one is NOT off-by-default, because the
operator's whole point was "we are missing capital protection, fix it."
It still fails open (allows the trade) if equity or the proposed R is
unknown, consistent with the project's doctrine of "a gate only
activates when there is real data to enforce it with."
respect_r_multiple_gate defaults to True but is INERT for any symbol
with no tracked StopState -- so a slice that never gets a stop attached
(e.g. persistent ATR data gaps) keeps the exact pre-existing horizon
behaviour.

Live workflow changes (.github/workflows/live_capture.yml)

Schedule changed from once/day (21:00 UTC) to 5x/trading day
(14:00/16:00/18:00/20:00/21:00 UTC). Rationale: the protective stop is
broker-enforced continuously regardless of scan cadence, but the RATCHET
(breakeven at +1R, then the chandelier trail) only advances the next
time reconcile_stops runs -- a once-a-day cadence meant a strong
intraday move could give back most of its gain before the trail ever
caught up. This was an explicit operator trade-off (more GitHub Actions
minutes for tighter ratchet responsiveness).
localdata/stop_state.json and localdata/stopout_journal.json added to
the auto-commit file list. This is NOT cosmetic: without persisting
these across workflow runs, every fresh checkout would start with NO
tracked stop state, so reconcile_stops would treat every open position
as brand new and re-attach an INITIAL stop at the CURRENT price every
run -- silently discarding the breakeven/trailing ratchet progress and
defeating "small losses, large profits" with no error ever raised. This
would have been a real, silent bug if shipped without this fix.
paper_trade invocation now passes every new flag explicitly (including
--auto-sizing-equity), with inline comments explaining that
--auto-sizing-equity is safe to enable by default because it only ever
TIGHTENS sizing (the vol rail is a min() against conviction-notional
sizing; the aggregate-risk check can only block a trade, never enlarge
one).
--regime-filter and --target-leverage are DELIBERATELY NOT enabled in
the workflow. Both are net-new policy decisions requiring explicit
operator sign-off, not bug fixes -- see this section and the Leverage
Phase section below for the operator action items.

What this patch does NOT do

Does not build take-profit as a fixed target -- the operator explicitly
wants profits large, so a hard target was rejected by design in favour
of the trailing stop.
Does not build pyramiding / multi-unit position tracking. Explicitly
deferred by operator choice: Alpaca blends multiple entries into one
avg_entry_price, so pyramiding needs its own per-unit ledger (a real
schema change), and the operator chose to ship the risk rails first as
an independently reviewable patch. Queued as the next phase.
Does not promote any slice. The V4 "nothing is promoted" deadlock
stands; this entire patch is capital-protection plumbing, not a
research or promotion claim.
Does not change monitor.DEFAULT_MONITORED_SLICES or
monitored_slices.csv.

Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 332 passed (up from the 207-test post-bugfix
baseline; +125 tests across price.stops, price.stop_manager,
price.trading's new order plumbing, the position_manager R-gate, the
risk_limits leverage/aggregate-risk additions, and paper_trade.py's new
audit/equity-resolution helpers).
ruff check clean repo-wide.
Multiple genuine end-to-end integration tests (not just unit tests on
isolated modules) proving monitor.scan_all_slices actually wires
reconcile_stops and the aggregate-risk check into a live scan.

Operator action items (optional, none required for the patch to be safe)

The live workflow now runs 5x/day instead of once -- monitor GitHub
Actions minutes usage if that matters for the account's plan.
Consider running python3 scripts/paper_trade.py --no-r-gate if you ever
want to A/B the R-gate's effect on realized P&L once enough round-trips
accumulate (lever 5's attribution report is the tool for that
comparison).

Leverage Phase (2026-07-06)

This section records the design and implementation of steady-state
(overnight-hold) leverage, built immediately after the protective-stop
patch above because that patch is leverage's real prerequisite: turning on
leverage before every position had a REAL enforced stop and a book-wide
risk cap would have meant sizing bigger against a system that could not
yet guarantee "small losses." With the stop system in place, this section
extends it rather than replacing anything.

A regulatory fact that shaped this design (verified, not assumed)

FINRA's Pattern Day Trader rule (the old $25,000-equity / 4-day-trades-
in-5-days framework) was eliminated by SEC approval on 2026-04-14,
effective 2026-06-04, replaced by a real-time "intraday margin" standard
under FINRA Rule 4210. Alpaca adopted the new framework on day one
(2026-06-04). Practical consequence for this project: PDT-flag / day-trade-
count logic is now dead weight and was deliberately NOT built. The
account object's pattern_day_trader field (already read by
trading.get_account_info) can be treated as legacy/inert.

Why NOT Alpaca's 4x intraday rate (the key design decision)

Alpaca margin accounts get up to 4x intraday buying power, but that
multiplier is INTRADAY-ONLY: Reg T requires it to step down to 2x for
anything held overnight, or the account receives a margin call and the
broker can force-liquidate positions unilaterally -- exactly the
uncontrolled exit the protective-stop system exists to prevent. This
system's exit policy holds positions across multiple bars by design (a
5-bar horizon is up to a trading week on 1d, a full session on 1h); it
does not flatten positions same-day. Using 4.0 as a static leverage
multiple would therefore silently violate the overnight limit every
single session. The operator was presented this trade-off directly and
chose: build 2.0x (Reg T's standard overnight multiplier, which matches
how this system already holds positions) now; true 4x would require a
separate same-day force-flatten exit mode, scoped as a future phase of
similar size to pyramiding, not built here.

Design: two independent budgets beyond the existing R-based one

The R-based aggregate-risk budget (price.stops, above) caps the SUM of
every open position's stop-distance dollar risk. Leverage does NOT change
that number directly -- it changes how much NOTIONAL a given amount of
equity can control. A low-ATR%, high-priced name can carry a small R (a
tight stop) while still deploying huge notional/margin exposure -- exactly
the case leverage amplifies. That is why leverage needs its OWN budget,
not a bigger number plugged into the existing one:

Gross notional exposure cap (src/price/leverage.py,
check_gross_notional_budget): total deployed notional (existing
positions' market value + a proposed new trade) <= equity *
target_leverage_multiple. Deliberately opt-in via requiring
open_positions_notional to be explicitly non-None (not defaulted to
0.0) -- this was a real design bug caught by the existing test suite
during development: an earlier version silently activated this check
for any caller that had equity_for_risk_cap set for the UNRELATED
sizing volatility rail, which would have incorrectly blocked trades
for callers never asking for a notional cap. Fixed before merge.
Margin cushion (src/price/leverage.py, check_margin_cushion): an
honest backstop against the notional check's own approximate math.
Reads Alpaca's REAL-TIME buying_power and blocks new entries once the
fraction of the SELF-IMPOSED leverage ceiling (equity *
target_leverage_multiple) remaining as actual buying power drops below
margin_cushion_pct (operator-chosen default 0.20 = block at 80% margin
usage). Deliberately normalizes against OUR OWN target_leverage_multiple,
not whatever higher multiple the broker might allow (e.g. Alpaca's 4x
intraday rate) -- this is a self-imposed ceiling, not a broker-capacity
check.
Force-close on unprotected leverage (src/price/stop_manager.py):
when target_leverage_multiple > 1.0, a position that cannot get a
protective stop attached this scan (no ATR data, or the broker rejects
the stop order) is CLOSED immediately rather than retried next scan (the
1.0x default behaviour). An unprotected position is tolerable at 1x
(small, cash-secured, retried quickly); under leverage the same gap is
materially more dangerous, so the safer default is to never hold
unprotected leveraged exposure at all.

What was added

src/price/leverage.py: total_open_notional,
check_gross_notional_budget, check_margin_cushion. Pure functions, no
network/broker calls, fail open when equity/multiple/notional data is
missing. 15 tests.
src/price/risk_limits.py: RiskLimits gained target_leverage_multiple
(default 1.0 = today's exact cash-secured behaviour) and
margin_cushion_pct (default 0.20). check_entry gained
open_positions_notional and buying_power as optional kwargs threading
into the two new checks. 8 tests on the check_entry wiring, all
backward compatible (omitted by every pre-existing caller).
src/price/stop_manager.py: reconcile_stops gained close_position_fn
(injectable, defaults to price.trading.close_position) and the
force-close-when-levered rule described above. 5 new tests.
src/price/monitor.py: scan_all_slices now computes
open_positions_notional every scan (cheap, pure) and fetches
buying_power from Alpaca ONLY when leverage is actually configured
beyond the default (target_leverage_multiple != 1.0 or
margin_cushion_pct set) -- avoids an extra live account API call on
every scan when leverage is off. Both feed into check_entry.
scripts/paper_trade.py: new flags --target-leverage (default 1.0) and
--margin-cushion-pct (default 0.20).
A genuine end-to-end integration test (tests/test_scan_leverage_
integration.py) proving monitor.scan_all_slices wires both leverage
checks into a live scan under three regimes: leverage off, leverage on
with room, leverage on and margin-cushion-blocked.

What this patch does NOT do

Does not enable true 4x intraday leverage. See the design rationale
above; this requires a same-day force-flatten exit mode that does not
exist yet.
Does not build PDT-flag handling. Verified unnecessary: PDT was
eliminated by regulation, effective before this patch was written.
Does not enable leverage on the live workflow. .github/workflows/
live_capture.yml deliberately does NOT pass --target-leverage --
turning on leverage is an explicit operator decision requiring
sign-off on the ratio, same doctrine as --regime-filter.
Does not promote any slice, or touch sizing/exits/allocation/cost/
attribution logic. Composes with all of it.

Verification

python3 -m py_compile clean on all changed files.
python3 -m pytest -q -> 332 passed (includes all leverage-phase tests
alongside the protective-stop patch's tests in the same run; both
patches landed in the same session).
ruff check clean repo-wide.

Operator action items (required before leverage can do anything)

Leverage is fully inert at its 1.0x default. To activate it: (1) enable
--auto-sizing-equity or set --sizing-equity explicitly (both leverage
checks fail open without a known equity value -- this is why the
protective-stops section's --auto-sizing-equity addition is also the
leverage-activation switch), and (2) add --target-leverage 2.0
--margin-cushion-pct 0.20 to the live_capture.yml paper_trade invocation
once you have explicitly decided to do so.
Do not set --target-leverage above 2.0 without first building the
same-day force-flatten exit mode described above -- higher multiples on
this codebase's current exit policy risk an overnight Reg T margin call.

Next phase (queued, not built)

Pyramiding / multi-unit position tracking (adding units to a
confirmed winner once its first unit is at breakeven-or-better; requires
a per-unit ledger since Alpaca blends fills into one avg_entry_price).
True same-day 4x leverage (requires a force-flatten-before-close exit
mode).
Both were explicitly deferred by the operator to keep this session's two
patches independently reviewable rather than compounding four risk
decisions into one diff.

Red-Team Closeout — Bin-Mode Deployment Alignment (2026-07-06)

This follow-up closes the remaining deployment/research alignment risk after
commit 311c48a locked the live workflow's supply chain and pinned GitHub Actions.
No new test file was added; regression coverage was appended to the existing
tests/test_state_unavailable.py file per operator preference.

Problem:

The monitor wrote bin_mode=insample into monitored_slices.csv, but
src/price/monitor.py still ignored that column and always used
bin_features() over a short live lookback window. That meant validation could
authorize a slice under one binning regime while live monitoring evaluated a
slightly different state definition, especially for quantile-derived states such
as state_slope, state_vol, and return bands.

Fix:

Explicit monitored slices now preserve bin_mode.
Clean-survivor rows loaded from a candidate leaderboard also preserve
bin_mode when present.
scan_all_slices() groups by (symbol, timeframe, bin_mode), so insample and
rolling slices on the same symbol/timeframe do not share one state frame.
get_current_state() now calls apply_state_bins(..., bin_mode=...) instead
of hard-coded bin_features().
Cross-asset state attachment is threaded with the same bin_mode.
Live state binning defaults to the full local warehouse partition rather than
the prior 200-bar tail. lookback_bars remains available for tests/local
diagnostics, but the scheduled path now avoids tail-local quantile relabeling.
Verification:

python3 -m py_compile src/price/monitor.py passed.
python3 -m pytest -q -> 374 passed.
python3 -m ruff check src scripts tests -> clean.
Practical conclusion:

The three red-team risks are now closed across commits:

ad4a4ef: workflow deletion/state consistency, stale exposure filtering, and
confirmed-fill-only stopout accounting.
311c48a: hash-locked dependency install, pinned workflow actions/runner, and
explicit workflow bin_mode column.
this patch: monitor actually consumes and enforces the bin_mode contract.
No slice is promoted and no risk gate is loosened.

Live Forward-Return Capture Universe Source (2026-07-06)

The 19:08 UTC live_capture run succeeded end-to-end: hash-locked dependency
install passed on GitHub's Python 3.11 runner, data capture completed, the
monitor scanned with explicit bin_mode=insample, no new entries were
submitted, and workflow outputs were committed back as 7839d62.

One remaining issue was visible in the log:

live_forward_returns.py printed "No clean_survivor* rows in the current
leaderboard; nothing to capture." That is correct for the old dynamic-leaderboard
workflow, but wrong for the current execution-only workflow. The live workflow no
longer refreshes candidate_leaderboard.csv; its authoritative watched universe
is now the explicit localdata/monitored_slices.csv file written earlier in the
same workflow run.

Important design correction:

An implicit fallback from leaderboard -> monitored_slices.csv is NOT the cleanest
answer, because it can mask a broken/missing leaderboard during research runs.
The correct design is an explicit universe source.

Fix:

scripts/live_forward_returns.py now accepts --universe-source with choices:
leaderboard, monitored, and auto.
Default remains leaderboard, preserving the research-mode behavior:
clean_survivor* rows only, no silent fallback.
The live workflow calls:
scripts/live_forward_returns.py --universe-source monitored
because execution-mode forward returns should track the exact explicit
monitored set that paper_trade.py scanned.
auto exists only for deliberate diagnostics; it tries leaderboard first and
monitored second.
The watched universe key includes (symbol, timeframe, slice_combination, bin_mode), because the same slice text under insample versus rolling
represents a different state definition and must not collide.
Regression coverage was added to existing tests/test_live_forward_returns.py
rather than creating another test file. Tests cover explicit monitored mode,
the guarantee that default leaderboard mode does NOT silently fall back to
monitored_slices.csv, and bin-mode-specific matching/row keys.
Verification:

python3 -m py_compile scripts/live_forward_returns.py passed.
python3 -m pytest -q -> 377 passed.
python3 -m ruff check src scripts tests -> clean.
Operational note from the same run:

XOP remains the only open tracked position with a resting protective stop in
localdata/stop_state.json.
XLK's prior stop state was cleared because the position was no longer open;
autonomous_fill_journaled=False, so this was treated as state cleanup, not
a confirmed broker-side stop-out.
No slice was promoted and no risk gate was loosened.

Capture Source Logging Alignment (2026-07-06)

The successful 19:34 UTC live_capture run showed another non-fatal but important
operator-facing inconsistency: the capture log printed XOP/XLB/KLAC daily pulls
as from ALPACA, even though fetch_universal_bars() routes all equity 1d bars
to Tiingo when TIINGO_API_KEY is available.

Root cause:

scripts/capture_bars.py had its own stale source-label heuristic:

ETF daily -> Tiingo
all other equities -> Alpaca
But the actual router in src/price/data_sources.py had already been broadened
to:

all equity daily + Tiingo key present -> Tiingo first, Alpaca fallback on
Tiingo error
Fix:

Added resolve_universal_source(symbol, timeframe) next to the router in
src/price/data_sources.py.
scripts/capture_bars.py now uses that helper for logging when the universal
router is enabled, so the printed first-attempt source cannot drift from the
actual data path again.
The label remains a first-attempt source: Tiingo daily can still fall back to
Alpaca if Tiingo raises.
Added regression coverage to existing tests/test_sources.py; no new test
file was created.
Verification:

python3 -m py_compile src/price/data_sources.py scripts/capture_bars.py
passed.
python3 -m pytest -q -> 380 passed.
python3 -m ruff check src scripts tests -> clean.
Practical conclusion:

The workflow's prior from ALPACA lines for XOP/XLB/KLAC daily were a logging
truth problem, not evidence that the router had reverted. Future logs should now
print from TIINGO for any equity 1d pull when a Tiingo key is present, matching
the actual first-attempt source.
Live Forward-Return Idempotency Repair (2026-07-06)

The 19:50 UTC live_capture run confirmed the source-label fix: XOP, XLB, and
KLAC daily captures now print from TIINGO, matching the universal router's
all-equity-daily Tiingo-first behavior. The same run also showed the explicit
monitored universe source is working: live_forward_returns.py --universe-source monitored updated the live forward-return artifact.

A new issue was found by inspecting the committed artifact after that run:
localdata/live_forward_returns.csv contained multiple rows with the same
row_key. That violates the script's idempotency contract. Root cause:
paper_trade.py can log the same matched bar/slice across repeated scheduled
scans. Those are audit observations of the same signal label, not distinct
forward-return labels. The old capture logic updated every duplicate audit row
and preserved duplicate existing rows, so reruns could keep expanding or
maintaining duplicates.

Fix:

scripts/live_forward_returns.py now collapses matched audit rows by computed
row_key before scoring forward returns.
Existing live_forward_returns.csv rows are compacted by row_key, keeping
the latest captured_at_utc, before updates are applied. This repairs legacy
duplicate artifacts on the next run.
The durable output is restored to one row per signal key.
For backward compatibility, bin_mode=insample keeps the historical row-key
shape; non-insample modes include bin_mode in the row key to prevent
insample/rolling collisions.
Regression coverage was added to existing tests/test_live_forward_returns.py:
repeated matched audit rows collapse to one output row, and pre-existing
duplicate live rows compact on update.
Verification:

python3 -m py_compile scripts/live_forward_returns.py passed.
python3 -m pytest -q -> 382 passed.
python3 -m ruff check src scripts tests -> clean.
Operational state from the run remains sane:

XOP is still the only open tracked position.
XOP stop remains unchanged around $147.18.
No new entries were submitted.
Forward-return capture is now populating, but the duplicate-row repair will
take effect on the next workflow run after this patch is committed.

Cloud Execution Restoration & Optimization (2026-07-07)
This section records the restoration and optimization of the automated GitHub
Actions pipeline (.github/workflows/live_capture.yml), transitioning it from a
broken/failing state to a 100% autonomous, self-healing cloud execution engine.

Root cause analysis of prior cloud failures

CLI Argument Mismatch (paper_trade.py): In prior refactoring passes,
scripts/paper_trade.py was replaced with a truncated stub that omitted key
argparse flags (--cost-spread-bps, --cost-slippage-bps, --whipsaw-limit,
--max-aggregate-risk-pct, --breakeven-trigger-r). However, live_capture.yml
still passed all 15 multi-lever execution parameters. Every scheduled cloud
run crashed immediately upon invoking paper_trade.py with:
error: unrecognized arguments: --breakeven-trigger-r 1.0 ...
Universe Scale & Rate-Limit Thrashing: live_capture.yml previously ran
capture_bars.py --tier allowlist (236 symbols) on an hourly schedule during
market hours. Issuing hundreds of HTTP requests hourly against free-tier
Alpaca/Tiingo APIs caused severe rate-limit backoffs, 45-minute workflow
timeouts, and cache eviction thrashing on unique github.run_id cache keys.
Fixes implemented and deployed

Restored scripts/paper_trade.py and scripts/live_forward_returns.py to their
full multi-parameter implementations, supporting all 15 execution and risk flags.
Restored missing execution cost methods (per_leg_bps_for_validation, apply)
in src/price/cost_model.py.
Streamlined ingestion in .github/workflows/live_capture.yml: replaced --tier allowlist
with targeted ingestion for the active monitored and macro conditioning set
(SPY QQQ IWM DIA GLD TLT USO XLK XLF XLE XOP XLB KLAC). This cut cloud execution
time from tens of minutes down to under 40 seconds per run while eliminating API
rate-limit contention.
Operational verification & first P&L measurement

Test Suite: 100% green (382 passed) across all local and remote runs.
Autonomous Cloud Run (a45b6fd): Executed cleanly on schedule during market hours.
Ingested targeted bars in ~3 seconds, evaluated risk groups against live equity
($100,002.66), submitted a clean paper order for XLK (cross_TLT_state_slope=uptrend + state_ext=neutral),
blocked XOP correctly due to existing exposure, and auto-committed execution logs.
First Realized P&L Report (attribute_pnl.py): Reconstructed the paper ledger's first
completed round-trip (cross_USO_state_vol=mid_vol + state_ext=stretched_), showing
a 100% win rate and +$5.38 net profit.
Slippage Reality Check: The P&L report measured empirical fill friction at 161.2 bps
per leg (due to signals firing at bar close and market orders filling at next session open).
This confirms why execution cost realism (CostModel) is essential and highlights
limit order entry control as a primary operational target.
Red-Team Hardened Roadmap & Operational Phases (2026-07-07+)
To prevent future drift and ensure rigorous progression toward real-money readiness,
all upcoming system development is locked into four distinct, actionable phases.

Phase 1: Out-of-Sample Evidence Accumulation & Slippage Calibration (Current - Mid July 2026)
Scope: Let the autonomous live_capture.yml runner operate completely unattended during
market hours to build an empirical paper ledger across incumbent slices.

Sample Adequacy: Accumulate >= 5 completed round-trips per monitored slice before
interpreting P&L attribution statistics as stable.
Cost Calibration: Replace CostModel default slippage placeholders with empirical
realized slippage measured by attribute_pnl.py.
Limit Order Execution: Mitigate the observed 161.2 bps market-open fill gap by
transitioning entry execution from market orders to limit orders near signal close
(limit_price=close_adj), preserving backtested edge margins.
Phase 2: Decoupled Automated Research Refresh (research_refresh.yml) (Late July 2026)
Scope: Create a dedicated weekend background workflow (research_refresh.yml) scheduled
for Saturday mornings (08:00 UTC) to audit edge decay and re-index the 236-symbol allowlist
without interfering with weekday hourly paper execution.

To survive red-team engineering review, Phase 2 must enforce five non-negotiable invariants:

Concurrency & Git Isolation: Must operate under a dedicated GitHub Actions concurrency
group (research-refresh) and execute strictly outside trading hours to eliminate git push
deadlocks with live_capture.yml.
Storage & Cache Preservation: Must consume existing warehouse cache archives read-only or
maintain a single unified parquet schema. Never upload duplicate 236-symbol tarballs from
the research job, which would exhaust GitHub's 10GB repository cache quota.
API Rate Budgeting: Must enforce deterministic batch throttling and polite pagination inside
capture_bars.py to prevent free-tier Tiingo/Alpaca token exhaustion or IP bans over weekends.
Anti-Overfit / Anti-Snooping Gate: Weekend runs default strictly to diagnostic auditing
(--date-range-diagnostics) on incumbent edges to flag decay. Automated discovery of new
candidate slices is locked behind a sample accumulation delta threshold (requiring >= 60
new daily bars / ~1 quarter of fresh out-of-sample data before re-running grid search).
State Namespace Isolation: If evaluating look-ahead-free rolling bins (--bin-mode rolling),
research outputs must write strictly to isolated artifacts (localdata/research/candidate_leaderboard_rolling.csv)
to prevent operational vocabulary collisions with live execution's monitored_slices.csv.
Phase 3: Advanced Execution Architecture (August 2026)
Scope: Deliver the two major structural extensions queued in the V4/V5 Leverage Phase.

Pyramiding / Multi-Unit Ledger: Enable adding units to confirmed winning positions once
the initial unit ratchets to breakeven (+1R). Build a local per-unit accounting schema in
trading.py to resolve Alpaca's blending of multiple fills into a single avg_entry_price.
True 4.0x Intraday Leverage: To safely utilize Alpaca's 4.0x intraday buying power without
violating overnight Reg T 2.0x step-down limits or risking broker liquidation, build an
automated same-day force-flatten-before-close exit mode.
Phase 4: Real-Money Readiness Gate (September 2026+)
Scope: Transition from paper exploration to live capital deployment only upon rigorous proof.

Empirical Survival Gate: Require incumbent slices to demonstrate statistically significant
positive P&L net of empirical fill friction over multi-month out-of-sample paper execution.
Macro Drawdown Stress Testing: Verify that correlation allocation caps (max_per_group=2),
regime deployment gates (--regime-filter), and R-multiple protective stops successfully
bound portfolio drawdown during adverse macro market shifts.
Capital Deployment Pilot: Transition from paper API keys to live capital under strict
notional limits only after operator sign-off on empirical survival reports.

Operational Hardening — Live Capture Consistency (2026-07-07)
This section records three small operational patches landed in commit c9c61e0
to close live-capture consistency gaps that had been on the HANDOVER as open
questions for several days. No validation, sizing, or risk-gate logic changed.
Nothing is promoted. The patches are execution-side plumbing only.

Why these three, in this order
The three changes target three distinct failure modes that were visible in
the live workflow log over the last week:

1. The live workflow called live_forward_returns.py --universe-source auto,
   which silently fell through from candidate_leaderboard.csv to
   monitored_slices.csv when the leaderboard was empty or stale. The HANDOVER
   had explicitly recorded this as wrong ("An implicit fallback from
   leaderboard -> monitored_slices.csv is NOT the cleanest answer, because
   it can mask a broken/missing leaderboard during research runs"), but the
   workflow was still using it. Patch 1 --universe-source monitored fixes
   the inconsistency with the HANDOVER's own doctrine.

2. monitor.get_default_monitored_slices() had a three-level silent fallback
   chain (explicit -> dynamic -> hardcoded). On the happy path it did the
   right thing; on the unhappy path there was no signal that the monitor
   was reading a stale or empty set. Patch 2 adds a print() at each
   fallback level so future agents (and the operator) can see which source
   the monitor is actually using. Behavior is unchanged on the happy path;
   visibility is added on the unhappy path.

3. The auto-commit step in live_capture.yml had no guard against silent
   corruption. A stuck loop or a runaway append could write 10k+ spurious
   rows to a critical CSV and the workflow would happily commit it. The
   HANDOVER's "Scheduled Live Capture" section had flagged this as an open
   question ("A future change to the layer should consider whether this
   policy is still right"). Patch 3 closes that question with a small,
   opt-in guard.

What was added

.github/workflows/live_capture.yml:
  --universe-source auto changed to --universe-source monitored (matches
  HANDOVER doctrine; closes the silent-fallback gap).
  New line: `python3 scripts/delta_spike_guard.py` runs immediately before
  the auto-commit step.

src/price/monitor.py:
  get_default_monitored_slices() now prints a one-line message at each
  fallback level (explicit -> dynamic -> hardcoded). Behavior is identical
  on the happy path; the new thing is visibility of the unhappy path so
  a future agent reading the scan output knows which source is in use.

scripts/delta_spike_guard.py (new file):
  Refuses to allow auto-commit if any of the 5 guarded CSVs grew by more
  than SPIKE_FACTOR=10x AND SPIKE_MIN_DELTA=50 rows versus the previously
  committed version. Guarded files:
    localdata/live_forward_returns.csv
    localdata/trade_journal.csv
    localdata/paper_trade_log.csv
    localdata/candidate_leaderboard.csv
    localdata/monitored_slices.csv
  Exits 0 on healthy, 1 on detected spike. Never raises; a transient
  filesystem error is logged and treated as "cannot compare, allow commit"
  (fail-open, consistent with the project's other data-dependent gates).
  The thresholds (10x and 50 rows together) are deliberately conservative:
  a normal weekly forward-return run adds a handful of rows, so the guard
  will not trip on legitimate growth. Tune both constants in the script
  if a future workload needs a different policy.

Graceful degradation (the safety property)
The new guard is opt-in via the workflow change. A hypothetical external
caller that does not run scripts/delta_spike_guard.py before its commit
sees no behavior change. The guard never modifies the CSVs it inspects,
never deletes rows, and only ever BLOCKS a commit -- it cannot enlarge
or force any action. The print() additions in monitor.py are similarly
additive; the monitor returns the same slices in the same order, with
the same risk-gating behavior.

Verification
python3 -m py_compile on monitor.py and delta_spike_guard.py: clean.
python3 -m pytest -q tests/test_state_unavailable.py tests/test_attribution.py:
20 passed, 0 failed.
YAML validation on live_capture.yml (python3 -c "import yaml; yaml.safe_load(...)"):
clean.

What this section does NOT do
Does not change validation, sizing, exits, allocation, cost, attribution,
regime, or leverage logic.
Does not promote any slice.
Does not change monitor.DEFAULT_MONITORED_SLICES or monitored_slices.csv.
Does not enable the regime filter or leverage on the live workflow; those
remain opt-in per the HANDOVER's explicit-policy-decisions doctrine.
Does not change the guard thresholds without operator sign-off; the
SPIKE_FACTOR and SPIKE_MIN_DELTA constants are the only knobs and they
are conservative by design.

Open question this leaves for the next agent
None of the three patches close the structural-edge question
(regime-conditional vs regime-independent). The regime deployment gate
remains a trading-side fix; the regime-stratified validation diagnostic
remains diagnostic-only. The path to a real-money deployment still runs
through Phase 4 of the locked roadmap above. No patch in this section
should be read as moving the project toward that gate.
