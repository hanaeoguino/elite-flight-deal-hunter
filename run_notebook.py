# ── Cell: 04-imports ──────────────────────────────────────────────
import os
import sys
import json
import sqlite3
import asyncio
import smtplib
import random
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from typing import Optional

import requests
from dotenv import load_dotenv
from openai import AsyncOpenAI
from agents import Agent, Runner, function_tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

load_dotenv()

logging.basicConfig(level=logging.WARNING)
print('âœ… Imports OK')
print(f'   Python {sys.version.split()[0]}')

# ── Cell: 06-profile ──────────────────────────────────────────────
USER_PROFILE_FILE = 'user_profile.json'

DEFAULT_PROFILE = {
    'origins': ['GRU', 'CGH', 'VCP'],
    'destinations': ['CDG', 'ORY', 'LHR', 'FCO'],
    'max_budget_brl': 5000,
    'preferred_airlines': ['LATAM', 'Air France', 'TAP'],
    'avoid_airlines': [],
    'cabin_class': 'ECONOMY',
    'bags': 1,
    'miles_programs': {
        'smiles': {'balance': 0, 'tier': 'gold'},
        'latam_pass': {'balance': 0, 'tier': 'black'},
        'azul_fidelidade': {'balance': 0, 'tier': 'topazio'}
    },
    'credit_cards': [
        {'name': 'Nubank Ultravioleta', 'points_balance': 0, 'program': 'Nubank Rewards'}
    ],
    'monitoring': []
}

def load_profile() -> dict:
    if os.path.exists(USER_PROFILE_FILE):
        with open(USER_PROFILE_FILE, encoding='utf-8') as f:
            return json.load(f)
    save_profile(DEFAULT_PROFILE)
    return DEFAULT_PROFILE.copy()

def save_profile(profile: dict):
    with open(USER_PROFILE_FILE, 'w', encoding='utf-8') as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

profile = load_profile()
print('âœ… User profile loaded')
print(f'   Origins     : {", ".join(profile["origins"])}')
print(f'   Destinations: {", ".join(profile["destinations"])}')
print(f'   Max budget  : R${profile["max_budget_brl"]:,.0f}')

