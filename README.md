# ✈️ Elite Flight Deal Hunter

An AI agent that monitors airfare continuously, predicts price movements, and tells you exactly when to **BUY NOW** or **WAIT**.

Built with the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) on **Groq's free tier** (`openai/gpt-oss-120b`).

## Features

| Capability | Description |
|---|---|
| 🔍 Multi-source Search | Amadeus · Google Flights · Kiwi (mock fallback when keys not set) |
| 📊 Price Intelligence | 7/30/90-day averages · percentile ranking |
| 🎯 Buy Score | 0–100 algorithm → BUY NOW / WAIT / EXPENSIVE |
| 🔮 Price Prediction | Probability prices rise or fall based on history |
| 🗓️ Flexible Dates | ±3/7/14 day cheapest-date search |
| 💰 Miles Intelligence | Smiles · LATAM Pass · Azul Fidelidade |
| ⚠️ Error Fare Detector | Flags mistake fares automatically |
| 🔔 Alerts | Telegram + Email when score ≥ 85 |
| ⏰ Scheduler | Daily 08:00 + every 6h + drop detector every 2h |

## Quick Start

### 1. Clone & set up environment

```bash
git clone https://github.com/hanaeoguino/elite-flight-deal-hunter
cd elite-flight-deal-hunter

python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

pip install openai-agents python-dotenv requests apscheduler
```

### 2. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```env
GROQ_API_KEY=gsk_...          # required — free at console.groq.com
OPENAI_API_KEY=sk-...         # optional

AMADEUS_API_KEY=...           # optional — test.developer.amadeus.com
AMADEUS_API_SECRET=...
SERPAPI_KEY=...               # optional — serpapi.com
KIWI_API_KEY=...              # optional — tequila.kiwi.com

TELEGRAM_BOT_TOKEN=...        # optional — for deal alerts
TELEGRAM_CHAT_ID=...
EMAIL_SENDER=...              # optional — Gmail sender
EMAIL_PASSWORD=...
EMAIL_RECIPIENT=...
```

> Without flight API keys the agent uses realistic mock data automatically.

### 3. Run

```bash
# Windows (required for emoji output)
$env:PYTHONUTF8 = "1"
python run_notebook.py
```

Or open `elite_flight_deal_hunter.ipynb` in Jupyter / VS Code.

## Usage

```python
# Overview dashboard
ask("Show me an overview of my flight hunter")

# Search flights
ask("Search for flights from GRU to CDG on 2026-09-15")

# Buy recommendation
ask("Should I buy a GRU to LHR ticket for R$2,950?")

# Flexible dates
ask("Find the cheapest dates to fly GRU to Paris in September 2026, plus or minus 7 days")

# Miles vs cash
ask("I have 80000 Smiles miles. Compare cash vs miles for a R$3800 ticket GRU to CDG")

# Watchlist
ask("Monitor Sao Paulo to Paris in September 2026")

# Best destination
ask("Find the cheapest European destination from Sao Paulo for late September 2026. Check CDG, LHR, FCO, and AMS.")
```

## Buy Score Guide

| Score | Verdict | Meaning |
|---|---|---|
| 90–100 | 🟢 BUY NOW | Near historical minimum or 20%+ below average |
| 70–89 | 🟡 GOOD OPPORTUNITY | Below average, solid time to book |
| 40–69 | 🟠 WAIT | Average price, may drop |
| 0–39 | 🔴 EXPENSIVE | Do not buy |

## Project Structure

```
elite_flight_deal_hunter.ipynb   # main notebook (34 cells)
run_notebook.py                  # extracted runner (no Jupyter needed)
util_logging.py                  # colored logger with file rotation
flight_sources/
  amadeus.py                     # Amadeus flight offers API
  google_flights.py              # Google Flights via SerpAPI
  kiwi.py                        # Kiwi / Tequila API
.env.example                     # API key template
```

## Requirements

- Python 3.12+
- Groq API key (free tier — [console.groq.com](https://console.groq.com))
- Flight API keys are optional (mock data used as fallback)
