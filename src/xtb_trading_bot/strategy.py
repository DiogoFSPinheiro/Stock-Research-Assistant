from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from statistics import mean

from .domain import AssetClass, Candle, ContextSnapshot, Instrument, Signal, SignalSide
from .interfaces import StateStore


def _ema(values: list[float], length: int) -> float:
    multiplier = 2 / (length + 1)
    result = values[0]
    for value in values[1:]:
        result = (value - result) * multiplier + result
    return result


def _atr(candles: list[Candle], length: int = 14) -> float:
    true_ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
        true_ranges.append(true_range)
        previous_close = candle.close
    sample = true_ranges[-length:] if len(true_ranges) >= length else true_ranges
    return mean(sample) if sample else 0.0


@dataclass
class TrendSignalEngine:
    state_store: StateStore
    minimum_confidence: float = 0.55
    volatility_floor: float = 0.002

    def evaluate(
        self,
        instrument: Instrument,
        timeframe: str,
        candles: list[Candle],
        context: list[ContextSnapshot],
    ) -> Signal:
        if len(candles) < 30:
            return self._no_trade(instrument, timeframe, "Not enough candles for evaluation.")

        closes = [candle.close for candle in candles]
        fast = _ema(closes[-20:], min(8, len(closes[-20:])))
        slow = _ema(closes[-30:], min(21, len(closes[-30:])))
        current = closes[-1]
        previous = closes[-2]
        atr_ratio = _atr(candles) / current if current else 0.0
        momentum = (current - previous) / previous if previous else 0.0
        context_score = mean(snapshot.trend_score for snapshot in context) if context else 0.0
        risk_on = all(snapshot.risk_on for snapshot in context) if context else True

        if atr_ratio < self.volatility_floor:
            return self._no_trade(instrument, timeframe, "Volatility below floor.")
        if not risk_on:
            return self._no_trade(instrument, timeframe, "Context filter is risk-off.")

        side = SignalSide.NO_TRADE
        if current > slow and fast > slow and momentum > 0 and context_score >= -0.2:
            side = SignalSide.BUY
        elif instrument.asset_class == AssetClass.FX and current < slow and fast < slow and momentum < 0 and context_score <= 0.2:
            side = SignalSide.SELL

        if side == SignalSide.NO_TRADE:
            return self._no_trade(instrument, timeframe, "Trend filters are not aligned.")

        if self.state_store.has_recent_signal(instrument.symbol, timeframe, side.value):
            return self._no_trade(instrument, timeframe, "Duplicate signal suppressed.")

        confidence = min(
            0.95,
            0.5 + abs((fast - slow) / slow) * 10 + abs(momentum) * 15 + max(context_score, 0) * 0.1,
        )
        if confidence < self.minimum_confidence:
            return self._no_trade(instrument, timeframe, "Confidence below threshold.")

        stop_distance = max(_atr(candles) * 1.5, current * 0.005)
        if side == SignalSide.BUY:
            stop_loss = current - stop_distance
            take_profit = current + stop_distance * 2
        else:
            stop_loss = current + stop_distance
            take_profit = current - stop_distance * 2

        digest = sha1(f"{instrument.symbol}:{timeframe}:{side.value}:{current}".encode("utf-8")).hexdigest()[:12]
        return Signal(
            signal_id=digest,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            side=side,
            timeframe=timeframe,
            confidence=confidence,
            rationale=(
                f"Trend aligned on {timeframe}; fast EMA={fast:.4f}, slow EMA={slow:.4f}, "
                f"momentum={momentum:.4%}, context={context_score:.2f}"
            ),
            entry=current,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def _no_trade(self, instrument: Instrument, timeframe: str, rationale: str) -> Signal:
        digest = sha1(f"{instrument.symbol}:{timeframe}:NO_TRADE:{rationale}".encode("utf-8")).hexdigest()[:12]
        return Signal(
            signal_id=digest,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            side=SignalSide.NO_TRADE,
            timeframe=timeframe,
            confidence=0.0,
            rationale=rationale,
            entry=None,
            stop_loss=None,
            take_profit=None,
        )
