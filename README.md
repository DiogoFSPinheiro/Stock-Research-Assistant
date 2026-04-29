# Stock Research Assistant

![Language](https://img.shields.io/badge/language-Python%203.11%2B-blue)
![Interface](https://img.shields.io/badge/interface-Telegram-26A5E4)
![Market%20Data](https://img.shields.io/badge/market%20data-yfinance-0aa06e)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-555555)
![Focus](https://img.shields.io/badge/focus-Equity%20Research-gold)

A research-first Telegram assistant for screening companies, estimating fair value, ranking watchlist ideas, and summarizing owned portfolio moves.

## Overview

This project began as a market-ideas bot and has since been refocused into an equity-research workflow. The current product is designed to help identify attractive companies, generate concise research reports, and maintain a watchlist and portfolio view through Telegram.

The runtime no longer centers on trade execution. `/top` and `/tip` now publish research summaries, not trade signals, order proposals, entries, stop losses, or position sizes.

## Tech Stack

- **Language:** Python 3.11+
- **Interface:** Telegram bot
- **Market data:** `yfinance` by default
- **Persistence:** local JSON state
- **Packaging:** `setuptools`
- **Execution model:** local process or Linux service

## Core Capabilities

- Screen a stock watchlist for quality-value research ideas
- Rank current ideas by investment score, margin of safety, model confidence, data quality, and business quality
- Generate quick single-company research checks
- Generate full company research reports with valuation model breakdowns
- Summarize intrinsic value, valuation range, analyst target, business quality, catalysts, and key risks
- Maintain a research watchlist separately from an owned portfolio list
- Summarize daily portfolio moves from `config/portfolio.txt`

## Command Reference

### Research

- `/top 5`
  Return a ranked shortlist of current research ideas from the watchlist. The output highlights stance, investment score, intrinsic value, margin of safety, data quality, thesis, and main risk.

- `/tip`
  Return the single best research idea currently passing the screen.

- `/tip MSFT`
  Return a quick research check for one company. This is a compact version of `/analise`, with stance, valuation range, data quality, model confidence, thesis, risk, and suggested action.

- `/analise MSFT`
  Generate a full company research report with valuation models, peer context, catalysts, downside risk, and watchlist status.

- `/analyze MSFT`
  English alias for `/analise MSFT`.

- `/compare AAPL MSFT`
  Compare two to five companies by valuation, margin of safety, business quality, data quality, investment score, and main risk.

### Watchlist And Portfolio

- `/watch NVDA`
  Add a symbol to the research watchlist file.

- `/watchlist`
  Show the current research watchlist with stance, score, margin of safety, data quality, and main risk.

- `/portfolio`
  Show the current daily move summary for holdings in `config/portfolio.txt`.

- `/portfolio add MSFT 10 320.50`
  Add or replace a portfolio holding with symbol, quantity, and average cost.

- `/portfolio update MSFT 12 315.00`
  Update an existing holding's quantity and average cost.

- `/portfolio remove MSFT`
  Remove a holding from the portfolio.

- `/alert MSFT 15%`
  Create or update a research alert that triggers when margin of safety reaches the threshold.

- `/alerts`
  List active research alerts.

- `/unalert MSFT`
  Remove an active research alert.

- `/help`
  Display the available commands.

### Compatibility Aliases

These remain available:

- `top 5`
- `tips 5`
- `tip`
- `tip MSFT`
- `analise MSFT`
- `analyze MSFT`
- `analyse MSFT`
- `add NVDA`
- `/add NVDA`
- `watchlist`
- `compare AAPL MSFT`

## Research Output

### Top Shortlist

Each ranked idea includes:

- company name and ticker
- stance: `BUY`, `HOLD`, or `SELL`
- investment score
- intrinsic value estimate
- margin of safety
- business quality score
- data quality
- thesis summary
- main risk

### Quick Research Check

`/tip SYMBOL` includes:

- current price
- intrinsic value
- valuation range
- margin of safety
- data quality
- model confidence
- best and weakest valuation model
- thesis summary
- main risk
- suggested action

### Full Company Research Report

`/analise SYMBOL` includes:

- current price
- intrinsic value estimate
- valuation range
- quality-adjusted fair value
- analyst target
- margin of safety
- model confidence
- quality score
- peer context
- data quality summary
- investment score
- valuation model breakdown
- thesis
- catalysts
- downside / key-risk summary
- watchlist status and suggested action

## Project Structure

```text
src/stock_research_assistant/   Application code
config/                         Watchlist and portfolio files
data/                           Local JSON state
tests/                          Unit test suite
```

## Installation

### Windows (PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Configuration

Create a `.env` file in the repository root and populate the required Telegram values. See `.env.example` for the current baseline.

Important settings:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `BOT_STOCK_UNIVERSE_PATH`
- `BOT_PORTFOLIO_PATH`
- `MARKET_DATA_PROVIDER`
- `BOT_AUTO_SCAN_HOUR`
- `BOT_AUTO_SCAN_MINUTE`

Notes:

- `BOT_STOCK_UNIVERSE_PATH` points to the research watchlist file.
- `BOT_ALLOWED_STOCKS` is only used as a fallback when the watchlist file is empty or missing.
- `BOT_PORTFOLIO_PATH` points to owned holdings and is intentionally separate from the research watchlist.
- Portfolio rows can be either `SYMBOL` or `SYMBOL,quantity,avg_cost`; the richer form enables P/L reporting.
- `MARKET_DATA_PROVIDER=yfinance` is the default live-data path for research reports.
- `MARKET_DATA_PROVIDER=synthetic` is useful for local dry runs and tests.

## Running The Assistant

### Start The Application

Preferred entrypoint after installing the package:

```powershell
stock-research-assistant
```

Direct module execution:

```powershell
python -m stock_research_assistant
```

If the console command is missing, reinstall the local package:

```powershell
python -m pip install -e .
```

### Run The Test Suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Deployment Notes

The assistant can be hosted on a Linux server or cloud VM such as a DigitalOcean Droplet.

Typical production setup:

1. Clone the repository.
2. Create a virtual environment.
3. Install the package with `pip install -e .`.
4. Add the production `.env`.
5. Run the assistant under `systemd` for automatic restart and startup on boot.

The application does not require an inbound web port for normal operation. It primarily needs outbound internet access for Telegram and market data.

## Scope And Positioning

This repository is best understood as a personal equity-research assistant rather than an execution bot. It is optimized for:

- screening for undervaluation
- generating compact research views
- keeping a working watchlist
- reviewing owned positions at a glance

It is not intended to be a fully automated brokerage execution system in its current form.
