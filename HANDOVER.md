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

1. Finalize data-source shortlist
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
    __init__.py
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

## Agent workflow / preferred collaboration style

The preferred maintenance workflow is the practical small-patch workflow that allowed earlier repos/systems to be built quickly, but tailored for `Price`.

### Working style
- Keep changes minimal, safe, and copy-pasteable.
- Prefer small targeted patches over broad rewrites.
- Do not create new helper scripts, validators, reports, or docs unless explicitly asked.
- Do not create placeholder files, placeholder tests, or fake scaffold content just to make the repo look complete. If a file is needed, either leave it absent until justified or add only meaningful minimal real content.
- Use temporary shell one-liners for diagnostics instead of committing one-off tooling.
- Explain what each patch is expected to fix before asking the operator to run it.
- Do not run broad ingestion pulls, expensive API harvests, or load large ignored `localdata` from the agent environment unless the operator explicitly agrees.
- The operator runs local commands; the agent reads pasted terminal output and provides the next safe step.
- Never print or request secrets. If secrets appear in chat, tell the operator to revoke them and move keys to ignored `.env` files.

### Patch workflow
1. Inspect the relevant source narrowly.
2. Provide an exact bash block the operator can paste.
3. Include a syntax check, usually:
   `python3 -m py_compile <changed_python_file>`
4. Include a narrow sanity test that does not burn API quota or trigger large historical pulls unless necessary.
5. Review the operator's pasted output before suggesting commit/push.
6. Only commit after:
   - syntax check passes,
   - targeted sanity check passes,
   - diff is reviewed,
   - no unrelated files are included.
7. Use clear, small commit messages describing the actual fix.
8. After push, verify GitHub remote.

### Remote verification
- Prefer checking remote state after each important push.
- If a local clone is stale or dirty, verify GitHub directly with:
  `git ls-remote https://github.com/6ixtyn9-sudo/Price.git refs/heads/main`
- Confirm the remote SHA matches the operator's pushed commit.
- For workflow/config changes, verify remote file contents by inspecting `origin/main` or the GitHub remote, not only local state.

### Sanity-check pattern
- For source hygiene, use focused JSON/CSV diagnostics against existing ledgers or warehouse samples.
- For ingestion changes, test a tiny symbol set and one timeframe before expanding.
- For schema changes, verify expected columns with a narrow local sample instead of rerunning the full build.
- For discovery/validation claims, separate:
  - raw data coverage,
  - feature coverage,
  - discovered slices,
  - validation sample size,
  - cost-adjusted performance,
  - walk-forward survival.
- Do not judge the system by a single attractive slice or one short run.

### Decision rules for Price
- Freeze architecture during monitoring windows unless a concrete defect appears.
- Patch only for clear issues such as broken ingestion, symbol/timeframe normalization errors, schema drift, duplicated rows, bad forward-label generation, quota burn, or broken report generation.
- Do not jump into options, broker integration, or live automation because of one promising discovery pass.
- If a feature/discovery claim is not reproducible from committed code and local data, treat it as unproven.

### Communication preference
- Be direct and practical.
- Give the next command to run.
- Avoid long speculative rewrites.
- Do not repeatedly restate warnings once acted on.
- Keep the operator in control of local execution.
