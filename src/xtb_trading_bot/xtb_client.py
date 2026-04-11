from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from statistics import mean

from .config import XtbConfig
from .domain import AssetClass, Candle, ContextSnapshot, Instrument, OrderProposal, PositionSnapshot, SignalSide


def _infer_asset_class(symbol: str) -> AssetClass:
    if len(symbol) == 6 and symbol.isalpha():
        return AssetClass.FX
    if symbol in {"SPX500", "US100", "DE40"}:
        return AssetClass.INDEX
    if symbol in {"GOLD", "SILVER", "WTI"}:
        return AssetClass.COMMODITY
    if symbol.endswith("USD") and len(symbol) > 6:
        return AssetClass.CRYPTO
    return AssetClass.STOCK


@dataclass
class XtbClient:
    config: XtbConfig
    instruments_seed: list[str]
    context_seed: list[str]
    orders: list[OrderProposal] = field(default_factory=list)

    def login(self) -> bool:
        return bool(self.config.user_id and self.config.password)

    def list_instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for symbol in [*self.instruments_seed, *self.context_seed]:
            asset_class = _infer_asset_class(symbol)
            instruments.append(
                Instrument(
                    symbol=symbol,
                    asset_class=asset_class,
                    market="US" if asset_class == AssetClass.STOCK else "GLOBAL",
                    tradable=asset_class in {AssetClass.STOCK, AssetClass.FX},
                )
            )
        instruments.extend(
            [
                Instrument("OIL_FUT", AssetClass.FUTURE, "GLOBAL", False),
                Instrument("XTB_SWAP", AssetClass.SWAP, "GLOBAL", False),
            ]
        )
        return instruments

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        base = self.get_quote(symbol)
        step = 0.35 if timeframe == "D1" else 0.15
        direction = 1 if symbol not in {"USDJPY", "BTCUSD"} else -1
        candles: list[Candle] = []
        start = datetime.now(timezone.utc) - timedelta(hours=limit * 4)
        for index in range(limit):
            trend_component = direction * step * index
            noise = ((index % 5) - 2) * 0.03
            close = round(base + trend_component + noise, 4)
            open_price = round(close - direction * 0.08, 4)
            high = round(max(open_price, close) + 0.12, 4)
            low = round(min(open_price, close) - 0.12, 4)
            candles.append(
                Candle(
                    timestamp=start + timedelta(hours=index * 4),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1000 + index * 10,
                )
            )
        return candles

    def get_quote(self, symbol: str) -> float:
        digest = int(sha1(symbol.encode("utf-8")).hexdigest()[:8], 16)
        if _infer_asset_class(symbol) == AssetClass.FX:
            return round(1 + (digest % 5000) / 10000, 4)
        return round(80 + (digest % 5000) / 20, 4)

    def get_context(self, symbols: list[str]) -> list[ContextSnapshot]:
        snapshots: list[ContextSnapshot] = []
        for symbol in symbols:
            candles = self.get_candles(symbol, "D1", 20)
            closes = [candle.close for candle in candles]
            trend_score = (closes[-1] - mean(closes[-5:])) / closes[-1]
            snapshots.append(
                ContextSnapshot(
                    symbol=symbol,
                    asset_class=_infer_asset_class(symbol),
                    trend_score=trend_score,
                    risk_on=trend_score > -0.03,
                )
            )
        return snapshots

    def submit_order(self, proposal: OrderProposal) -> str:
        self.orders.append(proposal)
        return sha1(f"execution:{proposal.proposal_id}".encode("utf-8")).hexdigest()[:10]

    def list_positions(self) -> list[PositionSnapshot]:
        positions: list[PositionSnapshot] = []
        for proposal in self.orders:
            current_price = self.get_quote(proposal.symbol)
            pnl_direction = 1 if proposal.side == SignalSide.BUY else -1
            unrealized_pnl = (current_price - proposal.entry) * proposal.quantity * pnl_direction
            residual_risk = abs(current_price - proposal.stop_loss) * proposal.quantity
            positions.append(
                PositionSnapshot(
                    symbol=proposal.symbol,
                    side=proposal.side,
                    quantity=proposal.quantity,
                    entry_price=proposal.entry,
                    current_price=current_price,
                    unrealized_pnl=round(unrealized_pnl, 2),
                    residual_risk=round(residual_risk, 2),
                )
            )
        return positions
