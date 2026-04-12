from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from statistics import mean
import time
from typing import Any, Callable
from urllib import parse, request

from .config import MarketDataConfig, UniverseConfig
from .domain import AssetClass, Candle, ContextSnapshot, Instrument, PositionSnapshot, StockFundamentals


class MarketDataError(RuntimeError):
    pass


HttpGet = Callable[[str, int], dict]
TickerFactory = Callable[[str], Any]


def _default_get_json(url: str, timeout: int) -> dict:
    req = request.Request(url, headers={"User-Agent": "xtb-trading-bot/0.1"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_yfinance_ticker(symbol: str) -> Any:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - depends on runtime environment
        raise MarketDataError("yfinance is not installed. Run `python -m pip install -e .` in the project venv.") from exc
    return yf.Ticker(symbol)


def _is_retryable_market_data_exception(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return (
        isinstance(exc, (TimeoutError, OSError, ValueError))
        or "timeout" in name
        or "timed out" in message
        or "readtimeout" in name
        or "connectionerror" in name
        or "remote" in message
        or "nonetype" in message
        or "subscriptable" in message
    )


def _chunked(items: list[Candle], size: int) -> list[list[Candle]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _aggregate_candles(candles: list[Candle], size: int) -> list[Candle]:
    aggregated: list[Candle] = []
    for group in _chunked(candles, size):
        if len(group) < size:
            continue
        aggregated.append(
            Candle(
                timestamp=group[-1].timestamp,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
            )
        )
    return aggregated


def _expand_daily_to_four_hour(candles: list[Candle], limit: int) -> list[Candle]:
    expanded: list[Candle] = []
    for candle in candles:
        slot_length = (candle.high - candle.low) / 6 if candle.high != candle.low else max(candle.close * 0.001, 0.01)
        for slot in range(6):
            slot_start = candle.timestamp + timedelta(hours=slot * 4)
            slot_open = candle.open if slot == 0 else expanded[-1].close
            slot_close = candle.close if slot == 5 else round(slot_open + (candle.close - candle.open) / 6, 4)
            slot_high = max(slot_open, slot_close, candle.low + slot_length * (slot + 1))
            slot_low = min(slot_open, slot_close, candle.low + slot_length * slot)
            expanded.append(
                Candle(
                    timestamp=slot_start,
                    open=round(slot_open, 4),
                    high=round(min(slot_high, candle.high), 4),
                    low=round(max(min(slot_low, candle.high), candle.low), 4),
                    close=round(slot_close, 4),
                    volume=candle.volume / 6,
                )
            )
    return expanded[-limit:]


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


def _provider_symbol(symbol: str) -> str:
    if _infer_asset_class(symbol) != AssetClass.STOCK:
        return symbol
    if "." not in symbol:
        return symbol
    head, tail = symbol.rsplit(".", 1)
    # Yahoo uses dashes for US share classes like BRK.B, but keeps exchange suffixes such as EDP.LS.
    if tail.isalpha() and len(tail) == 1:
        return f"{head}-{tail}"
    return symbol


@dataclass
class SyntheticMarketDataProvider:
    universe: UniverseConfig

    def list_instruments(self) -> list[Instrument]:
        symbols = [*self.universe.allowed_fx, *self.universe.allowed_stocks, *self.universe.context_symbols]
        instruments = [
            Instrument(
                symbol=symbol,
                asset_class=_infer_asset_class(symbol),
                market="US" if _infer_asset_class(symbol) == AssetClass.STOCK else "GLOBAL",
                tradable=_infer_asset_class(symbol) in {AssetClass.STOCK, AssetClass.FX},
            )
            for symbol in symbols
        ]
        instruments.extend(
            [
                Instrument("OIL_FUT", AssetClass.FUTURE, "GLOBAL", False),
                Instrument("XTB_SWAP", AssetClass.SWAP, "GLOBAL", False),
            ]
        )
        return instruments

    def get_quote(self, symbol: str) -> float:
        seed = sum(ord(char) for char in symbol)
        if _infer_asset_class(symbol) == AssetClass.FX:
            return round(1 + (seed % 5000) / 10000, 4)
        return round(80 + (seed % 5000) / 20, 4)

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        base = self.get_quote(symbol)
        step = 0.35 if timeframe == "D1" else 0.15
        direction = 1 if symbol not in {"USDJPY", "BTCUSD"} else -1
        candles: list[Candle] = []
        step_hours = 24 if timeframe == "D1" else 4
        start = datetime.now(timezone.utc) - timedelta(hours=step_hours * limit)
        for index in range(limit):
            trend_component = direction * step * index
            noise = ((index % 5) - 2) * 0.03
            close = round(base + trend_component + noise, 4)
            open_price = round(close - direction * 0.08, 4)
            high = round(max(open_price, close) + 0.12, 4)
            low = round(min(open_price, close) - 0.12, 4)
            candles.append(
                Candle(
                    timestamp=start + timedelta(hours=step_hours * index),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=1000 + index * 10,
                )
            )
        return candles

    def get_context(self, symbols: list[str]) -> list[ContextSnapshot]:
        snapshots: list[ContextSnapshot] = []
        for symbol in symbols:
            try:
                candles = self.get_candles(symbol, "D1", 20)
            except MarketDataError:
                continue
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

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        current_price = self.get_quote(symbol)
        return StockFundamentals(
            symbol=symbol,
            current_price=current_price,
            market_cap=200_000_000_000,
            trailing_pe=18.0,
            forward_pe=16.0,
            price_to_book=3.2,
            peg_ratio=1.1,
            profit_margin=0.18,
            operating_margin=0.22,
            return_on_equity=0.19,
            revenue_growth=0.11,
            earnings_growth=0.14,
            debt_to_equity=45.0,
            target_mean_price=current_price * 1.18,
        )


@dataclass
class AlphaVantageMarketDataProvider:
    config: MarketDataConfig
    universe: UniverseConfig
    http_get_json: HttpGet = _default_get_json
    last_payload: dict | None = None

    def list_instruments(self) -> list[Instrument]:
        return SyntheticMarketDataProvider(self.universe).list_instruments()

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        raise MarketDataError("Stock fundamentals are not supported for alpha_vantage in this runtime.")

    def get_quote(self, symbol: str) -> float:
        candles = self.get_candles(symbol, "D1", 1)
        if not candles:
            raise MarketDataError(f"No quote data available for {symbol}.")
        return candles[-1].close

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        if not self.config.alpha_vantage_api_key:
            raise MarketDataError("ALPHA_VANTAGE_API_KEY is required when MARKET_DATA_PROVIDER=alpha_vantage.")
        if _infer_asset_class(symbol) == AssetClass.FX:
            return self._get_fx_candles(symbol, timeframe, limit)
        return self._get_stock_candles(symbol, timeframe, limit)

    def get_context(self, symbols: list[str]) -> list[ContextSnapshot]:
        snapshots: list[ContextSnapshot] = []
        for symbol in symbols:
            try:
                candles = self.get_candles(symbol, "D1", 20)
            except MarketDataError:
                continue
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

    def _get_stock_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        symbol = _provider_symbol(symbol)
        if timeframe == "D1":
            payload = self._query(function="TIME_SERIES_DAILY_ADJUSTED", symbol=symbol, outputsize="compact")
            series = payload.get("Time Series (Daily)")
            return self._parse_daily_series(series, limit)
        try:
            payload = self._query(
                function="TIME_SERIES_INTRADAY",
                symbol=symbol,
                interval="60min",
                outputsize="compact",
                adjusted="true",
                extended_hours="false",
            )
            series = payload.get("Time Series (60min)")
            candles = self._parse_intraday_series(series, limit * 4)
            aggregated = _aggregate_candles(candles, 4)[-limit:]
            if aggregated:
                return aggregated
        except MarketDataError:
            pass
        daily = self._get_stock_candles(symbol, "D1", max(limit // 6 + 10, 30))
        return _expand_daily_to_four_hour(daily, limit)

    def _get_fx_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        from_symbol = symbol[:3]
        to_symbol = symbol[3:]
        if timeframe == "D1":
            payload = self._query(
                function="FX_DAILY",
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                outputsize="compact",
            )
            series = payload.get("Time Series FX (Daily)")
            return self._parse_daily_series(series, limit)

        try:
            payload = self._query(
                function="FX_INTRADAY",
                from_symbol=from_symbol,
                to_symbol=to_symbol,
                interval="60min",
                outputsize="compact",
            )
            series = payload.get("Time Series FX (60min)")
            candles = self._parse_intraday_series(series, limit * 4)
            aggregated = _aggregate_candles(candles, 4)[-limit:]
            if aggregated:
                return aggregated
        except MarketDataError:
            pass
        daily = self._get_fx_candles(symbol, "D1", max(limit // 6 + 10, 30))
        return _expand_daily_to_four_hour(daily, limit)

    def _query(self, **params: str) -> dict:
        payload = {
            **params,
            "apikey": self.config.alpha_vantage_api_key,
        }
        url = f"{self.config.alpha_vantage_base_url}?{parse.urlencode(payload)}"
        data = self.http_get_json(url, self.config.request_timeout_seconds)
        self.last_payload = data if isinstance(data, dict) else None
        if "Error Message" in data:
            raise MarketDataError(data["Error Message"])
        if "Information" in data:
            raise MarketDataError(data["Information"])
        if "Note" in data:
            raise MarketDataError(data["Note"])
        return data

    def _parse_daily_series(self, series: dict | None, limit: int) -> list[Candle]:
        if not series:
            raise MarketDataError(self._format_missing_payload("daily"))
        candles: list[Candle] = []
        for timestamp, values in sorted(series.items()):
            candles.append(
                Candle(
                    timestamp=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc),
                    open=float(values["1. open"]),
                    high=float(values["2. high"]),
                    low=float(values["3. low"]),
                    close=float(values["4. close"]),
                    volume=float(values.get("6. volume") or values.get("5. volume") or 0.0),
                )
            )
        return candles[-limit:]

    def _parse_intraday_series(self, series: dict | None, limit: int) -> list[Candle]:
        if not series:
            raise MarketDataError(self._format_missing_payload("intraday"))
        candles: list[Candle] = []
        for timestamp, values in sorted(series.items()):
            candles.append(
                Candle(
                    timestamp=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc),
                    open=float(values["1. open"]),
                    high=float(values["2. high"]),
                    low=float(values["3. low"]),
                    close=float(values["4. close"]),
                    volume=float(values.get("5. volume", 0.0)),
                )
            )
        return candles[-limit:]

    def _format_missing_payload(self, series_name: str) -> str:
        if not isinstance(self.last_payload, dict) or not self.last_payload:
            return f"Missing {series_name} time series payload."
        keys = ", ".join(sorted(self.last_payload.keys()))
        return f"Missing {series_name} time series payload. Response keys: {keys}"


@dataclass
class YFinanceMarketDataProvider:
    config: MarketDataConfig
    universe: UniverseConfig
    ticker_factory: TickerFactory = _default_yfinance_ticker
    cache_ttl_seconds: int = 900
    ticker_cache: dict[str, Any] = field(default_factory=dict)
    candle_cache: dict[tuple[str, str], tuple[float, list[Candle]]] = field(default_factory=dict)
    fundamentals_cache: dict[str, tuple[float, StockFundamentals]] = field(default_factory=dict)

    def list_instruments(self) -> list[Instrument]:
        return SyntheticMarketDataProvider(self.universe).list_instruments()

    def list_positions(self) -> list[PositionSnapshot]:
        return []

    def get_stock_fundamentals(self, symbol: str) -> StockFundamentals:
        cached = self.fundamentals_cache.get(symbol)
        if cached is not None and (time.monotonic() - cached[0]) <= self.cache_ttl_seconds:
            return cached[1]
        provider_symbol = self._provider_symbol(symbol)
        ticker = self._build_ticker(provider_symbol, symbol)
        try:
            info = getattr(ticker, "info", {}) or {}
        except Exception as exc:
            if not _is_retryable_market_data_exception(exc):
                raise
            raise MarketDataError(f"Unable to load fundamentals for {symbol}: {exc}") from exc
        current_price = float(
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
            or self.get_quote(symbol)
        )
        fundamentals = StockFundamentals(
            symbol=symbol,
            current_price=current_price,
            market_cap=_optional_float(info.get("marketCap")),
            trailing_pe=_optional_float(info.get("trailingPE")),
            forward_pe=_optional_float(info.get("forwardPE")),
            price_to_book=_optional_float(info.get("priceToBook")),
            peg_ratio=_optional_float(info.get("pegRatio")),
            profit_margin=_optional_float(info.get("profitMargins")),
            operating_margin=_optional_float(info.get("operatingMargins")),
            return_on_equity=_optional_float(info.get("returnOnEquity")),
            revenue_growth=_optional_float(info.get("revenueGrowth")),
            earnings_growth=_optional_float(info.get("earningsGrowth")),
            debt_to_equity=_optional_float(info.get("debtToEquity")),
            target_mean_price=_optional_float(info.get("targetMeanPrice")),
        )
        self.fundamentals_cache[symbol] = (time.monotonic(), fundamentals)
        return fundamentals

    def get_quote(self, symbol: str) -> float:
        candles = self.get_candles(symbol, "D1", 1)
        if not candles:
            raise MarketDataError(f"No quote data available for {symbol}.")
        return candles[-1].close

    def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        cache_key = (symbol, timeframe)
        cached = self.candle_cache.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) <= self.cache_ttl_seconds:
            cached_candles = cached[1]
            if len(cached_candles) >= limit:
                return cached_candles[-limit:]
        provider_symbol = self._provider_symbol(symbol)
        interval = "1d" if timeframe == "D1" else "1h"
        period = "1y" if timeframe == "D1" else "60d"
        ticker = self._build_ticker(provider_symbol, symbol)
        try:
            history = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            if not _is_retryable_market_data_exception(exc):
                raise
            raise MarketDataError(f"Unable to load price history for {symbol}: {exc}") from exc
        if history.empty:
            raise MarketDataError(f"No price history returned for {symbol}.")

        candles: list[Candle] = []
        for timestamp, row in history.tail(max(limit * 6, limit)).iterrows():
            candle_time = timestamp.to_pydatetime() if hasattr(timestamp, "to_pydatetime") else timestamp
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)
            else:
                candle_time = candle_time.astimezone(timezone.utc)
            candles.append(
                Candle(
                    timestamp=candle_time,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0.0)),
                )
            )

        if timeframe == "H4":
            aggregated = _aggregate_candles(candles, 4)[-limit:]
            if aggregated:
                self.candle_cache[cache_key] = (time.monotonic(), aggregated)
                return aggregated
            daily = self.get_candles(symbol, "D1", max(limit // 6 + 10, 30))
            expanded = _expand_daily_to_four_hour(daily, limit)
            self.candle_cache[cache_key] = (time.monotonic(), expanded)
            return expanded
        result = candles[-limit:]
        self.candle_cache[cache_key] = (time.monotonic(), result)
        return result

    def get_context(self, symbols: list[str]) -> list[ContextSnapshot]:
        snapshots: list[ContextSnapshot] = []
        for symbol in symbols:
            try:
                candles = self.get_candles(symbol, "D1", 20)
            except MarketDataError:
                continue
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

    def _provider_symbol(self, symbol: str) -> str:
        if _infer_asset_class(symbol) == AssetClass.FX:
            return f"{symbol}=X"
        return _provider_symbol(symbol)

    def _build_ticker(self, provider_symbol: str, symbol: str) -> Any:
        cached = self.ticker_cache.get(provider_symbol)
        if cached is not None:
            return cached
        try:
            ticker = self.ticker_factory(provider_symbol)
        except Exception as exc:
            if not _is_retryable_market_data_exception(exc):
                raise
            raise MarketDataError(f"Unable to initialize market data for {symbol}: {exc}") from exc
        self.ticker_cache[provider_symbol] = ticker
        return ticker


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_market_data_provider(
    config: MarketDataConfig, universe: UniverseConfig
) -> SyntheticMarketDataProvider | AlphaVantageMarketDataProvider | YFinanceMarketDataProvider:
    if config.provider == "yfinance":
        return YFinanceMarketDataProvider(config, universe)
    if config.provider == "alpha_vantage":
        return AlphaVantageMarketDataProvider(config, universe)
    return SyntheticMarketDataProvider(universe)
