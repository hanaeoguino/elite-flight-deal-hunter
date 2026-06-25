"""Google Flights via SerpAPI integration."""
import os
import requests


def search_google_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = None,
    currency: str = "BRL",
    language: str = "pt",
) -> list[dict]:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        raise ValueError("SERPAPI_KEY not set")

    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": destination,
        "outbound_date": departure_date,
        "currency": currency,
        "hl": language,
        "api_key": api_key,
    }
    if return_date:
        params["return_date"] = return_date
        params["type"] = "1"  # round trip
    else:
        params["type"] = "2"  # one way

    resp = requests.get("https://serpapi.com/search.json", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for flight in data.get("best_flights", []) + data.get("other_flights", []):
        for leg in flight.get("flights", [{}]):
            results.append({
                "airline": leg.get("airline", "Unknown"),
                "flight_number": leg.get("flight_number", ""),
                "origin": origin,
                "destination": destination,
                "departure": leg.get("departure_airport", {}).get("time", departure_date),
                "arrival": leg.get("arrival_airport", {}).get("time", ""),
                "duration": f"{flight.get('total_duration', 0) // 60}h{flight.get('total_duration', 0) % 60:02d}m",
                "stops": len(flight.get("flights", [])) - 1,
                "bags": 1,
                "price": float(flight.get("price", 0)),
                "currency": currency,
                "url": f"https://www.google.com/flights?hl=pt#flt={origin}.{destination}.{departure_date}",
                "source": "google_flights",
            })
            break  # one entry per offer

    return results
