"""Amadeus Flight Offers API integration."""
import os
import requests
from datetime import datetime

_token_cache = {"token": None, "expires_at": 0}


def _get_token() -> str:
    now = datetime.now().timestamp()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    env = os.getenv("AMADEUS_ENV", "test")
    base = "https://test.api.amadeus.com" if env == "test" else "https://api.amadeus.com"

    resp = requests.post(
        f"{base}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("AMADEUS_API_KEY"),
            "client_secret": os.getenv("AMADEUS_API_SECRET"),
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data["expires_in"]
    return _token_cache["token"]


def search_amadeus(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str = None,
    adults: int = 1,
    cabin_class: str = "ECONOMY",
    currency: str = "BRL",
    max_results: int = 10,
) -> list[dict]:
    env = os.getenv("AMADEUS_ENV", "test")
    base = "https://test.api.amadeus.com" if env == "test" else "https://api.amadeus.com"

    token = _get_token()
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": departure_date,
        "adults": adults,
        "travelClass": cabin_class,
        "currencyCode": currency,
        "max": max_results,
    }
    if return_date:
        params["returnDate"] = return_date

    resp = requests.get(
        f"{base}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=15,
    )
    resp.raise_for_status()

    results = []
    for offer in resp.json().get("data", []):
        itinerary = offer["itineraries"][0]
        segments = itinerary["segments"]
        first_seg = segments[0]
        last_seg = segments[-1]
        price = float(offer["price"]["grandTotal"])

        results.append({
            "airline": first_seg["carrierCode"],
            "flight_number": f"{first_seg['carrierCode']}{first_seg['number']}",
            "origin": first_seg["departure"]["iataCode"],
            "destination": last_seg["arrival"]["iataCode"],
            "departure": first_seg["departure"]["at"][:16],
            "arrival": last_seg["arrival"]["at"][:16],
            "duration": itinerary["duration"],
            "stops": len(segments) - 1,
            "bags": 0,
            "price": price,
            "currency": currency,
            "url": f"https://www.amadeus.com/",
            "source": "amadeus",
        })

    return results
