"""
Utility Tools for Local AI Agent.
Queries weather forecasts (Open-Meteo API) and real-time currency exchange rates.
"""

import ctypes
import datetime
import json
import logging
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple

logger = logging.getLogger("utility_tools")

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, Any]] = {}

CURRENCY_ALIASES = {
    "DOLLAR": "USD",
    "DOLLARS": "USD",
    "US DOLLAR": "USD",
    "RUPEE": "INR",
    "RUPEES": "INR",
    "INDIAN RUPEE": "INR",
    "EURO": "EUR",
    "EUROS": "EUR",
    "POUND": "GBP",
    "POUNDS": "GBP",
    "YEN": "JPY",
}


def _cache_get(key: str) -> Any:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        _CACHE.pop(key, None)
    return None


def _cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + ttl_seconds, value)

# WMO Weather interpretation codes
WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Drizzle: Light", 53: "Drizzle: Moderate", 55: "Drizzle: Dense intensity",
    61: "Rain: Slight", 63: "Rain: Moderate", 65: "Rain: Heavy intensity",
    71: "Snow fall: Slight", 73: "Snow fall: Moderate", 75: "Snow fall: Heavy",
    80: "Rain showers: Slight", 81: "Rain showers: Moderate", 82: "Rain showers: Violent",
    95: "Thunderstorm: Slight or moderate", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail"
}


def get_weather(city: str = "Kurukshetra") -> Dict[str, Any]:
    """
    Query Open-Meteo free weather API for current temperature, weather conditions,
    and forecast for the given city (cached for 5 minutes).
    """
    clean_city = (city or "Kurukshetra").strip()
    cache_key = f"weather:{clean_city.lower()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        # Step 1: Geocode city name to lat/lon (cached for 1 hour)
        geo_key = f"geo:{clean_city.lower()}"
        geo_result = _cache_get(geo_key)
        if geo_result is None:
            geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(clean_city)}&count=1&language=en&format=json"
            req = urllib.request.Request(geo_url, headers={'User-Agent': 'LocalAIAgent/1.0'})

            with urllib.request.urlopen(req, timeout=10) as response:
                geo_data = json.loads(response.read().decode('utf-8'))

            results = geo_data.get("results") or []
            if not results:
                return {
                    "status": "error",
                    "message": f"City '{clean_city}' not found in geocoding database."
                }
            geo_result = results[0]
            _cache_set(geo_key, geo_result, ttl_seconds=3600)

        lat, lon = geo_result["latitude"], geo_result["longitude"]
        city_name = geo_result.get("name", clean_city)
        country = geo_result.get("country", "")

        # Step 2: Fetch current weather & forecast
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&daily=temperature_2m_max,temperature_2m_min,weathercode&timezone=auto"
        req_w = urllib.request.Request(weather_url, headers={'User-Agent': 'LocalAIAgent/1.0'})

        with urllib.request.urlopen(req_w, timeout=10) as response_w:
            w_data = json.loads(response_w.read().decode('utf-8'))

        current = w_data.get("current_weather") or {}
        temp = current.get("temperature")
        windspeed = current.get("windspeed")
        code = current.get("weathercode", 0)
        condition = WMO_WEATHER_CODES.get(code, "Unknown condition")

        daily = w_data.get("daily") or {}
        max_temps = daily.get("temperature_2m_max") or [None]
        min_temps = daily.get("temperature_2m_min") or [None]
        max_temp = max_temps[0]
        min_temp = min_temps[0]

        summary = (
            f"Weather in {city_name}, {country}: {temp}°C, {condition}. "
            f"Wind Speed: {windspeed} km/h. High today: {max_temp}°C, Low today: {min_temp}°C."
        )

        payload = {
            "status": "success",
            "city": city_name,
            "country": country,
            "latitude": lat,
            "longitude": lon,
            "temperature_celsius": temp,
            "condition": condition,
            "wind_speed_kmh": windspeed,
            "max_temperature_today": max_temp,
            "min_temperature_today": min_temp,
            "summary": summary
        }
        _cache_set(cache_key, payload, ttl_seconds=300)
        return payload

    except Exception as e:
        logger.error(f"Error fetching weather for {clean_city}: {e}")
        return {
            "status": "error",
            "message": f"Failed to retrieve weather data: {str(e)}",
            "city": clean_city
        }