# ── Cell: 08-database ──────────────────────────────────────────────
DB_FILE = 'flights.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS flight_search_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            route         TEXT    NOT NULL,
            departure_date TEXT,
            return_date   TEXT,
            checked_at    TEXT    DEFAULT CURRENT_TIMESTAMP,
            price         REAL,
            currency      TEXT    DEFAULT "BRL",
            airline       TEXT,
            source        TEXT,
            url           TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS price_statistics (
            route          TEXT PRIMARY KEY,
            average_7_days  REAL,
            average_30_days REAL,
            minimum_found   REAL,
            maximum_found   REAL,
            last_price      REAL,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def save_flight_to_db(route, departure_date, return_date, price, currency, airline, source, url):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO flight_search_history
        (route, departure_date, return_date, price, currency, airline, source, url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (route, departure_date, return_date, price, currency, airline, source, url))

    c.execute('SELECT minimum_found, maximum_found FROM price_statistics WHERE route=?', (route,))
    row = c.fetchone()
    if row:
        c.execute('''
            UPDATE price_statistics
            SET last_price=?, minimum_found=?, maximum_found=?, updated_at=CURRENT_TIMESTAMP
            WHERE route=?
        ''', (price, min(row[0] or price, price), max(row[1] or price, price), route))
    else:
        c.execute('''
            INSERT INTO price_statistics (route, minimum_found, maximum_found, last_price)
            VALUES (?, ?, ?, ?)
        ''', (route, price, price, price))

    conn.commit()
    conn.close()

def get_price_stats(route: str) -> dict:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now = datetime.now()

    def avg_since(days):
        since = (now - timedelta(days=days)).isoformat()
        c.execute('SELECT AVG(price) FROM flight_search_history WHERE route=? AND checked_at>=?', (route, since))
        return c.fetchone()[0]

    c.execute('SELECT MIN(price), MAX(price) FROM flight_search_history WHERE route=?', (route,))
    min_p, max_p = c.fetchone()
    c.execute('SELECT price FROM flight_search_history WHERE route=? ORDER BY price', (route,))
    all_prices = [r[0] for r in c.fetchall()]
    conn.close()

    return {
        'avg_7_days': avg_since(7),
        'avg_30_days': avg_since(30),
        'avg_90_days': avg_since(90),
        'historical_min': min_p,
        'historical_max': max_p,
        'all_prices': all_prices,
    }

init_db()
print(f'âœ… Database ready: {DB_FILE}')

# ── Cell: 10-amadeus ──────────────────────────────────────────────
# â”€â”€ Amadeus â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_amadeus_token_cache = {'token': None, 'expires_at': 0}

def _amadeus_token() -> str:
    now = datetime.now().timestamp()
    if _amadeus_token_cache['token'] and now < _amadeus_token_cache['expires_at'] - 60:
        return _amadeus_token_cache['token']
    env = os.getenv('AMADEUS_ENV', 'test')
    base = 'https://test.api.amadeus.com' if env == 'test' else 'https://api.amadeus.com'
    resp = requests.post(f'{base}/v1/security/oauth2/token', data={
        'grant_type': 'client_credentials',
        'client_id': os.getenv('AMADEUS_API_KEY'),
        'client_secret': os.getenv('AMADEUS_API_SECRET'),
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _amadeus_token_cache.update({'token': data['access_token'], 'expires_at': now + data['expires_in']})
    return data['access_token']

def search_amadeus(origin, destination, departure_date, return_date=None,
                   adults=1, cabin='ECONOMY', currency='BRL', max_r=10):
    env = os.getenv('AMADEUS_ENV', 'test')
    base = 'https://test.api.amadeus.com' if env == 'test' else 'https://api.amadeus.com'
    params = {
        'originLocationCode': origin, 'destinationLocationCode': destination,
        'departureDate': departure_date, 'adults': adults,
        'travelClass': cabin, 'currencyCode': currency, 'max': max_r,
    }
    if return_date:
        params['returnDate'] = return_date
    resp = requests.get(f'{base}/v2/shopping/flight-offers',
                        headers={'Authorization': f'Bearer {_amadeus_token()}'},
                        params=params, timeout=15)
    resp.raise_for_status()
    results = []
    for offer in resp.json().get('data', []):
        itin = offer['itineraries'][0]
        segs = itin['segments']
        s0, sN = segs[0], segs[-1]
        results.append({
            'airline': s0['carrierCode'], 'flight_number': f"{s0['carrierCode']}{s0['number']}",
            'origin': s0['departure']['iataCode'], 'destination': sN['arrival']['iataCode'],
            'departure': s0['departure']['at'][:16], 'arrival': sN['arrival']['at'][:16],
            'duration': itin['duration'], 'stops': len(segs) - 1,
            'bags': 0, 'price': float(offer['price']['grandTotal']),
            'currency': currency, 'url': 'https://www.amadeus.com/', 'source': 'amadeus',
        })
    return results

print('âœ… Amadeus source loaded')

# ── Cell: 11-google-flights ──────────────────────────────────────────────
# â”€â”€ Google Flights via SerpAPI â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def search_google_flights(origin, destination, departure_date, return_date=None, currency='BRL'):
    api_key = os.getenv('SERPAPI_KEY')
    if not api_key:
        raise ValueError('SERPAPI_KEY not set')
    params = {
        'engine': 'google_flights', 'departure_id': origin, 'arrival_id': destination,
        'outbound_date': departure_date, 'currency': currency, 'hl': 'pt',
        'type': '1' if return_date else '2', 'api_key': api_key,
    }
    if return_date:
        params['return_date'] = return_date
    resp = requests.get('https://serpapi.com/search.json', params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for flight in data.get('best_flights', []) + data.get('other_flights', []):
        leg = (flight.get('flights') or [{}])[0]
        dur = flight.get('total_duration', 0)
        results.append({
            'airline': leg.get('airline', 'Unknown'),
            'flight_number': leg.get('flight_number', ''),
            'origin': origin, 'destination': destination,
            'departure': leg.get('departure_airport', {}).get('time', departure_date),
            'arrival': leg.get('arrival_airport', {}).get('time', ''),
            'duration': f'{dur // 60}h{dur % 60:02d}m',
            'stops': len(flight.get('flights', [])) - 1,
            'bags': 1, 'price': float(flight.get('price', 0)),
            'currency': currency,
            'url': f'https://www.google.com/flights?hl=pt#flt={origin}.{destination}.{departure_date}',
            'source': 'google_flights',
        })
    return results

print('âœ… Google Flights source loaded')

# ── Cell: 12-kiwi ──────────────────────────────────────────────
# â”€â”€ Kiwi / Tequila API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def search_kiwi(origin, destination, departure_date, return_date=None, currency='BRL', max_r=10):
    api_key = os.getenv('KIWI_API_KEY')
    if not api_key:
        raise ValueError('KIWI_API_KEY not set')
    dep = datetime.strptime(departure_date, '%Y-%m-%d')
    params = {
        'fly_from': origin, 'fly_to': destination,
        'date_from': dep.strftime('%d/%m/%Y'),
        'date_to': (dep + timedelta(days=1)).strftime('%d/%m/%Y'),
        'curr': currency, 'limit': max_r, 'sort': 'price',
    }
    if return_date:
        ret = datetime.strptime(return_date, '%Y-%m-%d')
        params['return_from'] = ret.strftime('%d/%m/%Y')
        params['return_to'] = (ret + timedelta(days=1)).strftime('%d/%m/%Y')
    resp = requests.get('https://api.tequila.kiwi.com/v2/search',
                        headers={'apikey': api_key}, params=params, timeout=15)
    resp.raise_for_status()
    results = []
    for f in resp.json().get('data', []):
        route = f.get('route', [{}])
        s0 = route[0] if route else {}
        dur_s = f.get('duration', {}).get('departure', 0)
        results.append({
            'airline': s0.get('airline', 'Unknown'),
            'flight_number': s0.get('flight_no', ''),
            'origin': f.get('flyFrom', origin), 'destination': f.get('flyTo', destination),
            'departure': f.get('local_departure', departure_date)[:16],
            'arrival': f.get('local_arrival', '')[:16],
            'duration': f'{dur_s // 3600}h{(dur_s % 3600) // 60:02d}m',
            'stops': len(route) - 1, 'bags': 1,
            'price': float(f.get('price', 0)), 'currency': currency,
            'url': f.get('deep_link', 'https://www.kiwi.com/'), 'source': 'kiwi',
        })
    return results

print('âœ… Kiwi source loaded')

# ── Cell: 13-unified-search ──────────────────────────────────────────────
# â”€â”€ Unified search with mock fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MOCK_AIRLINES = [
    ('LATAM Airlines', 'LA', 2800),
    ('Air France',     'AF', 3100),
    ('TAP Portugal',   'TP', 2600),
]

def _mock_flights(origin, destination, departure_date):
    results = []
    for name, code, typical in MOCK_AIRLINES[:2]:   # 2 airlines = half the token output
        price = round(typical * random.uniform(0.75, 1.35), 2)
        results.append({
            'airline': name, 'flight_number': f'{code}{random.randint(100, 999)}',
            'origin': origin, 'destination': destination,
            'departure': f'{departure_date}T{random.randint(6, 22):02d}:00',
            'arrival': '', 'duration': f'{random.randint(12, 16)}h00m',
            'stops': random.choice([0, 1]), 'bags': 1, 'price': price, 'currency': 'BRL',
            'url': f'https://example.com/flights/{origin}-{destination}', 'source': 'mock',
        })
    return results

def search_all_sources(origin, destination, departure_date, return_date=None):
    results = []
    errors = []
    for name, fn in [('Amadeus', search_amadeus),
                     ('Google Flights', search_google_flights),
                     ('Kiwi', search_kiwi)]:
        try:
            results.extend(fn(origin, destination, departure_date, return_date))
        except Exception as e:
            errors.append(f'{name}: {type(e).__name__}')
    if not results:
        print(f'   âš¡ Using mock data ({", ".join(errors)})')
        results = _mock_flights(origin, destination, departure_date)
    seen, unique = set(), []
    for f in results:
        key = (f.get('airline'), round(f.get('price', 0)), f.get('departure', '')[:10])
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return sorted(unique, key=lambda f: f.get('price', float('inf')))

print('âœ… Unified search ready (Amadeus + Google Flights + Kiwi + mock fallback)')

# ── Cell: 15-price-intelligence ──────────────────────────────────────────────
def calculate_percentile(price: float, all_prices: list) -> float:
    """% of historical prices that are MORE expensive than current price."""
    if not all_prices:
        return 50.0
    return round(sum(1 for p in all_prices if p > price) / len(all_prices) * 100, 1)

def calculate_buy_score(current_price: float, stats: dict) -> dict:
    """0â€“100 score: 90+ = BUY NOW, 70â€“89 = GOOD, 40â€“69 = WAIT, 0â€“39 = EXPENSIVE."""
    score = 40
    reasons = []
    avg30 = stats.get('avg_30_days') or current_price
    hist_min = stats.get('historical_min') or current_price
    all_prices = stats.get('all_prices', [])
    percentile = calculate_percentile(current_price, all_prices)

    # Factor 1 â€” vs historical minimum
    if hist_min > 0:
        ratio = current_price / hist_min
        if ratio <= 1.03:
            score += 35
            reasons.append('At / near historical minimum')
        elif ratio <= 1.10:
            score += 20
            reasons.append('Close to historical minimum')
        elif ratio >= 1.30:
            score -= 15
            reasons.append('Above historical minimum')

    # Factor 2 â€” vs 30-day average
    if avg30 > 0:
        pct = (avg30 - current_price) / avg30 * 100
        if pct >= 25:
            score += 25
            reasons.append(f'{pct:.0f}% below 30-day average')
        elif pct >= 10:
            score += 12
            reasons.append(f'{pct:.0f}% below 30-day average')
        elif pct <= -15:
            score -= 15
            reasons.append('Above average price')

    # Factor 3 â€” percentile
    if all_prices:
        if percentile >= 90:
            score += 15
            reasons.append(f'Cheaper than {percentile:.0f}% of all historical fares')
        elif percentile >= 75:
            score += 8
            reasons.append(f'Cheaper than {percentile:.0f}% of historical fares')
        elif percentile <= 25:
            score -= 10
            reasons.append('More expensive than usual')

    score = max(0, min(100, score))

    if score >= 90:
        rec, label = 'ðŸŸ¢ BUY NOW', 'EXCELLENT DEAL'
    elif score >= 70:
        rec, label = 'ðŸŸ¡ GOOD OPPORTUNITY', 'GOOD DEAL'
    elif score >= 40:
        rec, label = 'ðŸŸ  WAIT', 'AVERAGE PRICE'
    else:
        rec, label = 'ðŸ”´ EXPENSIVE', 'OVERPRICED'

    return {'score': score, 'recommendation': rec, 'label': label,
            'reasons': reasons, 'percentile': percentile}

def predict_price_movement(route: str, days_ahead: int = 7) -> dict:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT price FROM flight_search_history WHERE route=? ORDER BY checked_at DESC LIMIT 30', (route,))
    prices = [r[0] for r in c.fetchall()]
    conn.close()

    if len(prices) < 4:
        return {
            'prediction': 'INSUFFICIENT_DATA',
            'probability_increase': 0.5, 'probability_decrease': 0.5,
            'expected_change': 0,
            'message': 'Not enough history for prediction â€” keep searching to build data.'
        }

    recent = sum(prices[:3]) / 3
    older = sum(prices[-3:]) / 3
    trend = recent - older
    volatility = sum(abs(prices[i] - prices[i+1]) for i in range(len(prices)-1)) / (len(prices)-1)

    if trend > volatility * 0.4:
        p_up, p_down = 0.72, 0.28
    elif trend < -volatility * 0.4:
        p_up, p_down = 0.28, 0.72
    else:
        p_up, p_down = 0.50, 0.50

    expected = trend * (days_ahead / 7)
    if expected < 0:
        msg = f'If you wait {days_ahead} days: Expected saving R${abs(expected):.0f} | Risk of increase: {p_up*100:.0f}%'
    else:
        msg = f'If you wait {days_ahead} days: Expected increase R${expected:.0f} | Risk: {p_up*100:.0f}% â€” consider buying now'

    return {'prediction': 'INCREASING' if trend > 0 else 'DECREASING',
            'probability_increase': p_up, 'probability_decrease': p_down,
            'expected_change': round(expected, 2), 'message': msg}

print('âœ… Price Intelligence Engine ready')

# ── Cell: 17-features ──────────────────────────────────────────────
# â”€â”€ Flexible Date Hunter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _find_flexible_dates(origin, destination, base_departure, return_date=None, flexibility=5):
    flexibility = min(flexibility, 5)  # cap at Â±5 days to stay within free-tier token limits
    base = datetime.strptime(base_departure, '%Y-%m-%d')
    candidates = []
    for delta in range(-flexibility, flexibility + 1):
        dep = (base + timedelta(days=delta)).strftime('%Y-%m-%d')
        try:
            flights = search_all_sources(origin, destination, dep, return_date)
            if flights:
                best = min(flights, key=lambda f: f.get('price', float('inf')))
                candidates.append({'dep_date': dep, 'delta': delta,
                                   'price': best.get('price', 0),
                                   'airline': best.get('airline', '?')})
        except Exception:
            pass
    if not candidates:
        return {'message': 'No flights found in flexible date range', 'options': []}
    return {'options': sorted(candidates, key=lambda f: f['price'])[:5]}

# â”€â”€ Airport Optimization â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
AIRPORT_GROUPS = {
    'SAO': ['GRU', 'CGH', 'VCP'],
    'PAR': ['CDG', 'ORY'],
    'LON': ['LHR', 'LGW', 'STN'],
    'ROM': ['FCO', 'CIA'],
    'AMS': ['AMS'],
    'MIL': ['MXP', 'LIN', 'BGY'],
}

def get_city_airports(code):
    code = code.upper()
    if code in AIRPORT_GROUPS:
        return AIRPORT_GROUPS[code]
    for airports in AIRPORT_GROUPS.values():
        if code in airports:
            return airports
    return [code]

def search_all_airports(origin_code, dest_code, departure_date, return_date=None):
    # Only expand destination — keep the exact origin the user specified to limit API calls
    dests = get_city_airports(dest_code)
    all_results = []
    for dest in dests:
        all_results.extend(search_all_sources(origin_code, dest, departure_date, return_date))
    return sorted(all_results, key=lambda f: f.get('price', float('inf')))

# â”€â”€ Miles Intelligence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MILES_PROGRAMS = {
    'smiles':           {'name': 'Smiles (GOL)',     'brl_per_mile': 0.020},
    'latam_pass':       {'name': 'LATAM Pass',       'brl_per_mile': 0.018},
    'azul_fidelidade':  {'name': 'Azul Fidelidade',  'brl_per_mile': 0.022},
}

def recommend_payment_method(cash_price: float, profile: dict) -> dict:
    best = {'method': 'BUY CASH', 'cost_brl': cash_price, 'miles_used': 0, 'savings_brl': 0}
    for prog_id, prog_info in MILES_PROGRAMS.items():
        balance = profile.get('miles_programs', {}).get(prog_id, {}).get('balance', 0)
        if balance <= 0:
            continue
        rate = prog_info['brl_per_mile']
        miles_needed = int(cash_price / rate)
        if balance >= miles_needed:
            option = {'method': f'USE MILES ({prog_info["name"]})',
                      'cost_brl': 0.0, 'miles_used': miles_needed,
                      'savings_brl': cash_price, 'balance_after': balance - miles_needed}
        else:
            cash_remaining = cash_price - balance * rate
            option = {'method': f'PARTIAL MILES ({prog_info["name"]})',
                      'cost_brl': max(0, cash_remaining),
                      'miles_used': balance, 'savings_brl': balance * rate,
                      'balance_after': 0}
        if option['cost_brl'] < best['cost_brl']:
            best = option
    return best

# â”€â”€ Error Fare Detector â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
LONG_HAUL_ROUTES = ['GRU-CDG', 'GRU-ORY', 'GRU-LHR', 'GRU-FCO', 'CGH-CDG', 'VCP-LHR']
FLOOR_PRICES = {'long_haul_brl': 1800, 'europe_brl': 1500}

def detect_error_fare(current_price: float, route: str, stats: dict) -> dict:
    avg30 = stats.get('avg_30_days') or 0
    hist_min = stats.get('historical_min') or current_price
    alerts = []
    confidence = 0
    if avg30 > 0:
        pct_below = (avg30 - current_price) / avg30 * 100
        if pct_below >= 50:
            confidence += 50
            alerts.append(f'{pct_below:.0f}% below 30-day average (R${avg30:,.0f})')
    if hist_min > 0 and current_price < hist_min * 0.65:
        confidence += 40
        alerts.append(f'{((hist_min-current_price)/hist_min*100):.0f}% below historical minimum (R${hist_min:,.0f})')
    if route.upper() in LONG_HAUL_ROUTES and current_price < FLOOR_PRICES['long_haul_brl']:
        confidence += 30
        alerts.append(f'Below floor price for long-haul route (R${current_price:,.0f} < R${FLOOR_PRICES["long_haul_brl"]:,})')
    is_error = confidence >= 50
    return {
        'is_error_fare': is_error, 'confidence': min(100, confidence), 'alerts': alerts,
        'message': (f'POSSIBLE ERROR FARE (confidence {min(100,confidence)}%)\n'
                    f'Normal: R${avg30:,.0f} -> Found: R${current_price:,.0f}\n'
                    + '\n'.join(alerts)) if is_error else 'Normal fare'
    }

print('Features ready')

# ── Cell: 19-notifications ──────────────────────────────────────────────
def _deal_message(flight: dict, buy_score: dict, stats: dict) -> str:
    avg = stats.get('avg_30_days') or 0
    saving_pct = (avg - flight['price']) / avg * 100 if avg > 0 else 0
    return (
        f'âœˆï¸ DEAL FOUND\n\n'
        f'Route   : {flight.get("origin", "?")} â†’ {flight.get("destination", "?")}\n'
        f'Dates   : {flight.get("departure", "?")[:10]}'
        + (f' â€” {flight.get("return_date", "?")[:10]}' if flight.get('return_date') else '') + '\n'
        f'Airline : {flight.get("airline", "?")}\n'
        f'Price   : R${flight["price"]:,.0f}\n'
        f'Average : R${avg:,.0f}\n'
        f'Saving  : {saving_pct:.0f}%\n'
        f'Score   : {buy_score["score"]}/100\n'
        f'Action  : {buy_score["recommendation"]}\n'
        f'Link    : {flight.get("url", "N/A")}'
    )

def send_telegram(message: str) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                          json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
                          timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def send_email(subject: str, body: str) -> bool:
    sender = os.getenv('EMAIL_SENDER')
    password = os.getenv('EMAIL_PASSWORD')
    recipient = os.getenv('EMAIL_RECIPIENT')
    if not all([sender, password, recipient]):
        return False
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = recipient
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(sender, password)
            s.sendmail(sender, recipient, msg.as_string())
        return True
    except Exception:
        return False

def alert_deal(flight: dict, buy_score: dict, stats: dict, min_score: int = 85) -> bool:
    if buy_score['score'] < min_score:
        return False
    msg = _deal_message(flight, buy_score, stats)
    print(msg)
    tg = send_telegram(msg)
    em = send_email(f'âœˆï¸ Flight Deal Score {buy_score["score"]}/100', msg)
    print(f'   Telegram: {"âœ…" if tg else "âš ï¸ not configured"}  |  Email: {"âœ…" if em else "âš ï¸ not configured"}')
    return True

print('âœ… Notification system ready (Telegram + Email)')

# ── Cell: 20-scheduler ──────────────────────────────────────────────
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone='America/Sao_Paulo')

def run_monitoring_cycle(label: str = 'scheduled'):
    profile = load_profile()
    dep_date = (datetime.now() + timedelta(days=60)).strftime('%Y-%m-%d')
    print(f'\nðŸ” [{label}] Monitoring cycle started â€” {datetime.now().strftime("%Y-%m-%d %H:%M")}')

    for origin in profile['origins']:
        for dest in profile['destinations']:
            route = f'{origin}-{dest}'
            try:
                flights = search_all_sources(origin, dest, dep_date)
                if not flights:
                    continue
                best = flights[0]
                best.update({'origin': origin, 'destination': dest})
                save_flight_to_db(route, dep_date, None, best['price'], 'BRL',
                                  best.get('airline', ''), best.get('source', ''), best.get('url', ''))
                stats = get_price_stats(route)
                score = calculate_buy_score(best['price'], stats)
                error = detect_error_fare(best['price'], route, stats)
                flag = 'âš ï¸ POSSIBLE ERROR FARE' if error['is_error_fare'] else ''
                print(f'  {route}: R${best["price"]:,.0f} | Score {score["score"]}/100 | {score["recommendation"]} {flag}')
                alert_deal(best, score, stats)
            except Exception as e:
                print(f'  {route}: error â€” {e}')

def check_price_drop():
    """Triggered when any route drops >10% vs last seen price."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT route, last_price FROM price_statistics')
    routes = c.fetchall()
    conn.close()
    dep_date = (datetime.now() + timedelta(days=60)).strftime('%Y-%m-%d')
    for route, last_price in routes:
        parts = route.split('-')
        if len(parts) != 2 or not last_price:
            continue
        orig, dest = parts
        try:
            flights = search_all_sources(orig, dest, dep_date)
            if flights:
                new_price = flights[0]['price']
                if (last_price - new_price) / last_price >= 0.10:
                    print(f'  ðŸ’¥ {route}: price dropped {(last_price-new_price)/last_price*100:.0f}%!')
                    stats = get_price_stats(route)
                    score = calculate_buy_score(new_price, stats)
                    alert_deal(flights[0], score, stats, min_score=60)
        except Exception:
            pass

scheduler.add_job(run_monitoring_cycle, CronTrigger(hour=8, minute=0),
                  id='daily_monitor', kwargs={'label': 'daily-08:00'})
scheduler.add_job(run_monitoring_cycle, 'interval', hours=6,
                  id='frequent_monitor', kwargs={'label': 'every-6h'})
scheduler.add_job(check_price_drop, 'interval', hours=2,
                  id='drop_checker')

print('âœ… Scheduler configured')
print('   â€¢ Daily monitoring : 08:00 (America/Sao_Paulo)')
print('   â€¢ High-frequency   : every 6 hours')
print('   â€¢ Drop detector    : every 2 hours (triggers if >10% drop)')
print()
print('ðŸ’¡ To start: scheduler.start()')
print('   To stop : scheduler.shutdown()')

# ── Cell: 22-tools ──────────────────────────────────────────────
# â”€â”€ Agent Tools â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@function_tool
def search_flights(origin: str, destination: str, departure_date: str,
                   return_date: str = '', cabin_class: str = 'ECONOMY') -> str:
    '''Search flights between origin and destination. Returns top options with Buy Score.
    origin/destination: IATA codes (e.g. GRU, CDG). departure_date: YYYY-MM-DD.'''
    rdate = return_date or None
    flights = search_all_airports(origin, destination, departure_date, rdate)
    if not flights:
        return f'No flights found from {origin} to {destination} on {departure_date}.'
    route = f'{origin}-{destination}'
    lines = [f'\nâœˆï¸ Flights {origin} â†’ {destination} on {departure_date}\n']
    for f in flights[:6]:
        save_flight_to_db(route, departure_date, rdate, f.get('price', 0), 'BRL',
                          f.get('airline', ''), f.get('source', ''), f.get('url', ''))
        stats = get_price_stats(route)
        score = calculate_buy_score(f.get('price', 0), stats)
        error = detect_error_fare(f.get('price', 0), route, stats)
        ef_tag = ' âš ï¸ POSSIBLE ERROR FARE' if error['is_error_fare'] else ''
        lines.append(
            f'  {f.get("airline", "?")} {f.get("flight_number", "")} | '
            f'R${f.get("price", 0):,.0f} | {f.get("stops", 0)} stop(s) | '
            f'{f.get("duration", "")} | Score {score["score"]}/100 {score["recommendation"]}{ef_tag}'
        )
    return '\n'.join(lines)

@function_tool
def get_price_analysis(origin: str, destination: str) -> str:
    '''Get full price history and statistics for a route (7/30/90-day averages, min/max, percentile).'''
    route = f'{origin}-{destination}'
    stats = get_price_stats(route)
    if not stats['all_prices']:
        return f'No historical data for {route}. Run search_flights first to build history.'
    current = stats['all_prices'][-1]
    pct = calculate_percentile(current, stats['all_prices'])
    pred = predict_price_movement(route)
    return (
        f'\nðŸ“Š Price Analysis: {route}\n'
        f'  Current price   : R${current:,.0f}\n'
        f'  7-day average   : R${(stats["avg_7_days"] or 0):,.0f}\n'
        f'  30-day average  : R${(stats["avg_30_days"] or 0):,.0f}\n'
        f'  90-day average  : R${(stats["avg_90_days"] or 0):,.0f}\n'
        f'  Historical min  : R${(stats["historical_min"] or 0):,.0f}\n'
        f'  Historical max  : R${(stats["historical_max"] or 0):,.0f}\n'
        f'  Percentile      : cheaper than {pct:.0f}% of all historical fares\n\n'
        f'  Prediction: {pred["message"]}'
    )

@function_tool
def get_buy_recommendation(origin: str, destination: str, current_price: float) -> str:
    '''Get a BUY NOW / WAIT recommendation with a 0â€“100 score for a specific price.'''
    route = f'{origin}-{destination}'
    stats = get_price_stats(route)
    score = calculate_buy_score(current_price, stats)
    pred = predict_price_movement(route)
    error = detect_error_fare(current_price, route, stats)
    result = (
        f'\nðŸŽ¯ Buy Recommendation: {route} @ R${current_price:,.0f}\n'
        f'  BUY SCORE : {score["score"]}/100\n'
        f'  Verdict   : {score["recommendation"]}\n'
        f'  Reasons   : {", ".join(score["reasons"]) if score["reasons"] else "Standard pricing"}\n\n'
        f'  {pred["message"]}'
    )
    if error['is_error_fare']:
        result += f'\n\n  {error["message"]}'
    return result

@function_tool
def search_flexible_dates(origin: str, destination: str, target_date: str,
                          flexibility_days: int = 5) -> str:
    '''Find the cheapest departure date within Â±flexibility_days of target_date (capped at Â±5).
    Call this tool ONCE for the primary airport pair when asked about flexible dates.
    target_date: YYYY-MM-DD format.'''
    res = _find_flexible_dates(origin, destination, target_date, flexibility=min(flexibility_days, 5))
    opts = res.get('options', [])
    if not opts:
        return f'No flights found near {target_date} for {origin} â†’ {destination}.'
    lines = [f'Best dates {origin}â†’{destination} (Â±5d from {target_date}):']
    for f in opts:
        delta = f.get('delta', 0)
        tag = 'same' if delta == 0 else f'{abs(delta)}d {"earlier" if delta < 0 else "later"}'
        lines.append(f'  {f["dep_date"]} ({tag}): R${f["price"]:,.0f} â€” {f["airline"]}')
    lines.append(f'BEST: {opts[0]["dep_date"]} R${opts[0]["price"]:,.0f}')
    return '\n'.join(lines)

@function_tool
def analyze_miles_vs_cash(origin: str, destination: str, cash_price: float) -> str:
    '''Compare cash vs miles programs (Smiles, LATAM Pass, Azul) for a flight.'''
    profile = load_profile()
    rec = recommend_payment_method(cash_price, profile)
    lines = [f'\nðŸ’³ Miles vs Cash: {origin} â†’ {destination} @ R${cash_price:,.0f}\n']
    for prog_id, prog in MILES_PROGRAMS.items():
        bal = profile.get('miles_programs', {}).get(prog_id, {}).get('balance', 0)
        val = bal * prog['brl_per_mile']
        lines.append(f'  {prog["name"]}: {bal:,} miles = R${val:,.0f} value')
    lines.append(f'\n  ðŸŽ¯ Recommendation: {rec["method"]}')
    lines.append(f'  Out-of-pocket   : R${rec["cost_brl"]:,.0f}')
    if rec['miles_used'] > 0:
        lines.append(f'  Miles used      : {rec["miles_used"]:,}')
        lines.append(f'  Savings         : R${rec["savings_brl"]:,.0f}')
    return '\n'.join(lines)

@function_tool
def update_miles_balance(program: str, balance: int) -> str:
    '''Update miles balance for a program. program: smiles | latam_pass | azul_fidelidade'''
    profile = load_profile()
    prog_id = program.lower().replace(' ', '_')
    if prog_id not in profile.get('miles_programs', {}):
        return f'Unknown program: {program}. Use: smiles, latam_pass, azul_fidelidade'
    profile['miles_programs'][prog_id]['balance'] = balance
    save_profile(profile)
    return f'âœ… {MILES_PROGRAMS[prog_id]["name"]} balance updated to {balance:,} miles'

@function_tool
def monitor_route(origin: str, destination: str, target_month: str) -> str:
    '''Add a route to the monitoring watchlist. target_month: e.g. "2026-09"'''
    profile = load_profile()
    entry = {'origin': origin, 'destination': destination,
              'target_month': target_month, 'added': datetime.now().isoformat()}
    profile.setdefault('monitoring', []).append(entry)
    save_profile(profile)
    return (f'âœ… Now monitoring {origin} â†’ {destination} for {target_month}\n'
            f'I will alert you (score â‰¥ 85) via Telegram and email.')

@function_tool
def get_overview(request: str = 'summary') -> str:
    '''Show a dashboard: monitored routes, best deals found, profile summary.
    request: always pass "summary"'''
    profile = load_profile()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM flight_search_history')
    total = c.fetchone()[0]
    c.execute('SELECT route, MIN(price), MAX(price), COUNT(*) FROM flight_search_history GROUP BY route ORDER BY MIN(price)')
    routes = c.fetchall()
    conn.close()
    lines = ['\nðŸŒ ELITE FLIGHT DEAL HUNTER â€” DASHBOARD\n']
    lines.append(f'Origins     : {", ".join(profile["origins"])}')
    lines.append(f'Destinations: {", ".join(profile["destinations"])}')
    lines.append(f'Max budget  : R${profile["max_budget_brl"]:,.0f}')
    lines.append(f'Total prices tracked: {total}')
    if routes:
        lines.append('\nðŸ“Š Best Prices Found:')
        for route, min_p, max_p, count in routes[:8]:
            lines.append(f'  {route}: R${min_p:,.0f} â€“ R${max_p:,.0f} ({count} searches)')
    monitoring = profile.get('monitoring', [])
    if monitoring:
        lines.append('\nðŸ‘ï¸ Watchlist:')
        for m in monitoring[-5:]:
            lines.append(f'  {m["origin"]} â†’ {m["destination"]} â€” {m["target_month"]}')
    return '\n'.join(lines)

print('âœ… 8 agent tools defined')
print('   search_flights | get_price_analysis | get_buy_recommendation')
print('   search_flexible_dates | analyze_miles_vs_cash | update_miles_balance')
print('   monitor_route | get_overview')

# ── Cell: 23-agent ──────────────────────────────────────────────
# â”€â”€ Groq client (OpenAI-compatible, free tier) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# openai/gpt-oss-120b: OpenAI's open-source model on Groq â€” best tool-calling compatibility
GROQ_MODEL = 'openai/gpt-oss-120b'

groq_client = AsyncOpenAI(
    api_key=os.getenv('GROQ_API_KEY'),
    base_url='https://api.groq.com/openai/v1',
)

model = OpenAIChatCompletionsModel(
    model=GROQ_MODEL,
    openai_client=groq_client,
)

# Disable strict JSON schema â€” Groq does not support OpenAI strict mode
_all_tools = [
    search_flights, get_price_analysis, get_buy_recommendation,
    search_flexible_dates, analyze_miles_vs_cash, update_miles_balance,
    monitor_route, get_overview,
]
for _t in _all_tools:
    if hasattr(_t, 'strict_json_schema'):
        _t.strict_json_schema = False

# â”€â”€ Agent â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
agent = Agent(
    name='Elite Flight Deal Hunter',
    model=model,
    instructions='''You are an elite AI flight deal hunting agent â€” a combination of:
â€¢ Google Flights price tracker
â€¢ Hopper prediction engine
â€¢ Experienced travel hacker
â€¢ Brazilian miles & points expert (Smiles, LATAM Pass, Azul Fidelidade)

YOUR MISSION: Find the best flight deals and help the user decide exactly when to BUY NOW or WAIT.

ALWAYS follow this workflow:
1. Search across multiple airports (GRU/CGH/VCP for SÃ£o Paulo, CDG/ORY for Paris, etc.)
2. Calculate the BUY SCORE (0â€“100) before any recommendation
3. Check price analysis if history exists, or explain scores are based on current search
4. Flag error fares immediately â€” these disappear in hours
5. Use search_flexible_dates when the user asks about flexible dates, cheapest dates, or best time to fly
6. Compare miles vs cash when the user has a program balance
7. Give a direct verdict: BUY NOW / WAIT / EXPENSIVE â€” no vague answers

BUY SCORE GUIDE:
â€¢ 90â€“100 = BUY NOW (near historical minimum, or 20%+ below average)
â€¢ 70â€“89  = GOOD OPPORTUNITY
â€¢ 40â€“69  = WAIT â€” price may drop
â€¢ 0â€“39   = EXPENSIVE â€” do not buy

USER CONTEXT: Brazilian traveler flying from SÃ£o Paulo (GRU/CGH/VCP) to Europe and worldwide.
Prices in BRL. Common programs: Smiles (GOL), LATAM Pass, Azul Fidelidade.
Be concise, direct, and actionable.''',
    tools=_all_tools,
)

print(f'âœ… Agent created: {agent.name}')
print(f'   Provider: Groq (free tier)')
print(f'   Model   : {GROQ_MODEL}')
print(f'   Tools   : {len(agent.tools)}')

# ── Cell: 25-chat-helper ──────────────────────────────────────────────
import time

async def chat_async(message: str) -> str:
    result = await Runner.run(agent, message, max_turns=15)
    return result.final_output

def ask(message: str, _retry: int = 5) -> str:
    '''Send a message to the agent. Auto-retries on Groq rate limits.'''
    print(f'\n>>> {message}')
    print('-' * 60)
    for attempt in range(_retry):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            response = loop.run_until_complete(chat_async(message))
            print(response)
            return response
        except Exception as e:
            err = str(e)
            if '429' in err or 'rate_limit' in err.lower():
                wait = 60 * (attempt + 1)
                print(f'   [rate limit] waiting {wait}s (attempt {attempt+1}/{_retry})...')
                time.sleep(wait)
            else:
                raise
    print('   [failed after retries]')
    return ''

print('Chat helper ready  --  ask("your message")')

# ── Cell: 27-test1 ──────────────────────────────────────────────
ask('Show me an overview of my flight hunter')
time.sleep(60)

# ── Cell: 28-test2 ──────────────────────────────────────────────
ask('Search for flights from GRU to CDG on 2026-09-15')
time.sleep(60)

# ── Cell: 29-test3 ──────────────────────────────────────────────
ask('Should I buy a GRU to LHR ticket for R$2,950?')
time.sleep(60)

# ── Cell: 30-test4 ──────────────────────────────────────────────
ask('Find the cheapest dates to fly GRU to Paris in September 2026, plus or minus 7 days')
time.sleep(60)

# ── Cell: 31-test5 ──────────────────────────────────────────────
ask('I have 80000 Smiles miles and 45000 LATAM Pass miles. Update my balances, then compare cash vs miles for a R$3800 ticket GRU to CDG')
time.sleep(60)

# ── Cell: 32-test6 ──────────────────────────────────────────────
ask('Monitor Sao Paulo to Paris in September 2026 and London in October 2026')
time.sleep(60)

# ── Cell: 33-test7 ──────────────────────────────────────────────
ask('Find the cheapest European destination from Sao Paulo for late September 2026. Check CDG, LHR, FCO, and AMS.')

