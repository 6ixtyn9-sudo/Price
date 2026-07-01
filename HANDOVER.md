Price — Handover  
Date: 2026-06-30  
Purpose: clean-slate price-first research lab. Focus is on understanding market price behavior before choosing sides or building execution logic.

Single source of truth  
This file is the handover and should be updated in place. Do not create drifting reports or extra planning documents unless explicitly asked.

Current decision  
The previous sports-heavy path and the legacy crypto system were intentionally set aside.

Reasons:
- sports repos (Edge/Racket) remain useful background monitors but share failure-mode risk if over-copied into new systems  
- STST-Trading-System has real substance but too much hybrid architecture drag (Apps Script + Python + Supabase + historical off-repo notebook truth)  
- the new priority is a cleaner substrate with abundant structured price data and less operational baggage  

Chosen direction  
Start a brand new repo called Price.

Current target substrate:
- US ETFs / indices first  
- structured API data first  
- bar-based price research first  
- Python-first, local-first, lean architecture  
- auto-discovery of market-state slices  
- no broker integration in v1  
- no options in v1  
- no dashboards / governance / council layers in v1  

Core philosophy  
The foundation is to understand the price before choosing sides.

This repo should not begin with:
- hand-authored strategy bundles  
- hype claims about bots printing money  
- immediate signal generation  
- execution wiring  
- options-chain complexity  

This repo should begin with:
- clean OHLCV ingestion  
- a reproducible local warehouse  
- descriptive market-state features  
- 3D–5D slice discovery  
- honest train/valid/walk-forward validation  

Step 1 — substrate  
Initial focus is a small, clean US ETF/index universe.

Recommended initial symbols:
- SPY  
- QQQ  
- IWM  
- DIA  
- GLD  
- TLT  
- USO  
- XLK  
- XLF  
- XLE  

Recommended initial timeframes:
- 15m  
- 1h  
- 1d  

Why this substrate:
- abundant data  
- no thin-slate problem  
- cleaner than sports market/result matching  
- less baggage than reviving STST  
- broad enough to test mean reversion / momentum / trend behavior without hardcoding them  

Step 2 — data source doctrine  
Use structured API data first.

Rationale:
- unlike sports, stocks/ETFs do not require bookmaker-style scraping to establish the first research substrate  
- structured API bars are cleaner and easier to validate  
- scraping can be considered later only if the chosen data source proves insufficient  

Current intended data stack:
- API-first historical and current OHLCV bars  
- local flat-file / parquet / DuckDB warehouse  
- no Supabase in v1  
- no broker platform dependence in v1

