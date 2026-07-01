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
Important:
Do not rush into options, brokers, or automation before the base price substrate proves itself.

Step 3 — research grain
The atomic research row is not a match, pick, or signal.
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
Workspace now contains only:

Price/
Files currently present
README.md
PLAN.md
NEXT_STEPS.md
HANDOVER.md
These are starter docs only. The repo has not yet been scaffolded into code.

Immediate next actions for the next agent
Do these in order:

Finalize data-source shortlist
Produce a practical shortlist of API-first market-data options for:
US ETFs / indices
15m, 1h, 1d bars
manageable free-tier or multi-key rotation usage
Need to decide:

primary source
fallback source
output contract
2. Design the exact row schema
Define the v1 canonical bar-state schema for one row = one symbol × timestamp × timeframe × forward window.

Need to include:

raw OHLCV
derived feature placeholders
forward-return labels
metadata fields
3. Scaffold the repo
After schema is agreed, create the minimal code structure.
Recommended lean structure:

text

Price/
.gitignore
README.md
HANDOVER.md
requirements.txt
pyproject.toml
src/price/
init.py
config.py
data_sources.py
warehouse.py
features.py
discovery.py
validation.py
util.py
scripts/
capture_bars.py
build_warehouse.py
compute_features.py
discover_slices.py
validate_slices.py
tests/
test_warehouse.py
test_features.py
test_discovery.py
test_validation.py
4. Build only ingestion + warehouse first
Do not jump ahead.
Before any discovery engine work, make sure:

clean bar capture works
warehouse writes reproducibly
timeframes align
symbols are normalized
data gaps are inspectable
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
The preferred maintenance workflow is the same practical workflow that worked well across the earlier repos, but tailored for Price.

Working style
Keep changes minimal, safe, and copy-pasteable.
Prefer small targeted patches over broad rewrites.
Do not create new helper scripts, validators, reports, or docs unless explicitly asked.
Use temporary shell one-liners for diagnostics instead of committing one-off tooling.
Explain what each patch is expected to fix before asking the operator to run it.
Do not run broad ingestion pulls, expensive API harvests, or load large ignored localdata from the agent environment unless the operator explicitly agrees.
The operator runs local commands; the agent reads pasted terminal output and provides the next safe step.
Never print or request secrets. If secrets appear in chat, tell the operator to revoke them and move keys to ignored .env files.
Patch workflow
Inspect the relevant source narrowly.
Provide an exact bash block the operator can paste.
Include a syntax check, usually:
python3 -m py_compile <changed_python_file>
Include a narrow sanity test that does not burn API quota or trigger large historical pulls unless necessary.
Review the operator's pasted output before suggesting commit/push.
Only commit after:
syntax check passes,
targeted sanity check passes,
diff is reviewed,
no unrelated files are included.
Use clear, small commit messages describing the actual fix.
After push, verify GitHub remote.
Remote verification
Prefer checking remote state after each important push.
If a local clone is stale or dirty, verify GitHub directly with:
git ls-remote https://github.com/6ixtyn9-sudo/Price.git refs/heads/main
Confirm the remote SHA matches the operator's pushed commit.
For workflow/config changes, verify remote file contents by inspecting origin/main or the GitHub remote, not only local state.
Sanity-check pattern
For source hygiene, use focused JSON/CSV diagnostics against existing ledgers or warehouse samples.
For ingestion changes, test a tiny symbol set and one timeframe before expanding.
For schema changes, verify expected columns with a narrow local sample instead of rerunning the full build.
For discovery/validation claims, separate:
raw data coverage,
feature coverage,
discovered slices,
validation sample size,
cost-adjusted performance,
walk-forward survival.
Do not judge the system by a single attractive slice or one short run.
Decision rules for Price
Freeze architecture during monitoring windows unless a concrete defect appears.
Patch only for clear issues such as broken ingestion, symbol/timeframe normalization errors, schema drift, duplicated rows, bad forward-label generation, quota burn, or broken report generation.
Do not jump into options, broker integration, or live automation because of one promising discovery pass.
If a feature/discovery claim is not reproducible from committed code and local data, treat it as unproven.
Communication preference
Be direct and practical.
Give the next command to run.
Avoid long speculative rewrites.
Do not repeatedly restate warnings once acted on.
Keep the operator in control of local execution.
Strategic summary
Price should become a lean, Python-first, API-fed price-discovery research lab.

