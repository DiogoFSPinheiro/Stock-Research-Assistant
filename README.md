# Trading Bot

Local Python MVP for an XTB trading bot that scans only stocks and FX, proposes trades for manual Telegram approval, and runs safely against a demo account first.

## Features

- XTB integration layer with demo/live environment support
- Explicit instrument allowlist plus banned asset classes
- Trend-following signal engine for 4H/1D swing trading
- Conservative risk engine with drawdown and exposure guards
- Telegram approval workflow for approve/reject/list actions
- JSON persistence for signals, approvals, and executions
- Unit test suite covering filters, strategy, risk, Telegram flow, and orchestration

## Quick Start

1. Copy `.env.example` to `.env` and fill in your credentials.
2. Run the tests:

```powershell
python -m unittest discover -s tests
```

3. Run the bot:

```powershell
python -m xtb_trading_bot
```
