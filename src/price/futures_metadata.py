"""Canonical continuous-futures metadata.

This module introduces an unambiguous futures namespace for research:

  FUT/ES, FUT/NQ, FUT/RTY, FUT/YM, FUT/CL, FUT/GC, FUT/SI, FUT/ZB,
  FUT/ZN, FUT/NG

Why it exists
-------------
The repo's historical futures exploration used bare roots like CL / ES / BTC,
which collide with equities and crypto-style symbols. The canonical FUT/*
namespace removes that ambiguity before futures ever enter the warehouse,
research registry, or any future execution path.

Current scope
-------------
Research only. The metadata below is sufficient to route data and to begin
thinking about contract-aware sizing later, but execution is NOT enabled.
`execution_ready=False` on every contract by design.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FuturesContract:
    canonical_symbol: str
    provider_symbol: str
    description: str
    venue: str
    session_timezone: str
    contract_multiplier: float
    tick_size: float
    tick_value: float
    yahoo_symbol: str | None = None
    tiingo_symbol: str | None = None
    execution_ready: bool = False
    notes: str = "research-only metadata; verify before execution"

    def to_dict(self) -> dict:
        return asdict(self)


RESEARCH_FUTURES: dict[str, FuturesContract] = {
    "FUT/ES": FuturesContract(
        canonical_symbol="FUT/ES",
        provider_symbol="ES",
        description="S&P 500 E-mini",
        venue="CME",
        session_timezone="America/Chicago",
        contract_multiplier=50.0,
        tick_size=0.25,
        tick_value=12.50,
        yahoo_symbol="ES=F",
    ),
    "FUT/NQ": FuturesContract(
        canonical_symbol="FUT/NQ",
        provider_symbol="NQ",
        description="Nasdaq-100 E-mini",
        venue="CME",
        session_timezone="America/Chicago",
        contract_multiplier=20.0,
        tick_size=0.25,
        tick_value=5.00,
        yahoo_symbol="NQ=F",
    ),
    "FUT/RTY": FuturesContract(
        canonical_symbol="FUT/RTY",
        provider_symbol="RTY",
        description="Russell 2000 E-mini",
        venue="CME",
        session_timezone="America/Chicago",
        contract_multiplier=50.0,
        tick_size=0.10,
        tick_value=5.00,
        yahoo_symbol="RTY=F",
    ),
    "FUT/YM": FuturesContract(
        canonical_symbol="FUT/YM",
        provider_symbol="YM",
        description="Dow Jones E-mini",
        venue="CBOT",
        session_timezone="America/Chicago",
        contract_multiplier=5.0,
        tick_size=1.0,
        tick_value=5.00,
        yahoo_symbol="YM=F",
    ),
    "FUT/CL": FuturesContract(
        canonical_symbol="FUT/CL",
        provider_symbol="CL",
        description="Crude Oil WTI",
        venue="NYMEX",
        session_timezone="America/Chicago",
        contract_multiplier=1000.0,
        tick_size=0.01,
        tick_value=10.00,
        yahoo_symbol="CL=F",
    ),
    "FUT/GC": FuturesContract(
        canonical_symbol="FUT/GC",
        provider_symbol="GC",
        description="Gold",
        venue="COMEX",
        session_timezone="America/Chicago",
        contract_multiplier=100.0,
        tick_size=0.10,
        tick_value=10.00,
        yahoo_symbol="GC=F",
    ),
    "FUT/SI": FuturesContract(
        canonical_symbol="FUT/SI",
        provider_symbol="SI",
        description="Silver",
        venue="COMEX",
        session_timezone="America/Chicago",
        contract_multiplier=5000.0,
        tick_size=0.005,
        tick_value=25.00,
        yahoo_symbol="SI=F",
    ),
    "FUT/ZB": FuturesContract(
        canonical_symbol="FUT/ZB",
        provider_symbol="ZB",
        description="30-Year Treasury Bond",
        venue="CBOT",
        session_timezone="America/Chicago",
        contract_multiplier=1000.0,
        tick_size=0.03125,
        tick_value=31.25,
        yahoo_symbol="ZB=F",
    ),
    "FUT/ZN": FuturesContract(
        canonical_symbol="FUT/ZN",
        provider_symbol="ZN",
        description="10-Year Treasury Note",
        venue="CBOT",
        session_timezone="America/Chicago",
        contract_multiplier=1000.0,
        tick_size=0.015625,
        tick_value=15.625,
        yahoo_symbol="ZN=F",
    ),
    "FUT/NG": FuturesContract(
        canonical_symbol="FUT/NG",
        provider_symbol="NG",
        description="Natural Gas",
        venue="NYMEX",
        session_timezone="America/Chicago",
        contract_multiplier=10000.0,
        tick_size=0.001,
        tick_value=10.00,
        yahoo_symbol="NG=F",
    ),
}

_PROVIDER_TO_CANONICAL = {
    contract.provider_symbol.upper(): canonical
    for canonical, contract in RESEARCH_FUTURES.items()
}


def canonicalize_futures_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper()
    if s in RESEARCH_FUTURES:
        return s
    if s in _PROVIDER_TO_CANONICAL:
        return _PROVIDER_TO_CANONICAL[s]
    if s.startswith("FUT/"):
        raise KeyError(f"Unknown canonical futures symbol: {symbol}")
    raise KeyError(f"Unknown futures root: {symbol}")


def is_canonical_futures_symbol(symbol: str) -> bool:
    return str(symbol).strip().upper() in RESEARCH_FUTURES


def is_known_futures_symbol(symbol: str) -> bool:
    s = str(symbol).strip().upper()
    return s in RESEARCH_FUTURES or s in _PROVIDER_TO_CANONICAL


def get_futures_contract(symbol: str) -> FuturesContract:
    return RESEARCH_FUTURES[canonicalize_futures_symbol(symbol)]


def provider_symbol_for(symbol: str) -> str:
    return get_futures_contract(symbol).provider_symbol


def yahoo_symbol_for(symbol: str) -> str | None:
    return get_futures_contract(symbol).yahoo_symbol


def tiingo_symbol_for(symbol: str) -> str | None:
    return get_futures_contract(symbol).tiingo_symbol


def get_research_futures_symbols() -> list[str]:
    return list(RESEARCH_FUTURES.keys())