The intended sequence is:

choose clean bar-data sources
define canonical bar-state schema
scaffold minimal repo
build ingestion + warehouse
compute descriptive features
auto-discover 3D–5D slices
validate honestly
only later consider signals, portfolios, or options

Agent workflow / preferred collaboration style
The preferred maintenance workflow is the practical small-patch workflow that allowed earlier repos/systems to be built quickly, but tailored for Price.

Working style
Keep changes minimal, safe, and copy-pasteable.
Prefer small targeted patches over broad rewrites.
Do not create new helper scripts, validators, reports, or docs unless explicitly asked.
Do not create placeholder files, placeholder tests, or fake scaffold content just to make the repo look complete. If a file is needed, either leave it absent until justified or add only meaningful minimal real content.
Use temporary shell one-liners for diagnostics instead of committing one-off tooling.
Explain what each patch is expected to fix before asking the operator to run it.
Do not run broad ingestion pulls, expensive API harvests, or load large ignored localdata from the agent environment unless the operator explicitly agrees.
The operator runs local commands; the agent reads pasted terminal output and provides the next safe step.
Never print or request secrets. If secrets appear in chat, tell the operator to revoke them and move keys to ignored .env files.
Patch workflow
Inspect the relevant source narrowly.
Provide an exact bash block the operator can paste.
Include a syntax check, usually:
python3 -m py_compile <changed_python_file>
Include a narrow sanity test that does not burn API quota or trigger large historical pulls unless necessary.
Review the operator's pasted output before suggesting commit/push.
Only commit after:
syntax check passes,
targeted sanity check passes,
diff is reviewed,
no unrelated files are included.
Use clear, small commit messages describing the actual fix.
After push, verify GitHub remote.
Remote verification
Prefer checking remote state after each important push.
If a local clone is stale or dirty, verify GitHub directly with:
git ls-remote https://github.com/6ixtyn9-sudo/Price.git refs/heads/main
Confirm the remote SHA matches the operator's pushed commit.
For workflow/config changes, verify remote file contents by inspecting origin/main or the GitHub remote, not only local state.
Sanity-check pattern
For source hygiene, use focused JSON/CSV diagnostics against existing ledgers or warehouse samples.
For ingestion changes, test a tiny symbol set and one timeframe before expanding.
For schema changes, verify expected columns with a narrow local sample instead of rerunning the full build.
For discovery/validation claims, separate:
raw data coverage,
feature coverage,
discovered slices,
validation sample size,
cost-adjusted performance,
walk-forward survival.
Do not judge the system by a single attractive slice or one short run.
Decision rules for Price
Freeze architecture during monitoring windows unless a concrete defect appears.
Patch only for clear issues such as broken ingestion, symbol/timeframe normalization errors, schema drift, duplicated rows, bad forward-label generation, quota burn, or broken report generation.
Do not jump into options, broker integration, or live automation because of one promising discovery pass.
If a feature/discovery claim is not reproducible from committed code and local data, treat it as unproven.
Communication preference
Be direct and practical.
Give the next command to run.
Avoid long speculative rewrites.
Do not repeatedly restate warnings once acted on.
Keep the operator in control of local execution.
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
flashy strategy marketing
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
Future agents must not bypass the sequence.

Do not jump from V1/V2 straight into:

options
broker/execution work
flashy strategy claims
complex governance layers
cloud-memory sprawl
multi-runtime hybrid architecture
The correct progression is:

source shortlist
schema
ingestion + warehouse
features
discovery
validation
only much later: signals, portfolio logic, and optional execution research
If a future agent proposes:

