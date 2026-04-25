from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha1
import math
from statistics import mean

from .domain import AssetClass, Candle, ContextSnapshot, Instrument, Signal, SignalSide, StockFundamentals
from .interfaces import StateStore
from .valuation import compute_fair_value


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
class UndervaluedStockEngine:
    state_store: StateStore
    minimum_confidence: float = 0.45
    minimum_probability_positive: float = 0.50
    uncertainty_penalty: float = 0.45
    horizon_days: tuple[int, ...] = (1, 5, 21, 126)
    minimum_margin_of_safety: float = 0.10
    minimum_quality_score: float = 0.45

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
        daily_returns = [
            (closes[index] - closes[index - 1]) / closes[index - 1]
            for index in range(1, len(closes))
            if closes[index - 1]
        ]
        realized_volatility = math.sqrt(mean([value * value for value in daily_returns[-20:]]) or 0.0) if daily_returns else 0.0
        context_score = mean(snapshot.trend_score for snapshot in context) if context else 0.0
        risk_on = all(snapshot.risk_on for snapshot in context) if context else True
        live_fundamentals = replace(fundamentals, current_price=current)
        valuation = compute_fair_value(live_fundamentals)
        discount_to_target = None
        if fundamentals.target_mean_price and current:
            discount_to_target = (fundamentals.target_mean_price - current) / current
        price_vs_ema50 = (current - ema_50) / ema_50 if ema_50 else 0.0
        price_vs_ema20 = (current - ema_20) / ema_20 if ema_20 else 0.0
        timing_score = mean(
            [
                1.0 if current >= ema_20 else 0.3,
                1.0 if ema_20 >= ema_50 else 0.35,
                max(0.0, min(1.0, (price_vs_ema50 + 0.08) / 0.18)),
                max(0.0, min(1.0, (price_vs_ema20 + 0.05) / 0.12)),
                max(0.0, min(1.0, (context_score + 0.15) / 0.30)),
            ]
        )
        confidence = round(
            max(0.0, min(0.99, (valuation.margin_of_safety or 0.0) * 1.4)) * 0.5
            + valuation.quality_score * 0.35
            + timing_score * 0.15,
            4,
        )
        trend_gap = abs(ema_20 - ema_50) / ema_50 if ema_50 else 0.0
        short_momentum = (current - closes[-6]) / closes[-6] if len(closes) >= 6 and closes[-6] else 0.0
        medium_momentum = (current - closes[-22]) / closes[-22] if len(closes) >= 22 and closes[-22] else short_momentum
        long_momentum = (current - closes[0]) / closes[0] if closes[0] else medium_momentum

        if not risk_on:
            return self._no_trade(instrument, timeframe, "Market context is risk-off.")
        if fundamentals.earnings_yield is None and fundamentals.free_cash_flow_yield is None:
            return self._no_trade(instrument, timeframe, "Missing core valuation inputs for fair value.")
        if fundamentals.profit_margin is None or fundamentals.operating_margin is None or fundamentals.return_on_equity is None:
            return self._no_trade(instrument, timeframe, "Missing core quality inputs for fair value.")
        if fundamentals.profit_margin < 0 or fundamentals.operating_margin < 0:
            return self._no_trade(instrument, timeframe, "Profitability is negative, which fails the quality screen.")
        if fundamentals.net_debt_to_ebit is not None and fundamentals.net_debt_to_ebit > 4.0:
            return self._no_trade(instrument, timeframe, "Leverage is too high for the quality-value model.")
        if fundamentals.debt_to_equity is not None and fundamentals.debt_to_equity > 180:
            return self._no_trade(instrument, timeframe, "Debt-to-equity is too high for the quality-value model.")
        if valuation.margin_of_safety is None:
            return self._no_trade(instrument, timeframe, "Fair value could not be estimated with enough confidence.")
        if valuation.margin_of_safety < self.minimum_margin_of_safety:
            return self._no_trade(
                instrument,
                timeframe,
                f"Upside after fair-value adjustments is too small ({valuation.margin_of_safety:.1%}).",
            )
        if valuation.quality_score < self.minimum_quality_score:
            return self._no_trade(
                instrument,
                timeframe,
                f"Business quality score is too weak ({valuation.quality_score:.2f}) for a value pick.",
            )
        if confidence < self.minimum_confidence:
            return self._no_trade(
                instrument,
                timeframe,
                f"Overall conviction too weak ({confidence:.2f}); margin={valuation.margin_of_safety:.1%}, quality={valuation.quality_score:.2f}, timing={timing_score:.2f}.",
            )
        if not allow_repeat and self.state_store.has_recent_signal(instrument.symbol, timeframe, SignalSide.BUY.value):
            return self._no_trade(instrument, timeframe, "Duplicate stock pick suppressed.")

        horizon_signals: list[Signal] = []
        base_target_gap = valuation.margin_of_safety or discount_to_target or 0.0
        for horizon_days in self.horizon_days:
            horizon_label = self._horizon_label(horizon_days)
            momentum_weight = min(1.0, horizon_days / 21)
            target_weight = 0.85 - (0.20 * momentum_weight)
            momentum_signal = mean(
                [
                    short_momentum * 0.8,
                    medium_momentum * min(1.0, horizon_days / 21) * 0.9,
                    long_momentum * min(1.0, horizon_days / 126) * 0.5,
                    context_score,
                ]
            )
            expected_return = max(
                -0.15,
                min(
                    0.45,
                    (base_target_gap * target_weight)
                    + (momentum_signal * 0.20)
                    + (valuation.quality_score * 0.08)
                    + (timing_score * 0.05)
                    + ((confidence - 0.5) * 0.08),
                ),
            )
            horizon_uncertainty = max(
                0.01,
                (realized_volatility * math.sqrt(max(horizon_days, 1)))
                + (trend_gap * 0.12)
                + ((1 - confidence) * 0.08),
            )
            adjusted_return = expected_return - (self.uncertainty_penalty * horizon_uncertainty)
            normalized_score = (adjusted_return / horizon_days) + ((valuation.margin_of_safety or 0.0) * 0.02)
            probability_positive = _normal_cdf(expected_return / horizon_uncertainty) if horizon_uncertainty else 1.0
            buy_confidence = max(0.0, min(0.99, confidence * probability_positive))
            if adjusted_return <= 0 or probability_positive < self.minimum_probability_positive:
                continue

            entry = round(min(current, ema_20 * 1.01), 4)
            target_price = min(
                (valuation.fair_value or current * (1 + expected_return)),
                current * (1 + expected_return * 1.15),
            )
            stop_distance = max(atr * 1.2, current * (0.02 + (horizon_uncertainty * 0.5)))
            stop_loss = round(entry - stop_distance, 4)
            take_profit = round(max(target_price, entry + stop_distance * 1.8), 4)
            digest = sha1(
                f"{instrument.symbol}:{timeframe}:{horizon_days}:BUY:{entry}:{take_profit}".encode("utf-8")
            ).hexdigest()[:12]
            rationale = (
                f"Best horizon {horizon_label}: price={current:.2f}, fairValue={_fmt(valuation.fair_value)}, "
                f"margin={_fmt_pct(valuation.margin_of_safety)}, quality={valuation.quality_score:.2f}, "
                f"timing={timing_score:.2f}, expected={expected_return:.1%}, why={valuation.primary_reason}, "
                f"risk={valuation.primary_risk}"
            )
            horizon_signals.append(
                Signal(
                    signal_id=digest,
                    symbol=instrument.symbol,
                    company_name=fundamentals.company_name,
                    asset_class=instrument.asset_class,
                    side=SignalSide.BUY,
                    timeframe=timeframe,
                    confidence=round(buy_confidence, 4),
                    rationale=rationale,
                    entry=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    expected_return=round(expected_return, 6),
                    adjusted_return=round(adjusted_return, 6),
                    normalized_score=round(normalized_score, 6),
                    uncertainty=round(horizon_uncertainty, 6),
                    probability_positive=round(probability_positive, 6),
                    horizon_days=horizon_days,
                    fair_value=valuation.fair_value,
                    margin_of_safety=valuation.margin_of_safety,
                    quality_score=valuation.quality_score,
                    timing_score=round(timing_score, 4),
                    risk_flags=valuation.risk_flags,
                )
            )

        if not horizon_signals:
            return self._no_trade(
                instrument,
                timeframe,
                "All forecast horizons were filtered out by uncertainty or low probability of gains.",
            )

        return max(
            horizon_signals,
            key=lambda signal: (
                signal.normalized_score if signal.normalized_score is not None else float("-inf"),
                signal.margin_of_safety if signal.margin_of_safety is not None else float("-inf"),
                signal.quality_score if signal.quality_score is not None else float("-inf"),
                signal.probability_positive if signal.probability_positive is not None else float("-inf"),
                signal.confidence,
            ),
        )

    def _no_trade(self, instrument: Instrument, timeframe: str, rationale: str) -> Signal:
        digest = sha1(f"{instrument.symbol}:{timeframe}:NO_TRADE:{rationale}".encode("utf-8")).hexdigest()[:12]
        return Signal(
            signal_id=digest,
            symbol=instrument.symbol,
            company_name=None,
            asset_class=instrument.asset_class,
            side=SignalSide.NO_TRADE,
            timeframe=timeframe,
            confidence=0.0,
            rationale=rationale,
            entry=None,
            stop_loss=None,
            take_profit=None,
        )

    def _horizon_label(self, horizon_days: int) -> str:
        if horizon_days == 1:
            return "1d"
        if horizon_days < 21:
            return f"{horizon_days}d"
        if horizon_days < 126:
            return "1m"
        return "6m"


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))
