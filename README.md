# Trading Bot

Local Python bot that scans a stock universe from a file, estimates fair value using quality-plus-valuation heuristics, ranks the best candidates, and sends a Telegram shortlist of stocks that look undervalued without falling into obvious value traps.

## Features

- Stock-only quality-value screener with a ranked top-3 shortlist per scheduled cycle
- Fair value model using earnings yield, free-cash-flow yield, margins, growth, and leverage
- Hard rejection filters for missing core valuation data, weak profitability, and excessive leverage
- Technical overlay used for timing and entry confirmation instead of defining undervaluation
- Telegram delivery with fair value, margin of safety, quality score, timing score, and risk flags
- On-demand Telegram tip requests with `/tip`, `/tip MSFT`, `/top 3`, or `give a tip`
- On-demand deep stock analysis with `Analise MSFT` or `/analise MSFT`
- On-demand portfolio daily-performance report with `portfolio` or `/portfolio`
- Runtime stock universe updates without restarting the bot
- JSON persistence for picks and analysis history
- Unit test suite covering valuation math, filters, ranking logic, Telegram flow, and orchestration

## Runtime Expectations

- The bot is designed to run locally as a background process.
- Telegram is the primary user interface for receiving the shortlist and single-name ideas.
- The default scheduled scan sends the top 3 quality-value ideas instead of one forced pick.
- Send `/tip` in Telegram whenever you want an immediate fresh single-stock idea without waiting for the next scheduled cycle.
- Send `/tip MSFT` to force a fresh analysis for one ticker.
- Send `/top 3` to request a ranked shortlist on demand.
- Send `Analise MSFT` to run a compact multi-model stock analysis and add the ticker to your watch universe.
- Send `portfolio` or `/portfolio` to get the day move of the stocks listed in `config/portfolio.txt`.
- Send `add NVDA` or `/add NVDA` in Telegram to append a stock to the universe file and use it on the next scan.
- After the U.S. market close, the bot can also send the portfolio report automatically once per trading day.
- Market data is required for stocks; the project does not execute trades.
- The code is designed to use `yfinance` by default for market data, so you can run it without a paid API key.
- `synthetic` remains available for local dry runs and reproducible tests.
- XTB-specific execution settings are deprecated leftovers and not part of the runtime flow.
- Use `BOT_MODE=signal_only` to make the intent explicit in the environment file.

## Setup

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

If `python` is not available on your Windows machine, install Python 3.11+ from python.org and reopen PowerShell before continuing.

2. Install the package in editable mode from the repository root:

```powershell
python -m pip install -e .
```

3. A `.env` file is already present in the project root. Fill in the Telegram values. No paid market data key is required when you keep the default `yfinance` provider.

## Run

Run the tests:

```powershell
python -m unittest discover -s tests -v
```

Start the bot with the installed console script:

```powershell
xtb-trading-bot
```

If the console script is not available in your shell, run it directly from the project virtualenv:

```powershell
.\.venv\Scripts\python.exe -m xtb_trading_bot
```

If you prefer module execution after installation, this also works:

```powershell
python -m xtb_trading_bot
```

Once the bot is running, open Telegram and use:

- `/top 3` for the ranked shortlist
- `/tip` for one best current idea
- `/tip MSFT` for a single-ticker check
- `Analise MSFT` for FCF, DCF, intrinsic value, options, peer benchmark, and buy/hold/sell
- `portfolio` for the current daily move of your owned stocks
- `/add NVDA` to add a stock to the universe

## Configuration

- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are required for delivery.
- Use `/tip`, `/tip SYMBOL`, or `/top N` in Telegram to trigger immediate scans.
- Use `Analise SYMBOL` or `/analise SYMBOL` to run a compact stock analysis and auto-add valid tickers to the watch universe.
- Use `portfolio` or `/portfolio` to read holdings from `config/portfolio.txt` and get a daily move summary.
- You can edit the stock universe file while the bot is running; it reloads the file automatically before scans.
- `BOT_PORTFOLIO_PATH` points to the portfolio file, which defaults to `config/portfolio.txt`.
- `MARKET_DATA_PROVIDER=yfinance` is the default live-data path; `synthetic` is still available for local dry runs.
- `BOT_ALLOWED_STOCKS` defines the stock universe the picker will rank.
- `BOT_STOCK_UNIVERSE_PATH` points to the text file containing the stock universe, one ticker per line.
- `BOT_CONTEXT_SYMBOLS` is optional and can be used for market regime context only.
- `BOT_ALLOWED_FX` is no longer used by the default runtime flow.
- `XTB_*` variables are deprecated placeholders and should not be used.

## Output

Each shortlisted stock now includes:

- entry price
- estimated fair value
- margin of safety
- quality score
- timing score
- expected return and probability up
- risk flags and a short rationale

The `Analise SYMBOL` command includes:

- current price
- `FCF model`
- `DCF model`
- `Intrinsic value`
- analyst target
- options trader sentiment
- peer benchmark summary
- final `BUY`, `HOLD`, or `SELL`

The `portfolio` command includes:

- current price
- previous close
- daily percentage move
- a short possible reason for the move
