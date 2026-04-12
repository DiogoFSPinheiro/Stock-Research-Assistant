from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from statistics import mean

from .domain import AssetClass, Candle, ContextSnapshot, Instrument, Signal, SignalSide, StockFundamentals
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


def _bounded_score(value: float | None, low: float, high: float, inverse: bool = False) -> float:
    if value is None:
        return 0.5
    if inverse:
        if value <= low:
            return 1.0
        if value >= high:
            return 0.0
        return 1 - ((value - low) / (high - low))
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


@dataclass
class UndervaluedStockEngine:
    state_store: StateStore
    minimum_confidence: float = 0.60

    def evaluate(
        self,
        instrument: Instrument,
        timeframe: str,
        candles: list[Candle],
        context: list[ContextSnapshot],
        fundamentals: StockFundamentals,
        allow_repeat: bool = False,
    ) -> Signal:
        if instrument.asset_class != AssetClass.STOCK:
            return self._no_trade(instrument, timeframe, "Only stocks are evaluated for undervaluation.")
        if len(candles) < 60:
            return self._no_trade(instrument, timeframe, "Not enough candles for valuation overlay.")

        closes = [candle.close for candle in candles]
        current = closes[-1]
        ema_20 = _ema(closes[-20:], 8)
        ema_50 = _ema(closes[-50:], 21)
        atr = _atr(candles)
        context_score = mean(snapshot.trend_score for snapshot in context) if context else 0.0
        risk_on = all(snapshot.risk_on for snapshot in context) if context else True

        discount_to_target = None
        if fundamentals.target_mean_price and current:
            discount_to_target = (fundamentals.target_mean_price - current) / current

        valuation_score = mean(
            [
                _bounded_score(fundamentals.forward_pe, 8, 22, inverse=True),
                _bounded_score(fundamentals.trailing_pe, 10, 24, inverse=True),
                _bounded_score(fundamentals.price_to_book, 1, 6, inverse=True),
                _bounded_score(fundamentals.peg_ratio, 0.5, 2.0, inverse=True),
                _bounded_score(fundamentals.profit_margin, 0.05, 0.25),
                _bounded_score(fundamentals.return_on_equity, 0.08, 0.25),
                _bounded_score(fundamentals.revenue_growth, 0.02, 0.15),
                _bounded_score(fundamentals.earnings_growth, 0.02, 0.18),
                _bounded_score(fundamentals.debt_to_equity, 20, 180, inverse=True),
                _bounded_score(discount_to_target, 0.05, 0.25),
            ]
        )
        technical_score = mean(
            [
                1.0 if current >= ema_20 else 0.2,
                1.0 if ema_20 >= ema_50 else 0.3,
                _bounded_score((current - ema_50) / ema_50 if ema_50 else 0.0, -0.05, 0.15),
                _bounded_score(context_score, -0.2, 0.2),
            ]
        )
        confidence = round(valuation_score * 0.65 + technical_score * 0.35, 4)

        if not risk_on:
            return self._no_trade(instrument, timeframe, "Market context is risk-off.")
        if confidence < self.minimum_confidence:
            return self._no_trade(
                instrument,
                timeframe,
                f"Undervaluation score too weak ({confidence:.2f}); valuation={valuation_score:.2f}, technical={technical_score:.2f}.",
            )
        if not allow_repeat and self.state_store.has_recent_signal(instrument.symbol, timeframe, SignalSide.BUY.value):
            return self._no_trade(instrument, timeframe, "Duplicate stock pick suppressed.")

        stop_distance = max(atr * 1.2, current * 0.06)
        entry = round(min(current, ema_20 * 1.01), 4)
        stop_loss = round(entry - stop_distance, 4)
        target_base = fundamentals.target_mean_price if fundamentals.target_mean_price else entry + stop_distance * 2.2
        take_profit = round(max(target_base, entry + stop_distance * 1.8), 4)

        digest = sha1(f"{instrument.symbol}:{timeframe}:BUY:{entry}:{take_profit}".encode("utf-8")).hexdigest()[:12]
        rationale = (
            f"Undervalued stock candidate: valuation={valuation_score:.2f}, technical={technical_score:.2f}, "
            f"forwardPE={_fmt(fundamentals.forward_pe)}, P/B={_fmt(fundamentals.price_to_book)}, "
            f"ROE={_fmt_pct(fundamentals.return_on_equity)}, targetGap={_fmt_pct(discount_to_target)}"
        )
        return Signal(
            signal_id=digest,
            symbol=instrument.symbol,
            asset_class=instrument.asset_class,
            side=SignalSide.BUY,
            timeframe=timeframe,
            confidence=confidence,
            rationale=rationale,
            entry=entry,
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


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"
