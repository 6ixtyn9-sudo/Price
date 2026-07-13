Price
Autonomous price-first quantitative research and paper-trading system. US equities, ETFs, and crypto.

What it does
Ingests OHLCV bars for 236 liquid symbols via yfinance, Tiingo, and Alpaca. Computes 20+ descriptive price-state features per bar. Discovers market-state slices through combinatorial grid search and LightGBM. Validates every candidate against chronological train/valid, Newey-West, walk-forward, parent-baseline, search-wide multiple-testing, and regime-stratified diagnostics. Promotes strict survivors to a paper-trading book with conviction-weighted sizing, broker-side protective stops, risk-group caps, and a hybrid exit policy. Runs autonomously on GitHub Actions.

Current state
Everything is dynamic. The monitored slice count, symbol set, and validation metrics change with every discovery cycle. Read localdata/monitored_slices.csv for the live book and localdata/research/merged/candidate_leaderboard_merged.csv for the full research picture. The paper account is Alpaca (1.0x leverage, limit entries, GTC protective stops), scheduled hourly during market hours. 405 tests pass.

Key modules
data_sources.py — universal bar router: yfinance (primary for equity 1d/1h), Tiingo (fallback), Alpaca (15m and crypto). Chunked, rate-limited, with exponential backoff. Equity intraday filtered to regular trading hours.

warehouse.py — Parquet partitions at localdata/warehouse/symbol=X/timeframe=Y/. Vectorized daily adjustment factor propagation onto intraday bars via market-date key alignment.

features.py — 20 features per bar: extension vs MA, ATR-normalized extension, rolling returns, realized vol, vol regime, trend slope, trend strength, session bucket, day-of-week, gap, range position, forward returns, MFE/MAE.

discovery.py — Combinatorial slice discovery with two binning regimes. bin_features() uses full-history quantiles. bin_features_rolling() uses expanding-window quantiles computed strictly from bars before T — the look-ahead-free overfit-kill. Cross-asset conditioning via backward as-of merge. Precomputed frame cache for 12x speedup on large symbol grids.

validation.py — Newey-West HAC with Bartlett kernel and auto-bandwidth. Direction-adjusted returns (negated for shorts, plus borrow drag). Chronological train/valid, walk-forward, parent-baseline, unconditional baseline, search-wide BH-FDR and Bonferroni across the full hypothesis family.

monitor.py — Reads monitored_slices.csv, scans live state from warehouse, matches slices, gates through regime check, conviction-weighted sizing, and the full risk guard. Emits signals; does not place orders. Same-pass double-entry shield.

trading.py — Alpaca paper execution. Limit-order entries at signal close. GTC protective stops. Close-position with pre-close P&L snapshot and cancel-settle polling. Journal reconciliation against broker state.

stops.py / stop_manager.py — R-multiple stop: initial at k_stop × ATR from fill, ratchet to breakeven at +1R, chandelier trail beyond. Broker-side GTC order, continuously enforced. Whipsaw circuit breaker. Autonomous stopout journaling.

position_manager.py — Hybrid exit: stable filter break OR horizon reached, with R-gate winner-hold suppression (trade past +1R is left to the trailing stop, not time-stopped).

sizing.py — Conviction-weighted notional from research edge metrics (magnitude × robustness × validity × multiple-testing bonus). Volatility rail as min() against risk-dollars/ATR. Graceful degradation to equal-notional on missing data.

risk_limits.py — Halt flag, short-side lock, per-symbol dedup, max notional, max open, risk-group allocation cap, daily loss kill switch, aggregate open-risk budget, gross notional cap, margin cushion, whipsaw breaker, cooldown.

regime.py — SMA-50/200 deployment gate. Blocks dip-buying entries during macro bear. Fails open on missing data. Companion attach_regime_labels() for validation-side regime-stratified diagnostics.

leverage.py — Gross notional cap + real-time margin cushion. Both fail open. Overnight hold capped at 2x; 4x requires same-day force-flatten mode not yet built.

attribution.py — FIFO round-trip reconstruction from confirmed fills. Signal-to-fill slippage measurement via exact order_id join. Per-slice P&L with expected-vs-realized comparison. Works gracefully with zero round-trips.

cost_model.py — Decomposed cost: commission + spread + slippage per leg. Round-trip drag nets off edge before conviction sizing. Defaults conservative; designed to be calibrated from realized fills.

Operator commands
The live workflow auto-commits to main every hour. If you have local drift and git pull rejects you, don't think — just run the unstick command:

text

cd ~/Price && git stash && git pull --ff-only && git stash drop
That stashes whatever local noise exists, fast-forwards to match remote, and drops the stash. Works every time. After that:

text

PYTHONPATH=src python3 scripts/paper_trade.py --dry-run   # see what the system would do
PYTHONPATH=src python3 scripts/attribute_pnl.py           # realized P&L per slice
python3 -m pytest -q                                      # run tests
Doctrine
Price first, side second. Discovery before promotion, validation before execution. No slice is promoted without surviving the full gate. Every risk lever fails open on missing data. Paper account only — no live capital without multi-month out-of-sample survival. Read HANDOVER.md for the complete project history.