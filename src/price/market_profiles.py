"""Substrate-level market profiles.

These profiles are intentionally additive. They are meant to power isolated
research lanes (crypto, futures) first, without changing the current equity
live paper system.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from price.futures_metadata import get_research_futures_symbols, is_known_futures_symbol


@dataclass(frozen=True)
class MarketProfile:
    name: str
    asset_class: str
    is_24_7: bool
    session_model: str
    default_condition_symbols: tuple[str, ...]
    default_bin_mode: str
    supports_rth_filter: bool
    default_output_dir: str
    default_timeframes: tuple[str, ...]
    default_discovery_mode: str
    execution_enabled_default: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, MarketProfile] = {
    "equity": MarketProfile(
        name="equity",
        asset_class="equity",
        is_24_7=False,
        session_model="xnys_rth",
        default_condition_symbols=("USO", "TLT"),
        default_bin_mode="rolling",
        supports_rth_filter=True,
        default_output_dir=str(Path("localdata/research/equity")),
        default_timeframes=("1d", "1h", "15m"),
        default_discovery_mode="default",
        execution_enabled_default=True,
        notes="Current live paper system substrate.",
    ),
    "crypto": MarketProfile(
        name="crypto",
        asset_class="crypto",
        is_24_7=True,
        session_model="utc_24x7",
        default_condition_symbols=("BTC/USD", "ETH/USD"),
        default_bin_mode="rolling",
        supports_rth_filter=False,
        default_output_dir=str(Path("localdata/research/crypto")),
        default_timeframes=("1d", "1h", "15m"),
        default_discovery_mode="crypto",
        execution_enabled_default=False,
        notes="Research-first lane. No live paper deployment by default.",
    ),
    "futures": MarketProfile(
        name="futures",
        asset_class="futures",
        is_24_7=False,
        session_model="exchange_extended",
        default_condition_symbols=(),
        default_bin_mode="rolling",
        supports_rth_filter=False,
        default_output_dir=str(Path("localdata/research/futures")),
        default_timeframes=("1d",),
        default_discovery_mode="futures",
        execution_enabled_default=False,
        notes="Research-only foundation; execution disabled until contract-aware risk exists.",
    ),
}


def get_market_profile(name: str) -> MarketProfile:
    key = str(name).strip().lower()
    if key not in PROFILES:
        raise KeyError(f"Unknown market profile: {name}")
    return PROFILES[key]


def infer_market_profile(symbol: str) -> MarketProfile:
    s = str(symbol).strip().upper()
    if is_known_futures_symbol(s) or s.startswith("FUT/"):
        return PROFILES["futures"]
    if "/" in s:
        return PROFILES["crypto"]
    return PROFILES["equity"]


def get_default_condition_symbols(profile_name: str) -> tuple[str, ...]:
    return get_market_profile(profile_name).default_condition_symbols


def get_default_output_dir(profile_name: str) -> Path:
    return Path(get_market_profile(profile_name).default_output_dir)


def get_default_timeframes(profile_name: str) -> tuple[str, ...]:
    return get_market_profile(profile_name).default_timeframes


def get_research_symbols_for_profile(profile_name: str) -> list[str]:
    profile = get_market_profile(profile_name)
    if profile.name == "futures":
        return get_research_futures_symbols()
    raise KeyError(f"No built-in symbol resolver for profile: {profile_name}")
