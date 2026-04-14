# Stock Research Assistant

Local Python assistant that screens a watchlist, estimates fair value, ranks undervalued companies, and sends research-first Telegram reports for faster company analysis.

## What It Does

- Screens a stock watchlist for quality-value opportunities
- Sends a ranked shortlist of the most interesting research ideas
- Builds a richer single-company research report with intrinsic value, peer context, thesis, catalysts, and key risks
- Supports Telegram commands for research, watchlist maintenance, portfolio moves, and compatibility aliases from the older bot
- Uses `yfinance` by default, with `synthetic` still available for local dry runs and tests
- Keeps the existing package/module names for compatibility while the product shifts fully to research-first wording

## Primary Commands

- `/top 3` or `top 3`: ranked research shortlist
- `/analyze MSFT`: full company research report
- `/watch NVDA`: add a company to the research watchlist
- `/portfolio`: daily move summary for holdings in `config/portfolio.txt`
- `/help`: command summary

Compatibility aliases are still accepted during the transition:

- `/tip` and `/tip MSFT`
- `analise MSFT` and `/analise MSFT`
- `add NVDA` and `/add NVDA`

## Research Output

Shortlist messages highlight:

- company and ticker
- fair value
- margin of safety
- business quality
- why the idea is interesting now
- main risk

Detailed company reports highlight:

- current price
- intrinsic value
- analyst target
- margin of safety
- business quality
- peer context
- stance such as `BUY`, `HOLD`, or `SELL`
- thesis
- catalysts
- what could go wrong
- watchlist status and suggested action

## Setup

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

2. Install the package in editable mode:

```powershell
python -m pip install -e .
```

3. Fill in the Telegram values in `.env`.

## Run

Run the tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Start the assistant:

```powershell
stock-research-assistant
```

Compatibility entrypoint:

```powershell
xtb-trading-bot
```

If console scripts are not on your shell path:

```powershell
.\.venv\Scripts\python.exe -m xtb_trading_bot
```

## Configuration Notes

- `BOT_STOCK_UNIVERSE_PATH` points to the research watchlist file
- `BOT_PORTFOLIO_PATH` points to owned holdings, separate from the watchlist
- `MARKET_DATA_PROVIDER=yfinance` is the default live-data path
- `BOT_ALLOWED_TIMEFRAMES` and the risk-oriented settings remain for compatibility with the older scoring engine
- `XTB_*` settings are deprecated legacy placeholders and are no longer part of the runtime flow

## Current Scope

This project no longer focuses on broker execution. It is now centered on research workflows for finding and reviewing undervalued stocks, with Telegram as the primary interface.