options early,
broker/execution work early,
aggressive automation early,
or a hybrid architecture that resembles STST,
the default answer should be no unless the underlying price-discovery substrate is already proven and the operator explicitly wants that expansion.
V1 Decision Record (2026-07-01)
This section locks the concrete decisions required before any V2 scaffolding/ingestion
code is written. It exists because "API-first" and "reproducible warehouse" are not
themselves decisions — they are goals. Below are the actual choices, with rationale,
so a future agent cannot silently assume something different.

1. Data source: primary + fallback
Comparison basis: multi-year intraday history depth, correctness of dividend/split
adjustment, cost, and rate limits — not brand popularity.

Finding worth flagging explicitly: Polygon.io no longer has a free tier (starts at
$99/mo as of the 2025–2026 pricing structure). Any earlier assumption that Polygon is
a free default is stale and should not be acted on without an explicit budget decision.

Provider	Free tier	Intraday depth	Dividend/split correctness	Verdict
Polygon.io	No ($99+/mo)	Excellent	Excellent	Rejected for v1 (cost gate)
Alpaca Market Data	Yes (IEX feed)	Multi-year, not just a rolling window	Corporate-actions endpoint available	Primary
Tiingo	Yes	Intraday since 2016; daily back 50+ yrs	Independently validated correct adjusted-close reconstruction through splits/spinoffs	Fallback / cross-check
Twelve Data	Yes (800 calls/day)	Shallow intraday history	Unverified	Rejected (too shallow)
Alpha Vantage	Nominally yes	~1–2 yrs intraday cap; adjusted daily endpoint now paid-only	Broken on free tier	Rejected (free tier unusable)
Decision:

Primary source: Alpaca Market Data API (alpaca-py SDK), IEX feed on the free
tier. Zero cost, adequate multi-year history for 15m/1h/1d bars, has a corporate
actions endpoint for dividends/splits.
Fallback / cross-validation source: Tiingo, primarily to independently verify
daily adjusted-close reconstruction (splits/dividends) against Alpaca, and as a
backup if Alpaca has coverage gaps for a given symbol/timeframe.
Both are free-tier usable, so no budget approval is required to start.
Open item requiring operator action before ingestion code is written:

Register free API keys for Alpaca and Tiingo, store them in a local .env
(already gitignored) — never paste keys into chat.
Before trusting either source at scale, run one narrow manual pull (e.g. SPY, 1d,
last 30 days) from each and diff adjusted closes, to confirm the free-tier IEX feed
is acceptable for descriptive-feature research (it is expected to be — ETFs are
liquid and heavily traded on IEX too — but this should be confirmed once, not assumed).
2. Canonical bar-state row schema (v1 draft)
One row = one symbol × one timeframe × one bar timestamp. Forward-return/label columns
are computed relative to that bar and are allowed to be null until enough future bars exist.

Identity / metadata:

symbol (normalized, uppercase, no exchange suffix unless disambiguation is required)
timeframe (15m | 1h | 1d)
bar_ts_utc (bar open timestamp, UTC, tz-aware) — see Section 3 for convention
source (alpaca | tiingo) — which provider produced this row
ingested_at_utc — when this row was written to the warehouse (supports revision tracking)
Raw OHLCV (unadjusted, as traded):

open_raw, high_raw, low_raw, close_raw, volume_raw
Adjusted OHLCV (dividend + split adjusted) and reconstruction fields:

