# Trading Bot

Local Python MVP for a signal-only Telegram bot that scans stocks and FX, proposes trade ideas for manual review, and no longer sends orders to XTB or any other broker.

## Features

- Signal-only architecture with no direct broker execution
- Explicit instrument allowlist plus banned asset classes
- Trend-following signal engine for 4H/1D swing trading
- Conservative risk engine with drawdown and exposure guards
- Telegram workflow for approve/reject/list actions
- JSON persistence for signals, approvals, and outcomes
- Unit test suite covering filters, strategy, risk, Telegram flow, and orchestration

## Runtime Expectations

- The bot is designed to run locally as a background process.
- Telegram is the primary user interface for incoming signals and approvals.
- Market data is required for stocks and FX; the project does not execute trades.
- The code supports `synthetic` market data for local development and `alpha_vantage` for live candles/quotes.
- XTB-specific execution settings are deprecated and kept only for compatibility with older configs.
- Use `BOT_MODE=signal_only` to make the intent explicit in the environment file.

## Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials.
2. Configure a market data source and Telegram bot token/chat ID.
3. Run the tests:

```powershell
python -m unittest discover -s tests
```

4. Run the bot:

```powershell
python -m xtb_trading_bot
```

## Configuration Notes

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required for signal delivery.
- `MARKET_DATA_PROVIDER=synthetic` is the simplest local mode; switch to `alpha_vantage` and set `ALPHA_VANTAGE_API_KEY` for live data.
- `BOT_ALLOWED_FX` and `BOT_ALLOWED_STOCKS` define the tradable universe the signal engine will scan.
- `BOT_CONTEXT_SYMBOLS` is optional and can be used for market regime context only.
- `XTB_*` variables are retained as deprecated placeholders and should not be used for execution.
