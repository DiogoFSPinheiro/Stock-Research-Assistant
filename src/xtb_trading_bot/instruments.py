from __future__ import annotations

from dataclasses import dataclass

from .config import UniverseConfig
from .domain import AssetClass, Instrument


@dataclass
class InstrumentFilter:
    config: UniverseConfig
    banned_classes: tuple[AssetClass, ...] = (
        AssetClass.FUTURE,
        AssetClass.INSURANCE,
        AssetClass.SWAP,
        AssetClass.OTHER,
    )

    def can_analyze(self, instrument: Instrument) -> bool:
        if instrument.asset_class in self.banned_classes:
            return False
        if instrument.asset_class == AssetClass.FX:
            return instrument.symbol in self.config.allowed_fx
        if instrument.asset_class == AssetClass.STOCK:
            return instrument.symbol in self.config.allowed_stocks
        return instrument.symbol in self.config.context_symbols

    def can_trade(self, instrument: Instrument) -> bool:
        if not instrument.tradable or instrument.asset_class in self.banned_classes:
            return False
        if instrument.asset_class == AssetClass.FX:
            return instrument.symbol in self.config.allowed_fx
        if instrument.asset_class == AssetClass.STOCK:
            return instrument.symbol in self.config.allowed_stocks
        return False

    def filter_tradable(self, instruments: list[Instrument]) -> list[Instrument]:
        return [instrument for instrument in instruments if self.can_trade(instrument)]

    def filter_context(self, instruments: list[Instrument]) -> list[Instrument]:
        return [
            instrument
            for instrument in instruments
            if instrument.symbol in self.config.context_symbols and self.can_analyze(instrument)
        ]