def get_exchange_rate(source_currency: str = "USD", target_currency: str = "INR") -> Dict[str, Any]:
    """
    Fetch real-time currency exchange rates from open exchange APIs with fallback (cached for 5 minutes).
    """
    raw_source = (source_currency or "USD").strip().upper()
    raw_target = (target_currency or "INR").strip().upper()
    source = CURRENCY_ALIASES.get(raw_source, raw_source)
    target = CURRENCY_ALIASES.get(raw_target, raw_target)

    cache_key = f"fx:{source}:{target}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    api_urls = [
        f"https://open.er-api.com/v6/latest/{urllib.parse.quote(source)}",
        f"https://api.exchangerate-api.com/v4/latest/{urllib.parse.quote(source)}"
    ]

    last_error = None
    for url in api_urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'LocalAIAgent/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

            rates = data.get("rates")
            if rates and target in rates:
                rate = rates[target]
                summary = f"1 {source} = {rate:.4f} {target}"
                payload = {
                    "status": "success",
                    "source_currency": source,
                    "target_currency": target,
                    "exchange_rate": rate,
                    "summary": summary,
                    "provider": url.split("/")[2]
                }
                _cache_set(cache_key, payload, ttl_seconds=300)
                return payload
        except Exception as e:
            last_error = e
            logger.warning(f"Failed to fetch exchange rate from {url}: {e}")

    return {
        "status": "error",
        "message": f"Failed to fetch exchange rate for {source} to {target}: {str(last_error)}",
        "source_currency": source,
        "target_currency": target
    }


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_byte),
        ("BatteryFlag", ctypes.c_byte),
        ("BatteryLifePercent", ctypes.c_byte),
        ("SystemStatusFlag", ctypes.c_byte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def get_system_status() -> Dict[str, Any]:
    """Return local date/time and Windows battery/CPU/memory telemetry without extra dependencies."""
    now = datetime.datetime.now()
    info: Dict[str, Any] = {
        "status": "success",
        "date": now.strftime("%Y-%m-%d"),
        "day": now.strftime("%A"),
        "time": now.strftime("%I:%M %p"),
        "battery_percent": None,
        "ac_plugged": None,
    }

    if hasattr(ctypes, "windll"):
        try:
            sps = _SystemPowerStatus()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                if 0 <= sps.BatteryLifePercent <= 100:
                    info["battery_percent"] = int(sps.BatteryLifePercent)
                if sps.ACLineStatus in (0, 1):
                    info["ac_plugged"] = bool(sps.ACLineStatus == 1)
        except Exception:
            pass

    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        try:
            ps = (
                "$os = Get-CimInstance Win32_OperatingSystem; "
                "$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average; "
                "$usedGb = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/1MB, 1); "
                "$totalGb = [math]::Round($os.TotalVisibleMemorySize/1MB, 1); "
                "@{cpu=$cpu; ram_used=$usedGb; ram_total=$totalGb} | ConvertTo-Json -Compress"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if res.returncode == 0 and res.stdout.strip():
                hw = json.loads(res.stdout.strip())
                info["cpu_percent"] = hw.get("cpu")
                info["ram_used_gb"] = hw.get("ram_used")
                info["ram_total_gb"] = hw.get("ram_total")
        except Exception:
            pass

    parts = [f"Today is {info['day']}, {info['date']}, {info['time']}."]
    if info["battery_percent"] is not None:
        plug_state = "plugged in" if info["ac_plugged"] else "on battery"
        parts.append(f"Battery: {info['battery_percent']}% ({plug_state}).")
    if info.get("cpu_percent") is not None:
        parts.append(f"CPU: {info['cpu_percent']}%, RAM: {info['ram_used_gb']}/{info['ram_total_gb']} GB.")
    info["summary"] = " ".join(parts)
    return info


def get_morning_briefing(city: str = "Kurukshetra") -> Dict[str, Any]:
    """Compile local time, weather, pending todos, and due/upcoming reminders into one briefing."""
    from tools.memory_store import list_reminder_items, list_todo_items

    sys_info = get_system_status()
    weather = get_weather(city)
    todos_res = list_todo_items(include_completed=False)
    rem_res = list_reminder_items(include_triggered=False)

    sections = [sys_info.get("summary", "")]
    if weather.get("status") == "success":
        sections.append(weather["summary"])

    todos = todos_res.get("todos", [])
    if todos:
        top_todos = ", ".join(t["task"] for t in todos[:5])
        sections.append(f"Pending todos ({len(todos)}): {top_todos}.")
    else:
        sections.append("No pending todos.")

    reminders = rem_res.get("reminders", [])
    if reminders:
        top_rem = ", ".join(f"{r['title']} ({r['due_at']})" for r in reminders[:5])
        sections.append(f"Upcoming reminders ({len(reminders)}): {top_rem}.")

    return {
        "status": "success",
        "system": sys_info,
        "weather": weather,
        "pending_todos": len(todos),
        "upcoming_reminders": len(reminders),
        "summary": " ".join(s for s in sections if s),
    }


_COUNTRY_TIMEZONE_HINTS = {
    "canada": "Ottawa",
    "usa": "New York",
    "united states": "New York",
    "us": "New York",
    "america": "New York",
    "uk": "London",
    "united kingdom": "London",
    "england": "London",
    "britain": "London",
    "india": "New Delhi",
    "australia": "Sydney",
    "japan": "Tokyo",
    "china": "Beijing",
    "germany": "Berlin",
    "france": "Paris",
    "uae": "Dubai",
    "russia": "Moscow",
    "singapore": "Singapore",
    "pakistan": "Islamabad",
    "nepal": "Kathmandu",
}

_WINDOWS_TZ_FALLBACKS = {
    "canada": ("Canada (Toronto/Ottawa)", "Eastern Standard Time"),
    "ottawa": ("Ottawa, Canada", "Eastern Standard Time"),
    "toronto": ("Toronto, Canada", "Eastern Standard Time"),
    "vancouver": ("Vancouver, Canada", "Pacific Standard Time"),
    "usa": ("United States (New York)", "Eastern Standard Time"),
    "new york": ("New York, USA", "Eastern Standard Time"),
    "london": ("London, UK", "GMT Standard Time"),
    "uk": ("United Kingdom (London)", "GMT Standard Time"),
    "india": ("India (New Delhi)", "India Standard Time"),
    "delhi": ("Delhi, India", "India Standard Time"),
    "dubai": ("Dubai, UAE", "Arabian Standard Time"),
    "tokyo": ("Tokyo, Japan", "Tokyo Standard Time"),
    "sydney": ("Sydney, Australia", "AUS Eastern Standard Time"),
    "singapore": ("Singapore", "Singapore Standard Time"),
    "paris": ("Paris, France", "Romance Standard Time"),
    "berlin": ("Berlin, Germany", "W. Europe Standard Time"),
    "moscow": ("Moscow, Russia", "Russian Standard Time"),
}


def _fetch_json_with_retry(url: str, timeout: int = 10, retries: int = 1) -> Any:
    last_err: Exception = RuntimeError("Unknown network error")
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LocalAIAgent/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            last_err = e
    raise last_err


def _windows_tz_offset_seconds(tz_id: str) -> Tuple[int, str]:
    ps = (
        f"$tz = [System.TimeZoneInfo]::FindSystemTimeZoneById('{tz_id}'); "
        "$utc = [DateTime]::UtcNow; "
        "$off = [int]$tz.GetUtcOffset($utc).TotalSeconds; "
        "$abbr = if ($tz.IsDaylightSavingTime($utc)) { $tz.DaylightName } else { $tz.StandardName }; "
        "Write-Output \"$off|$abbr\""
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if res.returncode == 0 and "|" in res.stdout:
        off_s, abbr = res.stdout.strip().split("|", 1)
        return int(off_s), abbr.strip()
    raise RuntimeError(res.stderr.strip() or "Windows TimeZoneInfo lookup failed")


def _format_world_time_result(place: str, offset_seconds: int, timezone_name: str, abbr: str) -> Dict[str, Any]:
    tz = datetime.timezone(datetime.timedelta(seconds=int(offset_seconds)))
    now_target = datetime.datetime.now(datetime.timezone.utc).astimezone(tz)
    time_str = now_target.strftime("%I:%M %p")
    date_str = now_target.strftime("%A, %Y-%m-%d")
    label_abbr = abbr or timezone_name or "Local"
    return {
        "status": "success",
        "location": place,
        "date": date_str,
        "time": time_str,
        "timezone": timezone_name,
        "timezone_abbr": label_abbr,
        "summary": f"Current time in {place} is {time_str} ({label_abbr}, {date_str}).",
    }


def get_world_time(location: str = "") -> Dict[str, Any]:
    """Return accurate current date and wall-clock time for a city/country (or local time if empty)."""
    raw_loc = (location or "").strip(" ?.!,'\"")
    if not raw_loc or raw_loc.lower() in {"here", "local", "my laptop", "now", "today"}:
        now_local = datetime.datetime.now()
        return {
            "status": "success",
            "location": "Local",
            "date": now_local.strftime("%A, %Y-%m-%d"),
            "time": now_local.strftime("%I:%M %p"),
            "timezone": "Local",
            "summary": f"Current local time is {now_local.strftime('%I:%M %p')} ({now_local.strftime('%A, %Y-%m-%d')}).",
        }

    lookup = _COUNTRY_TIMEZONE_HINTS.get(raw_loc.lower(), raw_loc)
    tz_cache_key = f"tzinfo:{lookup.lower()}"
    tz_info = _cache_get(tz_cache_key)

    try:
        if tz_info is None:
            geo_key = f"geo:{lookup.lower()}"
            geo_result = _cache_get(geo_key)
            if geo_result is None:
                geo_url = (
                    f"https://geocoding-api.open-meteo.com/v1/search?"
                    f"name={urllib.parse.quote(lookup)}&count=1&language=en&format=json"
                )
                geo_data = _fetch_json_with_retry(geo_url, timeout=10, retries=1)
                results = geo_data.get("results") or []
                if not results:
                    return {
                        "status": "error",
                        "location": raw_loc,
                        "message": f"Could not find timezone information for '{raw_loc}'.",
                    }
                geo_result = results[0]
                _cache_set(geo_key, geo_result, ttl_seconds=3600)

            lat, lon = geo_result["latitude"], geo_result["longitude"]
            forecast_url = (
                f"https://api.open-meteo.com/v1/forecast?"
                f"latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto"
            )
            t_data = _fetch_json_with_retry(forecast_url, timeout=10, retries=1)

            offset_secs = int(t_data.get("utc_offset_seconds", 0))
            tz_name = str(t_data.get("timezone") or "")
            tz_abbr = str(t_data.get("timezone_abbreviation") or "")
            city_label = geo_result.get("name", raw_loc)
            country_label = geo_result.get("country", "")
            place_label = f"{city_label}, {country_label}".strip(", ")
            if raw_loc.lower() in _COUNTRY_TIMEZONE_HINTS and city_label.lower() != raw_loc.lower():
                place_label = f"{raw_loc.title()} ({city_label})"

            tz_info = {
                "place": place_label,
                "offset_seconds": offset_secs,
                "timezone": tz_name,
                "timezone_abbr": tz_abbr,
            }
            _cache_set(tz_cache_key, tz_info, ttl_seconds=21600)

        return _format_world_time_result(
            place=tz_info["place"],
            offset_seconds=int(tz_info["offset_seconds"]),
            timezone_name=tz_info.get("timezone", ""),
            abbr=tz_info.get("timezone_abbr", ""),
        )
    except Exception as e:
        fb = _WINDOWS_TZ_FALLBACKS.get(raw_loc.lower())
        if fb and os.name == "nt":
            try:
                place, win_tz_id = fb
                off_s, abbr = _windows_tz_offset_seconds(win_tz_id)
                return _format_world_time_result(
                    place=place,
                    offset_seconds=off_s,
                    timezone_name=win_tz_id,
                    abbr=abbr,
                )
            except Exception:
                pass
        logger.error(f"Error fetching world time for '{raw_loc}': {e}")
        return {
            "status": "error",
            "location": raw_loc,
            "message": f"Failed to fetch current time for '{raw_loc}': {e}",
        }