open_adj, high_adj, low_adj, close_adj
adj_factor — cumulative multiplicative adjustment factor applied to raw close to
get close_adj, so the adjustment is always reconstructable/auditable
split_factor (default 1.0), dividend_cash (default 0.0) — per-bar corporate action
deltas, only populated on daily bars where applicable
Descriptive feature placeholders (computed later, in V3 — columns reserved now so schema
doesn't churn):

feat_ext_vs_ma_*, feat_atr_norm_ext, feat_ret_1/3/5/10, feat_realized_vol_*,
feat_trend_slope_*, feat_session_bucket, feat_dow, feat_month, feat_sector_family
Forward evaluation / label placeholders (computed later, in V3):

fwd_ret_* (per horizon), fwd_mfe, fwd_mae (excursion), label_eligible (bool —
false near end of available history where forward window can't be computed)
Decision: store both raw and adjusted OHLCV plus an explicit adjustment factor, not
adjusted-only. This is more storage but fully reconstructable, auditable against a second
source, and lets features/labels choose their basis instead of baking one assumption in
at ingestion time.

3. Time & timezone convention (hard rule)
All bar timestamps are stored as UTC, timezone-aware, no naive datetimes.
A bar's timestamp is its open time (bar [t, t+timeframe)), matching Alpaca's
convention. This must not silently vary by source — Tiingo timestamps must be
normalized to the same open-time convention on ingestion.
1h and 1d bars are resampled from 15m bars in the warehouse, not independently
fetched, so all timeframes are guaranteed self-consistent by construction rather than
relying on two API endpoints agreeing.
Display/session-bucket features may convert to America/New_York for human-readable
session logic (open/close/lunch), but the stored timestamp itself is always UTC.
4. Look-ahead bias invariant (hard rule)
A feature computed "as of" bar T may only use information available at or before the
close of bar T.
A forward label for bar T may only reference bar T+1 or later.
This must be an enforced invariant with a test (e.g. shifting features forward by one
bar and asserting no feature column at T is correlated 1:1 with close_raw at T+1
by construction), not just a doctrine statement — to be implemented when features.py
and validation.py are built in V3/V4.
5. Corporate actions & data revision policy
Corporate actions (splits, cash dividends) are stored explicitly per bar (see schema
above), not just folded invisibly into an adjusted close.
Because providers sometimes back-revise historical bars, the warehouse write path
must be append + explicit overwrite, never silent in-place mutation: a re-ingested
bar for a timestamp that already exists should be logged as a revision, not silently
swapped, so "reproducible warehouse" claims stay honest.
6. Gaps & market calendar handling
Use pandas_market_calendars (XNYS calendar) as the single source of truth for
expected trading sessions/holidays/half-days.
A missing bar during an expected session is a gap (inspectable, logged); a missing
bar outside an expected session is expected absence, not a gap. Rolling-window features
must be calendar-aware so they don't silently span a holiday as if it were a normal gap.
7. Local warehouse storage format
Decision: Parquet as the source of truth, DuckDB as the query layer.

Parquet files, partitioned by symbol/timeframe/, are the durable, portable,
git-ignored on-disk artifact (localdata/).
DuckDB is used to query/join across partitions for feature computation, discovery,
and validation — it reads the Parquet files directly rather than owning storage,
so there is one physical source of truth and no sync problem between two stores.
8. Reproducibility & testing baseline
pyproject.toml dependencies must be pinned (not just named) once ingestion code
lands, so "reproducible warehouse" is true at the dependency level, not only the doctrine level.
Tests must use small deterministic synthetic OHLCV fixtures, not live API calls,
so pytest never burns API quota and is safe to run repeatedly/in CI.
A minimal CI workflow (ruff check + pytest on push) should be added once there is
real code to check — not before, to avoid a CI stub with nothing meaningful to run.
9. Licensing / ToS note
Alpaca and Tiingo free-tier terms permit local, personal, non-redistributed research
use of the data. Data must not be redistributed or resold. Re-check each provider's
ToS if this project's use ever moves beyond personal research (e.g. shared publicly).
10. V1 "Definition of Done" checklist
V1 is complete only when all of the following are true:

 Primary + fallback data source chosen and justified (this section)
 Canonical bar-state schema drafted (this section)
 Time/timezone convention fixed
 Look-ahead invariant stated as a hard rule
 Corporate-action and revision policy stated
 Gap/calendar handling approach chosen
 Storage format decided
 Operator has registered Alpaca + Tiingo API keys locally (.env, gitignored)
 One narrow manual pull from each source has been diffed for adjusted-close agreement
 Operator has explicitly signed off on this decision record
Only after the last three boxes are checked should V2 scaffolding (src/price/config.py,
data_sources.py, warehouse.py) begin.