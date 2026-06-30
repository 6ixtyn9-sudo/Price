# Price

A lean, price-first research lab for US ETFs, indices, and later liquid stocks.

## Purpose
The goal is to understand price before choosing sides.

This repo is for:
- clean OHLCV ingestion
- reproducible local warehousing
- descriptive market-state feature generation
- automatic 3D–5D slice discovery
- honest train/valid/walk-forward validation

This repo is not for:
- hype claims
- instant live trading
- broker wiring in v1
- options-chain complexity in v1
- hand-authored strategy bundles masquerading as discovery

## Initial scope
Initial substrate:
- US ETFs / indices first

Initial symbols:
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

Initial timeframes:
- 15m
- 1h
- 1d

## Core doctrine
- price first, side second
- API-first structured market data
- local-first storage
- descriptive features before strategy stories
- discovery before promotion
- validation before execution

## Planned sequence
1. choose clean bar-data sources
2. define canonical bar-state schema
3. scaffold minimal repo structure
4. build ingestion + warehouse
5. compute descriptive features
6. auto-discover 3D–5D slices
7. validate honestly
8. only later consider signals, portfolios, or options

## Non-goals for v1
- broker integration
- live trading
- options support
- dashboards
- cloud persistence sprawl
- governance/council layers
