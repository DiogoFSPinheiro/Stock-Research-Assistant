from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
from pathlib import Path
import os


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _load_symbol_file(path: str | Path) -> tuple[str, ...]:
    if not str(path).strip():
        return ()
    file_path = Path(path)
    if not file_path.exists() or file_path.is_dir():
        return ()
    symbols: list[str] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line)
    return tuple(symbols)


def normalize_stock_symbol(symbol: str) -> str:
    return symbol.strip().strip('"').strip("'").upper()


def load_stock_universe(path: str | Path) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for symbol in _load_symbol_file(path):
        normalized = normalize_stock_symbol(symbol)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def append_stock_to_universe(path: str | Path, symbol: str) -> tuple[bool, str]:
    normalized = normalize_stock_symbol(symbol)
    if not normalized:
        raise ConfigError("Stock symbol cannot be empty.")

    file_path = Path(path)
    existing = load_stock_universe(file_path)
    if normalized in existing:
        return False, normalized

    file_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if file_path.exists() and file_path.read_text(encoding="utf-8").strip() else ""
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{normalized}\n")
    return True, normalized


def refresh_stock_universe(universe: "UniverseConfig") -> "UniverseConfig":
    file_stocks = load_stock_universe(universe.stock_universe_path)
    if not file_stocks:
        return universe
    if file_stocks == universe.allowed_stocks:
        return universe
    return replace(universe, allowed_stocks=file_stocks)


class ConfigError(ValueError):
    pass


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in {"replace-me", "changeme", "your-token", "your-chat-id"}


@dataclass(frozen=True)
class MarketDataConfig:
    provider: str
    alpha_vantage_api_key: str
    alpha_vantage_base_url: str
    request_timeout_seconds: int


@dataclass(frozen=True)
class RiskConfig:
    capital: float
    risk_per_trade: float
    max_open_positions: int
    max_daily_loss: float
    max_weekly_loss: float
    reward_to_risk: float
    signal_expiry_minutes: int


@dataclass(frozen=True)
class UniverseConfig:
    allowed_fx: tuple[str, ...]
    allowed_stocks: tuple[str, ...]
    stock_universe_path: Path
    context_symbols: tuple[str, ...]
    allowed_timeframes: tuple[str, ...]


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str
    polling_timeout_seconds: int
    polling_limit: int
    drop_pending_updates_on_start: bool


@dataclass(frozen=True)
class AppConfig:
    market_data: MarketDataConfig
    risk: RiskConfig
    universe: UniverseConfig
    telegram: TelegramConfig
    poll_seconds: int
    auto_scan_hour: int
    auto_scan_minute: int
    log_level: str
    storage_path: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        stock_universe_path = Path(os.getenv("BOT_STOCK_UNIVERSE_PATH", "config/stock_universe.txt"))
        file_stocks = load_stock_universe(stock_universe_path)
        return cls(
            market_data=MarketDataConfig(
                provider=os.getenv("MARKET_DATA_PROVIDER", "yfinance"),
                alpha_vantage_api_key=os.getenv("ALPHA_VANTAGE_API_KEY", ""),
                alpha_vantage_base_url=os.getenv("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query"),
                request_timeout_seconds=int(os.getenv("MARKET_DATA_REQUEST_TIMEOUT_SECONDS", "20")),
            ),
            risk=RiskConfig(
                capital=float(os.getenv("BOT_CAPITAL", "100000")),
                risk_per_trade=float(os.getenv("BOT_RISK_PER_TRADE", "0.005")),
                max_open_positions=int(os.getenv("BOT_MAX_OPEN_POSITIONS", "4")),
                max_daily_loss=float(os.getenv("BOT_MAX_DAILY_LOSS", "0.02")),
                max_weekly_loss=float(os.getenv("BOT_MAX_WEEKLY_LOSS", "0.05")),
                reward_to_risk=float(os.getenv("BOT_REWARD_TO_RISK", "2.0")),
                signal_expiry_minutes=int(os.getenv("BOT_SIGNAL_EXPIRY_MINUTES", "240")),
            ),
            universe=UniverseConfig(
                allowed_fx=tuple(_split_csv(os.getenv("BOT_ALLOWED_FX", "EURUSD,GBPUSD,USDJPY"))),
                allowed_stocks=file_stocks or tuple(_split_csv(os.getenv("BOT_ALLOWED_STOCKS", "AAPL,MSFT,NVDA"))),
                stock_universe_path=stock_universe_path,
                context_symbols=tuple(_split_csv(os.getenv("BOT_CONTEXT_SYMBOLS", "SPX500,GOLD"))),
                allowed_timeframes=tuple(_split_csv(os.getenv("BOT_ALLOWED_TIMEFRAMES", "H4,D1"))),
            ),
            telegram=TelegramConfig(
                bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
                polling_timeout_seconds=int(os.getenv("TELEGRAM_POLLING_TIMEOUT_SECONDS", "30")),
                polling_limit=int(os.getenv("TELEGRAM_POLLING_LIMIT", "25")),
                drop_pending_updates_on_start=_get_bool("TELEGRAM_DROP_PENDING_UPDATES_ON_START", False),
            ),
            poll_seconds=int(os.getenv("BOT_POLL_SECONDS", "300")),
            auto_scan_hour=int(os.getenv("BOT_AUTO_SCAN_HOUR", "9")),
            auto_scan_minute=int(os.getenv("BOT_AUTO_SCAN_MINUTE", "0")),
            log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            storage_path=Path(os.getenv("BOT_STORAGE_PATH", "data/state.json")),
        )

    def validate(self) -> None:
        if not self.telegram.bot_token or _is_placeholder(self.telegram.bot_token):
            raise ConfigError("TELEGRAM_BOT_TOKEN is required.")
        if not self.telegram.chat_id or _is_placeholder(self.telegram.chat_id):
            raise ConfigError("TELEGRAM_CHAT_ID is required.")
        if self.market_data.provider not in {"synthetic", "alpha_vantage", "yfinance"}:
            raise ConfigError("MARKET_DATA_PROVIDER must be one of: synthetic, alpha_vantage, yfinance.")
        if self.market_data.provider == "alpha_vantage" and (
            not self.market_data.alpha_vantage_api_key or _is_placeholder(self.market_data.alpha_vantage_api_key)
        ):
            raise ConfigError("ALPHA_VANTAGE_API_KEY is required when MARKET_DATA_PROVIDER=alpha_vantage.")
        if not self.universe.allowed_fx and not self.universe.allowed_stocks:
            raise ConfigError("At least one symbol must be configured in BOT_ALLOWED_FX or BOT_ALLOWED_STOCKS.")
        invalid_timeframes = [timeframe for timeframe in self.universe.allowed_timeframes if timeframe not in {"H4", "D1"}]
        if invalid_timeframes:
            raise ConfigError(f"Unsupported BOT_ALLOWED_TIMEFRAMES values: {', '.join(invalid_timeframes)}")
        if self.poll_seconds <= 0:
            raise ConfigError("BOT_POLL_SECONDS must be greater than 0.")
        if not 0 <= self.auto_scan_hour <= 23:
            raise ConfigError("BOT_AUTO_SCAN_HOUR must be between 0 and 23.")
        if not 0 <= self.auto_scan_minute <= 59:
            raise ConfigError("BOT_AUTO_SCAN_MINUTE must be between 0 and 59.")
