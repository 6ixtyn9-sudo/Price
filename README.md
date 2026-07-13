Price
An autonomous, price-first quantitative research and paper-trading system for US equities, ETFs, and crypto.

What It Does
Ingests daily and intraday OHLCV bars for 236 liquid symbols via yfinance, Tiingo, and Alpaca
Computes 20+ descriptive price-state features per bar
Discovers market-state slices through combinatorial grid search and LightGBM-based ML
Validates every candidate against chronological train/valid split, Newey-West significance, walk-forward survival, parent-baseline excess, search-wide multiple-testing correction, and date-range / regime-stratified diagnostics
Auto-promotes strict survivors to a live paper-trading book with conviction-weighted position sizing, broker-side protective stops, risk-group allocation caps, and a hybrid state-break / horizon exit policy
Measures realized P&L per slice with signal-to-fill slippage calibration
Runs autonomously via GitHub Actions with cron-job.org scheduling
Current State
Component	Status
Universe	236 symbols (221 equities/ETFs + 15 crypto), 1d + 1h + 15m
Monitored slices	22 strict candidates across 18 symbols
Validation gate	n>=15, >=3/4 walk-forward, >=4 scenarios, BH-FDR pass, positive excess vs baseline and parent
Paper trading	Alpaca paper account, 1.0x leverage, limit entries, GTC protective stops
Schedule	Hourly live capture, daily research refresh, weekly 36-shard discovery
Tests	405 passing
Core Modules
Module	Purpose
data_sources.py	Universal bar router (yfinance -> Tiingo -> Alpaca)
warehouse.py	Parquet storage with adjustment factor propagation
features.py	20+ descriptive features per bar
discovery.py	Combinatorial slice discovery + cross-asset conditioning
validation.py	Newey-West, walk-forward, direction-adjusted cost gates
monitor.py	Live state scanner bridging research to execution
trading.py	Alpaca paper-trading execution layer
stops.py / stop_manager.py	R-multiple protective stop state machine + broker reconciliation
position_manager.py	Hybrid state-break / horizon exit policy
sizing.py	Conviction-weighted + volatility-rail position sizing
risk_limits.py	Multi-gate risk guard
regime.py	SMA-50/200 macro regime deployment gate
attribution.py	Per-slice realized P&L with signal-to-fill measurement
cost_model.py	Decomposed execution cost model
Operator Commands
text

cd ~/Price
git pull --ff-only
PYTHONPATH=src python3 scripts/paper_trade.py --dry-run
PYTHONPATH=src python3 scripts/attribute_pnl.py
python3 -m pytest -q
Doctrine
Price first, side second
Discovery before promotion, validation before execution
No slice is promoted without surviving the full V4 gate
Every risk lever fails open on missing data
Paper account only -- no live capital without multi-month out-of-sample survival
Read HANDOVER.md for complete project history and research findings