from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from statistics import mean
from typing import Callable
from urllib import parse, request

from .config import MarketDataConfig, UniverseConfig
from .domain import AssetClass, Candle, ContextSnapshot, Instrument, PositionSnapshot


class MarketDataError(RuntimeError):
    pass


HttpGet = Callable[[str, int], dict]


def _default_get_json(url: str, timeout: int) -> dict:
    req = request.Request(url, headers={"User-Agent": "xtb-trading-bot/0.1"})
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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

    def list_positions(self) -> list[PositionSnapshot]:
        return []


@dataclass
class AlphaVantageMarketDataProvider:
    config: MarketDataConfig
    universe: UniverseConfig
    http_get_json: HttpGet = _default_get_json

    def list_instruments(self) -> list[Instrument]:
        return SyntheticMarketDataProvider(self.universe).list_instruments()

    def list_positions(self) -> list[PositionSnapshot]:
        return []

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

    def _get_stock_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        if timeframe == "D1":
            payload = self._query(function="TIME_SERIES_DAILY_ADJUSTED", symbol=symbol, outputsize="compact")
            series = payload.get("Time Series (Daily)")
            return self._parse_daily_series(series, limit)

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
        return _aggregate_candles(candles, 4)[-limit:]

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

        payload = self._query(
            function="FX_INTRADAY",
            from_symbol=from_symbol,
            to_symbol=to_symbol,
            interval="60min",
            outputsize="compact",
        )
        series = payload.get("Time Series FX (60min)")
        candles = self._parse_intraday_series(series, limit * 4)
        return _aggregate_candles(candles, 4)[-limit:]

    def _query(self, **params: str) -> dict:
        payload = {
            **params,
            "apikey": self.config.alpha_vantage_api_key,
        }
        url = f"{self.config.alpha_vantage_base_url}?{parse.urlencode(payload)}"
        data = self.http_get_json(url, self.config.request_timeout_seconds)
        if "Error Message" in data:
            raise MarketDataError(data["Error Message"])
        if "Note" in data:
            raise MarketDataError(data["Note"])
        return data

    def _parse_daily_series(self, series: dict | None, limit: int) -> list[Candle]:
        if not series:
            raise MarketDataError("Missing daily time series payload.")
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
            raise MarketDataError("Missing intraday time series payload.")
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


def build_market_data_provider(config: MarketDataConfig, universe: UniverseConfig) -> SyntheticMarketDataProvider | AlphaVantageMarketDataProvider:
    if config.provider == "alpha_vantage":
        return AlphaVantageMarketDataProvider(config, universe)
    return SyntheticMarketDataProvider(universe)
