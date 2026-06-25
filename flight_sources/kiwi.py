"""Kiwi / Tequila API integration."""
import os
import requests
from datetime import datetime, timedelta


def search_kiwi(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = None,
    currency: str = "BRL",
    max_results: int = 10,
) -> list[dict]:
    api_key = os.getenv("KIWI_API_KEY")
    if not api_key:
        raise ValueError("KIWI_API_KEY not set")

    dep_dt = datetime.strptime(departure_date, "%Y-%m-%d")
    date_from = dep_dt.strftime("%d/%m/%Y")
    date_to = (dep_dt + timedelta(days=1)).strftime("%d/%m/%Y")

    params = {
        "fly_from": origin,
        "fly_to": destination,
        "date_from": date_from,
        "date_to": date_to,
        "curr": currency,
        "limit": max_results,
        "sort": "price",
    }
    if return_date:
        ret_dt = datetime.strptime(return_date, "%Y-%m-%d")
        params["return_from"] = ret_dt.strftime("%d/%m/%Y")
        params["return_to"] = (ret_dt + timedelta(days=1)).strftime("%d/%m/%Y")

    resp = requests.get(
        "https://api.tequila.kiwi.com/v2/search",
        headers={"apikey": api_key},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()

    results = []
    for flight in resp.json().get("data", []):
        route = flight.get("route", [{}])
        first_seg = route[0] if route else {}
        results.append({
            "airline": first_seg.get("airline", "Unknown"),
            "flight_number": first_seg.get("flight_no", ""),
            "origin": flight.get("flyFrom", origin),
            "destination": flight.get("flyTo", destination),
            "departure": flight.get("local_departure", departure_date)[:16],
            "arrival": flight.get("local_arrival", "")[:16],
            "duration": f"{flight.get('duration', {}).get('departure', 0) // 3600}h{(flight.get('duration', {}).get('departure', 0) % 3600) // 60:02d}m",
            "stops": len(route) - 1,
            "bags": flight.get("bags_price", {}).get("1", 1),
            "price": float(flight.get("price", 0)),
            "currency": currency,
            "url": flight.get("deep_link", "https://www.kiwi.com/"),
            "source": "kiwi",
        })

    return results
