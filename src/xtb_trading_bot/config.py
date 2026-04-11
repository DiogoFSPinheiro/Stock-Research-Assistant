from __future__ import annotations

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


@dataclass(frozen=True)
class XtbConfig:
    account_mode: str
    user_id: str
    password: str
    host: str
    port: int
    use_tls: bool


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
    context_symbols: tuple[str, ...]
    allowed_timeframes: tuple[str, ...]


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class AppConfig:
    xtb: XtbConfig
    risk: RiskConfig
    universe: UniverseConfig
    telegram: TelegramConfig
    poll_seconds: int
    log_level: str
    storage_path: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            xtb=XtbConfig(
                account_mode=os.getenv("XTB_ACCOUNT_MODE", "demo"),
                user_id=os.getenv("XTB_USER_ID", ""),
                password=os.getenv("XTB_PASSWORD", ""),
                host=os.getenv("XTB_HOST", "ws.xtb.com"),
                port=int(os.getenv("XTB_PORT", "5124")),
                use_tls=_get_bool("XTB_USE_TLS", True),
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
                allowed_stocks=tuple(_split_csv(os.getenv("BOT_ALLOWED_STOCKS", "AAPL,MSFT,NVDA"))),
                context_symbols=tuple(_split_csv(os.getenv("BOT_CONTEXT_SYMBOLS", "SPX500,GOLD"))),
                allowed_timeframes=tuple(_split_csv(os.getenv("BOT_ALLOWED_TIMEFRAMES", "H4,D1"))),
            ),
            telegram=TelegramConfig(
                bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            ),
            poll_seconds=int(os.getenv("BOT_POLL_SECONDS", "300")),
            log_level=os.getenv("BOT_LOG_LEVEL", "INFO"),
            storage_path=Path(os.getenv("BOT_STORAGE_PATH", "data/state.json")),
        )
