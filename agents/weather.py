# -*- coding: utf-8 -*-
"""
室外天气：从 OpenWeather API 拉取当前温湿度，供仿真物理引擎使用。
API Key 需在 .env 中配置；城市名在 agent_config.OPENWEATHER_CITY（默认 Beijing），也可用 .env 覆盖。
"""
import os
from typing import Dict, Any

try:
    import requests
except ImportError:
    requests = None


def fetch_openweather() -> Dict[str, float]:
    """
    调用 OpenWeather 当前天气接口，返回 { "temperature": float(°C), "humidity": float(0-1) }。
    未配置 API Key 或请求失败时返回空 dict，由调用方使用默认值。
    """
    api_key = (
        os.environ.get("OPENWEATHER_API_KEY", "").strip()
        or os.environ.get("WEATHER_API_KEY", "").strip()
    )
    if not api_key:
        return {}

    if requests is None:
        return {}

    try:
        from agent_config import OPENWEATHER_CITY as CONFIG_CITY
    except Exception:
        CONFIG_CITY = "Beijing"
    city = os.environ.get("OPENWEATHER_CITY", "").strip() or os.environ.get("WEATHER_CITY", "").strip() or CONFIG_CITY
    lat = os.environ.get("OPENWEATHER_LAT", "").strip()
    lon = os.environ.get("OPENWEATHER_LON", "").strip()
    if not city and not (lat and lon):
        city = CONFIG_CITY

    if city:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": city, "appid": api_key, "units": "metric"}
    elif lat and lon:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"lat": lat, "lon": lon, "appid": api_key, "units": "metric"}
    else:
        return {}

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        main = data.get("main") or {}
        temp = main.get("temp")
        humidity_pct = main.get("humidity")
        if temp is None:
            return {}
        temperature = float(temp)
        humidity = float(humidity_pct or 50) / 100.0
        humidity = max(0.0, min(1.0, humidity))
        return {"temperature": round(temperature, 2), "humidity": round(humidity, 2)}
    except Exception:
        return {}