**Operational Requirements:**
- **Rate Limiting:** The ingestion client (`data_sources.py`) must implement polite rate-limiting and exponential backoff (especially for Alpaca's 200/min limit).
- **Progress Caching:** Fetching must be chunk-based (e.g., 30-90 day increments) and appended incrementally to Parquet to prevent full restarts on failure.
- **DuckDB Read-Through:** DuckDB queries should scan Parquet directories directly to maintain a stateless warehouse layer.  

Important:  
Do not rush into options, brokers, or automation before the base price substrate proves itself.

Step 3 — research grain  
The atomic research row is not a signal, pick, or order.  
It is:
- one symbol × one bar timestamp × one forward evaluation window  

Every row should eventually support:
- symbol  
- timeframe  
- bar timestamp  
- OHLCV inputs  
- derived features describing price state  
- forward return windows  
- excursion / drawdown labels  
- slice eligibility fields  

Step 4 — discovery doctrine  
Do not start with hard-coded strategy rules.

Instead:
- compute descriptive features  
- let the system generate candidate slices  
- test which slices show stable forward behavior  

Example feature families:
- price extension vs moving average  
- ATR-normalized extension  
- recent 1/3/5/10 bar return bands  
- realized volatility bands  
- trend slope bands  
- session bucket  
- day-of-week / month  
- ETF class / sector family  

Example candidate slices:
- index ETF + opening hour + stretched down + low trend slope  
- commodity ETF + 1h + high trend slope + moderate volatility  
- tech ETF + daily + pullback-in-trend regime  

Important principle:  
The code should encode normalization, feature extraction, slice generation, and validation discipline — not subjective edge stories.

Step 5 — validation doctrine  
Every discovered slice must survive:
- train / valid separation  
- minimum sample thresholds  
- cost assumptions  
- overlap awareness  
- regime sanity  
- eventually walk-forward survival  

Do not promote slices based on:
- tiny samples  
- a good-looking week  
- social-media anecdotes  
- one chart or one strategy story  

Why not options first  
Options are explicitly deferred.

Reason:  
options add too much complexity too early:
- chain data  
- expiration handling  
- strike selection  
- greeks  
- IV surface issues  
- liquidity/spread filters  
- execution assumptions  

Correct path:
- prove price-behavior edges on the underlying  
- only then explore options as an execution wrapper  

Why not FX first  
FX is not rejected, but not first.

Reason:
- fewer instruments  
- less cross-sectional richness than equities/ETFs  
- macro interpretation burden earlier in the build  

Why not sports first  
Sports is intentionally not the next main build.

Reason:
- sparse/event-driven slate problems  
- more result/odds matching complexity  
- too much shared failure-mode risk with Edge/Racket patterns  

Why not revive STST now  
STST was inspected in workspace.  
High-level conclusion:
- real system, not fake  
- but hybrid and heavy  
- Apps Script + Python + Supabase + historical off-repo research truth  
- too much architecture drag for a fresh rapid discovery sprint  

Practical conclusion:  
Do not revive STST now. Treat it as a later salvage candidate, not the current foundation.

Current workspace state  
Old repos were intentionally removed from workspace:
- Edge-Factory  
- Racket-Factory  
- STST-Trading-System  

Workspace contains only:
- Price/  

Immediate next actions for the next agent  
Do these in order:
1. Finalize data-source shortlist  
   - primary source, fallback source, output contract  
2. Design the exact row schema  
   - raw OHLCV, derived feature placeholders, forward-return labels, metadata fields  
3. Scaffold the repo  
   - minimal code structure  
4. Build only ingestion + warehouse first  

Non-goals for v1  
Do not add these early:
- broker integration  
- live trading  
- options-chain support  
- dashboards  
- councils/governance layers  
- sprawling cloud persistence  
- prompt systems  
- strategy-story marketing  

Agent workflow / preferred collaboration style  
The preferred maintenance workflow is the practical small-patch workflow.

Working style:
- Keep changes minimal, safe, and copy-pasteable.  
- Prefer small targeted patches over broad rewrites.  
- Do not create new helper scripts, validators, reports, or docs unless explicitly asked.  
- Do not create placeholder files, placeholder tests, or fake scaffold content just to make the repo look complete.  
- Use temporary shell one-liners for diagnostics instead of committing one-off tooling.  
- Explain what each patch is expected to fix before asking the operator to run it.  
- Do not run broad ingestion pulls, expensive API harvests, or load large ignored localdata from the agent environment unless the operator explicitly agrees.  
- The operator runs local commands; the agent reads pasted terminal output and provides the next safe step.  
- Never print or request secrets. If secrets appear in chat, tell the operator to revoke them and move keys to ignored .env files.  

Patch workflow:
- Inspect the relevant source narrowly.  
- Provide an exact bash block the operator can paste.  
- Include a syntax check: `python3 -m py_compile <changed_python_file>`  
- Include a narrow sanity test that does not burn API quota.  
- Review the operator's pasted output before suggesting commit/push.  
- Only commit after: syntax check passes, targeted sanity check passes, diff is reviewed, and no unrelated files are included.  
- Use clear, small commit messages describing the actual fix.  

V1 to V4 build boundaries  
This section exists to stop future agents from drifting into unnecessary complexity too early.

V1 — data source shortlist + canonical schema  
Scope:
- finalize the API-first market-data shortlist  
- choose the initial primary and fallback source  
- define the canonical bar-state row schema  
- confirm the initial universe and timeframes  

Allowed:
- source comparison  
- schema design  
- local sample inspection  
- narrow docs updates in this handover / README if needed  

Not allowed:
- options-chain work  
- broker/execution integration  
- live trading logic  
- strategy promotion claims  
- broad architecture expansion  

V2 — minimal scaffold + ingestion  
Scope:
- create the minimal code structure only once the schema is agreed  
- implement bar capture for a tiny symbol set  
- normalize symbols, timestamps, and timeframes  
- write reproducible local warehouse outputs  

Allowed:
- `src/price/*` minimal modules  
- `scripts/capture_bars.py`  
- `scripts/build_warehouse.py`  
- tiny-sample ingestion checks  
- narrow schema verification  

Not allowed:
- options support  
- broker APIs  
- execution runners  
- cloud persistence sprawl  
- dashboards  
- governance/council systems  
- broad multi-provider complexity before one clean path works  

V3 — feature state + slice discovery  
Scope:
- compute descriptive price-state features  
- generate candidate 3D–5D slices  
- measure forward-return behavior on historical bars  
- separate raw coverage, feature coverage, and discovered slices  

Allowed:
- feature engineering tied to price state  
- discovery logic  
- sample-size floors  
- small, reproducible research outputs  

Not allowed:
- hand-authored “hero strategy” bundles  
- flash strategy marketing  
- social-media style profit claims  
- broker/execution work  
- options overlays  
- sprawling orchestration layers  

V4 — validation discipline  
Scope:
- train/valid separation  
- cost-aware evaluation  
- overlap awareness  
- regime sanity  
- eventual walk-forward validation  
- promotion discipline for only the most stable slices  

Allowed:
- validation logic  
- cost assumptions  
- walk-forward planning  
- rejection of weak or unstable slices  

Not allowed:
- live deployment  
- broker wiring  
- options execution design  
- “AI bot prints money” narratives  
- premature portfolio automation  
- another bloated hybrid architecture like STST  

Anti-drift rules  
Future agents must not bypass the sequence. Do not jump from V1/V2 straight into options, broker/execution work, flashy strategy claims, complex governance layers, cloud-memory sprawl, or multi-runtime hybrid architecture.

V1 Decision Record (2026-07-01)  
This section locks the concrete decisions required before any V2 scaffolding/ingestion code is written.

1. Data source: primary + fallback  
- **Primary source for intraday (15m, 1h):** Alpaca Market Data API (alpaca-py SDK), IEX feed on the free tier. Zero cost, adequate history, corporate actions endpoint.  
- **Primary source for daily (1d):** Tiingo API. Zero cost, consolidated volume, deep history (30+ years back to 1990s), correct adjusted-close reconstruction.
- Both are free-tier usable, so no budget approval is required to start.  

Open item requiring operator action before ingestion code is written:
- Register free API keys for Alpaca and Tiingo, store them in a local .env (already gitignored) — never paste keys into chat.  
- Run one narrow manual pull (e.g. SPY, 1d, last 30 days) from each and diff adjusted closes, to confirm the free-tier IEX feed is acceptable.

2. Canonical bar-state row schema (v1 draft)  
One row = one symbol × one timeframe × one bar timestamp. Forward-return/label columns are computed relative to that bar.

Identity / metadata:
- `symbol` (normalized, uppercase)  
- `timeframe` (15m | 1h | 1d)  
- `bar_ts_utc` (bar open timestamp, UTC, tz-aware)  
- `source` (alpaca | tiingo)  
- `ingested_at_utc` (ingestion timestamp)  

Raw OHLCV:
- `open_raw`, `high_raw`, `low_raw`, `close_raw`, `volume_raw`  

Adjusted OHLCV & reconstruction:
- `open_adj`, `high_adj`, `low_adj`, `close_adj`  
- `adj_factor` (cumulative multiplicative factor applied to raw close to get close_adj)  
- `split_factor` (default 1.0), `dividend_cash` (default 0.0)  

Descriptive features (computed later in V3):
- `feat_ext_vs_ma_*`, `feat_atr_norm_ext`, `feat_ret_1/3/5/10`, `feat_realized_vol_*`, `feat_trend_slope_*`, `feat_session_bucket`, `feat_dow`, `feat_month`, `feat_sector_family`  

Forward evaluation placeholders (computed later in V3):
- `fwd_ret_*` (per horizon), `fwd_mfe`, `fwd_mae`, `label_eligible`  

3. Time & timezone convention (hard rule)  
- All bar timestamps are stored as UTC, timezone-aware, no naive datetimes.  
- A bar's timestamp is its open time (bar [t, t+timeframe)).  
- **1d bars must be fetched directly from Tiingo (primary) or Alpaca (secondary)** to ensure deep historical reach (1990s vs 2016 cap) and consolidated market volume. 1h bars remain resampled from 15m bars.  
- **Feature Invariant:** In `features.py`, any timezone-aware UTC timestamp must be localized to `America/New_York` BEFORE extracting time/session-based features (e.g., hour of day, session bucket) to avoid DST drift.  

4. Look-ahead bias invariant (hard rule)  
- A feature computed "as of" bar T may only use information available at or before the close of bar T.  
- A forward label for bar T may only reference bar T+1 or later.  
- This must be an enforced invariant with a test.  

5. Corporate actions & data revision policy  
- Corporate actions (splits, cash dividends) are stored explicitly per bar.  
- **Intraday Adjustment Propagation:** Because intraday bars (15m, 1h) are often raw/unadjusted, the daily cumulative adjustment factor (`adj_factor = close_adj / close_raw`) from the daily bars must be propagated backward to all intraday bars for that given day during the warehouse build. This ensures consistency and prevents artificial price gaps in features.  
- Ingestion write path must be append + explicit overwrite, never silent in-place mutation.  

6. Gaps & market calendar handling  
- Use `pandas_market_calendars` (XNYS calendar) as the single source of truth for expected trading sessions.  
- A missing bar during an expected session is logged as a gap.  

7. Local warehouse storage format  
- Parquet files, partitioned by `symbol/timeframe/`, are the durable on-disk artifact.  
- DuckDB is used to query/join across partitions.  

8. Reproducibility & testing baseline  
- `pyproject.toml` dependencies must be pinned once ingestion code lands.  
- Tests must use small deterministic synthetic OHLCV fixtures.  

9. Licensing / ToS note  
- Personal, non-redistributed research use only.  

10. V1 "Definition of Done" checklist  
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
(`src/price/validation.py`, `scripts/validate_slices.py`) against the
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
- QQQ 1h "afternoon reversal" (`state_session=afternoon + state_ext=stretched_down
  [+ state_slope=downtrend]`): originally reported at n=31, 80.65% win rate,
  +0.814% mean 5h return. On ~3x more history the slice recurs at much
  larger sample sizes (n=84-92 in-training) but the training-window
  Newey-West test itself is not significant, despite a consistent positive
  sign. Verdict: rejected -- the original reading was a small-sample
  artifact, not a stable edge.
- SPY 1d "breakout" (`state_ext=stretched_up + state_vol=high_vol`):
  originally reported at n=24, 75.00% win rate, +0.880% mean 5d return. On
  the larger dataset this exact combination no longer ranks among the top
  discovered slices at all (`state_ext=stretched_down + state_vol=high_vol`
  -- the opposite extension direction -- ranks highest instead for both
  SPY and QQQ daily), and neither variant clears train+valid significance
  when tested directly. Verdict: rejected.

Two new slices survived full V4 discipline (train + valid + cost +
significance + sample floor), both on SPY 1h:
- `state_session=afternoon + state_ext=neutral + state_slope=downtrend`:
  train n=462 (+0.437% cost-adj, t=5.22), valid n=163 (+0.155% cost-adj,
  t=2.15, p=0.031), walk-forward survival 3/4 folds (75%).
- `state_session=lunch + state_ext=neutral + state_slope=downtrend`:
  train n=279 (+0.457% cost-adj, t=4.86), valid n=114 (+0.155% cost-adj,
  t=2.08, p=0.037), walk-forward survival 2/4 folds (50%).

These are smaller in magnitude than the retracted V3 numbers (~0.15-0.16%
per 5-bar window after cost, vs. the original ~0.8%+ readings), which is
expected: genuine, survivable edges are almost always smaller than what a
small-sample discovery pass first suggests. Notably both survivors involve
`state_ext=neutral` (not a stretched extension) combined with a downtrend
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

Operational note: `scripts/discover_slices.py` writes to a single fixed
output path (`localdata/discovered_slices.csv`) and overwrites it on every
invocation. Running discovery once per timeframe (e.g. 1h, then 1d) without
using the `--append` flag will silently discard the earlier timeframe's
results before validation ever sees them. Use `--append` when discovering
across more than one timeframe or symbol set in the same research session.

Practical conclusion: do not promote or reference the original V3 QQQ
afternoon-reversal or SPY daily-breakout numbers going forward -- they did
not survive validation. The two SPY 1h `state_ext=neutral + downtrend`
slices above are the first slices in this project to carry a genuine V4
validation stamp, and are the current state of the art pending further
walk-forward robustness work and additional history/symbols.


V4 Parent-Baseline Update (2026-07-01)
After adding unconditional baseline and parent-regime baseline diagnostics to
`scripts/validate_slices.py`, the prior two SPY 1h 3D survivors were found to
be over-specified. They still pass the original train+valid+cost+significance
gate and beat the unconditional SPY 1h baseline, but they do not beat their
strongest simpler parent regimes in validation.

The important discovery improvement was adding the missing intraday 2D
combination:
- `state_session + state_slope`

This exposed cleaner 2D candidates:

1. SPY 1h `state_session=afternoon + state_slope=downtrend`
- train n=542, cost-adjusted mean +0.4014%, Newey-West t=4.98
- valid n=174, cost-adjusted mean +0.1763%, Newey-West p=0.0148
- valid unconditional baseline +0.0255%
- valid excess vs unconditional baseline +0.1508%
- strongest valid parent: `state_slope=downtrend`, +0.1294%
- valid excess vs best parent +0.0470%
- walk-forward survival 3/4 folds (75%)

2. SPY 1h `state_session=lunch + state_slope=downtrend`
- train n=345, cost-adjusted mean +0.4179%, Newey-West t=5.11
- valid n=119, cost-adjusted mean +0.1627%, Newey-West p=0.0210
- valid unconditional baseline +0.0255%
- valid excess vs unconditional baseline +0.1372%
- strongest valid parent: `state_slope=downtrend`, +0.1294%
- valid excess vs best parent +0.0334%
- walk-forward survival 2/4 folds (50%)

3. QQQ 1h `state_session=lunch + state_slope=downtrend`
- train n=368, cost-adjusted mean +0.1630%, Newey-West t=2.01
- valid n=136, cost-adjusted mean +0.2281%, Newey-West p=0.0119
- valid unconditional baseline +0.0426%
- valid excess vs unconditional baseline +0.1854%
- strongest valid parent: `state_slope=downtrend`, +0.1275%
- valid excess vs best parent +0.1005%
- walk-forward survival 1/4 folds (25%)

Practical conclusion:
- Do not promote the old 3D `state_ext=neutral + state_slope=downtrend`
  slices as the cleanest current finding.
- The cleaner current state of the art is the 2D intraday
  `state_session + state_slope=downtrend` structure, especially SPY afternoon.
- QQQ lunch is interesting but less stable because walk-forward survival is
  only 25%.
- Future promotion should require positive excess vs both the unconditional
  symbol/timeframe baseline and the strongest simpler parent regime.


V4 Robustness Caveat (2026-07-01)
A targeted scenario check was run on the three cleaner 2D intraday candidates:
- SPY 1h `state_session=afternoon + state_slope=downtrend`
- SPY 1h `state_session=lunch + state_slope=downtrend`
- QQQ 1h `state_session=lunch + state_slope=downtrend`

Scenarios tested:
- default validation settings
- `--cost-bps 2` (4 bps round trip under the current cost model)
- `--cost-bps 5` (10 bps round trip)
- `--split 0.6`
- `--split 0.8`

Result:
- SPY afternoon + downtrend survived default, cost2, and split06, but failed
  cost5 and split08. It stayed positive and beat both the unconditional and
  best-parent baselines across the scenarios, but significance was not stable
  under the stricter split/cost settings.
- SPY lunch + downtrend showed a similar but weaker profile, with lower
  walk-forward survival.
- QQQ lunch + downtrend had strong validation-period mean returns but weak
  walk-forward survival and was rejected in most robustness scenarios.

Practical conclusion:
- The leading current research candidate is SPY 1h
  `state_session=afternoon + state_slope=downtrend`.
- It is promising, not promoted.
- Future agents should not describe it as a validated tradable edge yet.
- Next validation work should focus on rolling/anchored walk-forward,
  date-range sensitivity, and more explicit robustness tables rather than
  expanding the discovery grid.


V4 Anchored Walk-Forward Diagnostics (2026-07-01)
Added `scripts/validate_slices.py --walk-forward-diagnostics`, which writes
`localdata/walk_forward_diagnostics.csv` and prints fold-level validation
diagnostics for the current leading 2D candidates.

Default 4-fold anchored diagnostics:

SPY 1h `state_session=afternoon + state_slope=downtrend`
- fold 0: valid mean +0.4268%, p=0.000839, pass
- fold 1: valid mean +0.3467%, p=0.027353, pass
- fold 2: valid mean +0.2150%, p=0.016456, pass
- fold 3: valid mean +0.1358%, p=0.148389, fail
Interpretation: positive in every fold and beats both unconditional and
best-parent baselines in every fold, but the effect decays over time and the
latest fold is not statistically significant.

SPY 1h `state_session=lunch + state_slope=downtrend`
- fold 0: pass
- fold 1: fail
- fold 2: pass
- fold 3: fail
Interpretation: positive but unstable; secondary candidate only.

QQQ 1h `state_session=lunch + state_slope=downtrend`
- fold 0: fail
- fold 1: fail
- fold 2: pass
- fold 3: fail
Interpretation: unstable despite attractive full-period validation means; do
not promote.

Practical conclusion:
- The best current candidate remains SPY 1h
  `state_session=afternoon + state_slope=downtrend`.
- It should be described as promising but recently weaker/decaying, not as a
  validated tradable edge.
- Next work should investigate date-range/regime sensitivity and whether the
  latest-fold weakening is due to market regime, data quirks, or decay.


V4 Date-Range Sensitivity Diagnostics (2026-07-01)
Added `scripts/validate_slices.py --date-range-diagnostics`, which writes
`localdata/date_range_diagnostics.csv` and prints targeted date-window
diagnostics for the current leading 2D candidates.

Windows checked:
- all available data
- calendar 2024
- calendar 2025
- calendar 2026 YTD
- latest 12 months
- latest 6 months

SPY 1h `state_session=afternoon + state_slope=downtrend`
- all: valid mean +0.3467%, p=4.47e-08, pass
- 2024: valid mean +0.4972%, p=1.05e-06, pass
- 2025: valid mean +0.1775%, p=0.1155, fail
- 2026 YTD: valid mean +0.1554%, p=0.1472, fail
- latest 12m: valid mean +0.1533%, p=0.0226, pass
- latest 6m: valid mean +0.1554%, p=0.1472, fail

Interpretation:
- The effect remains positive and continues to beat both unconditional and
  best-parent baselines across the checked windows.
- Statistical strength is concentrated in 2024.
- 2025 and 2026 YTD are positive but individually not significant.
- Latest 12m passes, but latest 6m fails.
- This supports the prior anchored-walk-forward conclusion: the candidate is
  not dead, but it is materially weaker/recently decayed.

SPY 1h `state_session=lunch + state_slope=downtrend`
- Similar but weaker profile: strong in 2024, weaker/non-significant in 2025
  and 2026 YTD, latest 12m passes, latest 6m fails.
- Remains secondary to SPY afternoon.

QQQ 1h `state_session=lunch + state_slope=downtrend`
- All-period and latest-12m windows pass, but individual calendar windows and
  latest 6m do not.
- Remains interesting but unstable; do not promote.

Practical conclusion:
- The leading candidate remains SPY 1h
  `state_session=afternoon + state_slope=downtrend`.
- It should now be described as positive across windows but statistically
  strongest in 2024 and weaker recently.
- Do not promote it as a tradable edge.
- Next work should inspect regime context for the 2024 strength versus the
  2025/2026 weakening before any further discovery-grid expansion.
