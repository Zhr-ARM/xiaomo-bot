"""小源 QQ 机器人 - 多城市天气预报 (每日定时 + 随时查询)"""
import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bot

logger = logging.getLogger("xiaomo.weather")

CST = timezone(timedelta(hours=8))
def _get_target_group() -> str:
    """从配置获取天气推送目标群号"""
    from .config import get_config
    cfg = get_config()
    weather_cfg = cfg.get("weather", {})
    target = weather_cfg.get("target_group", "")
    if target:
        return str(target)
    allowed = cfg.get("allowed_group_ids", [])
    return str(allowed[0]) if allowed else ""

TARGET_GROUP = ""  # 运行时从配置读取
_scheduler: AsyncIOScheduler | None = None

_WEATHER_CN = {
    "Sunny": "晴天", "Clear": "晴朗", "Partly cloudy": "多云",
    "Cloudy": "阴天", "Overcast": "阴天", "Mist": "薄雾",
    "Fog": "雾", "Light rain": "小雨", "Moderate rain": "中雨",
    "Heavy rain": "大雨", "Light drizzle": "毛毛雨",
    "Patchy rain nearby": "局部阵雨", "Thunderstorm": "雷阵雨",
    "Light snow": "小雪", "Moderate snow": "中雪",
}

_WIND_CN = {
    "N": "北", "NNE": "东北偏北", "NE": "东北", "ENE": "东北偏东",
    "E": "东", "ESE": "东南偏东", "SE": "东南", "SSE": "东南偏南",
    "S": "南", "SSW": "西南偏南", "SW": "西南", "WSW": "西南偏西",
    "W": "西", "WNW": "西北偏西", "NW": "西北", "NNW": "西北偏北",
}

CHENGDU_LATITUDE = 30.5728
CHENGDU_LONGITUDE = 104.0668
_LOCATION_CACHE: dict[str, tuple[float, dict]] = {}
_WEATHER_CACHE: dict[str, tuple[float, dict]] = {}

_OPEN_METEO_WEATHER_CN = {
    0: "晴朗",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴天",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    80: "阵雨",
    81: "较强阵雨",
    82: "强阵雨",
    95: "雷阵雨",
}


def _open_meteo_desc(code) -> str:
    try:
        return _OPEN_METEO_WEATHER_CN.get(int(code), f"天气代码 {code}")
    except (TypeError, ValueError):
        return "未知"


def _wind_dir_from_degrees(value) -> str:
    try:
        degrees = float(value) % 360
    except (TypeError, ValueError):
        return "未知"
    labels = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return labels[int((degrees + 22.5) // 45) % 8]


def _fmt_num(value, digits: int = 0) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def _fmt_clock(value) -> str:
    text = str(value or "")
    if "T" in text:
        return text.split("T", 1)[1]
    return text or "?"


async def _fetch_raw(city: str = "成都") -> dict | None:
    """从 wttr.in 获取原始天气 JSON"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"https://wttr.in/{city}",
                params={"format": "j1"},
                headers={"Accept-Language": "zh-CN"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch wttr.in weather: %s: %r", type(e).__name__, str(e))
        return None


async def _fetch_open_meteo_raw(
    latitude: float = CHENGDU_LATITUDE,
    longitude: float = CHENGDU_LONGITUDE,
    timezone_name: str = "Asia/Shanghai",
) -> dict | None:
    """Fetch a forecast from Open-Meteo with a short cache."""
    cache_key = f"{latitude:.4f}:{longitude:.4f}:{timezone_name}"
    cached = _WEATHER_CACHE.get(cache_key)
    if cached and time.time() - cached[0] <= 300:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": CHENGDU_LATITUDE,
                    "longitude": CHENGDU_LONGITUDE,
                    "current": (
                        "temperature_2m,relative_humidity_2m,apparent_temperature,"
                        "weather_code,wind_speed_10m,wind_direction_10m,uv_index"
                    ),
                    "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,weather_code",
                    "timezone": timezone_name or "auto",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if len(_WEATHER_CACHE) >= 128:
                oldest = min(_WEATHER_CACHE, key=lambda key: _WEATHER_CACHE[key][0])
                _WEATHER_CACHE.pop(oldest, None)
            _WEATHER_CACHE[cache_key] = (time.time(), data)
            return data
    except Exception as e:
        logger.warning("Failed to fetch Open-Meteo weather: %s: %r", type(e).__name__, str(e))
        return None


def _default_city() -> str:
    from .config import get_config

    return str(get_config().get("weather", {}).get("city", "成都") or "成都")


def extract_city(query: str, *, default: str | None = None) -> str:
    """Extract a likely city name without mistaking question words for places."""

    fallback = default or _default_city()
    text = (query or "").strip()
    if not text:
        return fallback
    clean = re.sub(r"(?:今天|明天|后天|现在|最近|这几天|当地)", "", text)
    clean = re.sub(
        r"^(?:小源|帮我|给我|麻烦|想知道|我想知道|查一下|查查|看看|看下|问下)+",
        "",
        clean,
    )
    cue_pattern = (
        r"(?:天气(?:预报)?|气温|温度|几度|会不会下雨|下雨吗|冷不冷|热不热|"
        r"要不要带伞|需要带伞)"
    )
    match = re.search(rf"([\u4e00-\u9fff]{{2,10}}?)(?:市)?(?:的)?{cue_pattern}", clean)
    if match:
        candidate = match.group(1)
        candidate = re.sub(r"^(?:我想知道|想知道|请问|问下|看看)", "", candidate)
        if candidate not in {"外面", "这里", "那边", "本地", "今日", "明日"}:
            return candidate
    latin = re.search(r"([A-Za-z][A-Za-z .'-]{1,30})\s+(?:weather|temperature)", clean, re.I)
    if latin:
        return latin.group(1).strip()
    return fallback


async def _resolve_location(city: str) -> dict | None:
    normalized = city.strip()
    if normalized in {"成都", "成都市", "Chengdu"}:
        return {
            "name": "成都",
            "latitude": CHENGDU_LATITUDE,
            "longitude": CHENGDU_LONGITUDE,
            "timezone": "Asia/Shanghai",
        }
    cached = _LOCATION_CACHE.get(normalized)
    if cached and time.time() - cached[0] <= 86400:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": normalized,
                    "count": 1,
                    "language": "zh",
                    "format": "json",
                },
            )
            response.raise_for_status()
            results = response.json().get("results") or []
    except Exception as exc:
        logger.warning("Weather geocoding failed for %s: %r", normalized, exc)
        return None
    if not results:
        return None
    location = results[0]
    resolved = {
        "name": location.get("name") or normalized,
        "latitude": float(location["latitude"]),
        "longitude": float(location["longitude"]),
        "timezone": location.get("timezone") or "auto",
    }
    if len(_LOCATION_CACHE) >= 128:
        oldest = min(_LOCATION_CACHE, key=lambda key: _LOCATION_CACHE[key][0])
        _LOCATION_CACHE.pop(oldest, None)
    _LOCATION_CACHE[normalized] = (time.time(), resolved)
    return resolved


def _format_weather(
    data: dict,
    date_label: str,
    greeting: str = "",
    *,
    city: str = "成都",
) -> str:
    """格式化天气数据为消息文本"""
    try:
        cur = data["current_condition"][0]
        day = data["weather"][0]

        temp_c = cur["temp_C"]
        feels_like = cur["FeelsLikeC"]
        humidity = cur["humidity"]
        desc_en = cur["weatherDesc"][0]["value"]
        desc = _WEATHER_CN.get(desc_en, desc_en)
        wind_spd = cur["windspeedKmph"]
        wind_dir_en = cur["winddir16Point"]
        wind_dir = _WIND_CN.get(wind_dir_en, wind_dir_en)
        uv = cur["uvIndex"]
        high = day["maxtempC"]
        low = day["mintempC"]
        sunrise = day["astronomy"][0]["sunrise"]
        sunset = day["astronomy"][0]["sunset"]

        lines = []
        if greeting:
            lines.append(greeting)
        else:
            lines.append(f"(=^･ω･^=) {city}天气 {date_label}")

        lines += [
            f"☁ {desc}",
            f"🌡 {temp_c}°C（体感 {feels_like}°C）  |  {low}°C ~ {high}°C",
            f"💧 湿度 {humidity}%  |  🌬 {wind_dir}风 {wind_spd} km/h  |  ☀ 紫外线 {uv}",
            f"🌅 日出 {sunrise}  |  🌇 日落 {sunset}",
        ]
        return "\n".join(lines)
    except Exception as e:
        logger.exception("Failed to format weather: %s", e)
        return ""


def _format_open_meteo_weather(
    data: dict,
    target_date,
    date_label: str,
    *,
    include_current: bool = True,
    greeting: str = "",
    city: str = "成都",
) -> str:
    """格式化 Open-Meteo 数据。"""
    try:
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        date_key = target_date.date().isoformat()
        if not include_current and dates and date_key not in dates:
            return ""
        idx = dates.index(date_key) if date_key in dates else 0

        high = (daily.get("temperature_2m_max") or ["?"])[idx]
        low = (daily.get("temperature_2m_min") or ["?"])[idx]
        sunrise = (daily.get("sunrise") or ["?"])[idx]
        sunset = (daily.get("sunset") or ["?"])[idx]
        day_code = (daily.get("weather_code") or [current.get("weather_code")])[idx]
        desc = _open_meteo_desc(day_code)

        lines = []
        if greeting:
            lines.append(greeting)
        else:
            lines.append(f"(=^･ω･^=) {city}天气 {date_label}")

        if include_current and current:
            lines += [
                f"☁ {desc}",
                (
                    f"🌡 {_fmt_num(current.get('temperature_2m'), 0)}°C"
                    f"（体感 {_fmt_num(current.get('apparent_temperature'), 0)}°C）"
                    f" |  {_fmt_num(low, 0)}°C ~ {_fmt_num(high, 0)}°C"
                ),
                (
                    f"💧 湿度 {_fmt_num(current.get('relative_humidity_2m'), 0)}%"
                    f"  |  🌬 {_wind_dir_from_degrees(current.get('wind_direction_10m'))}风"
                    f" {_fmt_num(current.get('wind_speed_10m'), 0)} km/h"
                    f"  |  ☀ 紫外线 {_fmt_num(current.get('uv_index'), 1)}"
                ),
            ]
        else:
            lines += [
                f"☁ {desc}",
                f"🌡 {_fmt_num(low, 0)}°C ~ {_fmt_num(high, 0)}°C",
            ]

        lines.append(f"🌅 日出 {_fmt_clock(sunrise)}  |  🌇 日落 {_fmt_clock(sunset)}")
        return "\n".join(lines)
    except Exception as e:
        logger.exception("Failed to format Open-Meteo weather: %s", e)
        return ""


async def _query_weather_inner(date_str: str = "") -> str:
    """Resolve a city and fetch its forecast."""
    today = datetime.now(CST)
    target_date = today
    label = today.strftime("%m月%d日")

    if date_str:
        s = date_str.strip()
        if "明天" in s:
            target_date = today + timedelta(days=1)
            label = target_date.strftime("%m月%d日")
        elif "后天" in s:
            target_date = today + timedelta(days=2)
            label = target_date.strftime("%m月%d日")

    city = extract_city(date_str)
    location = await _resolve_location(city)
    open_meteo_data = None
    if location:
        is_chengdu = location["name"] == "成都"
        if is_chengdu:
            open_meteo_data = await _fetch_open_meteo_raw()
        else:
            open_meteo_data = await _fetch_open_meteo_raw(
                location["latitude"],
                location["longitude"],
                location["timezone"],
            )
        if open_meteo_data:
            formatted = _format_open_meteo_weather(
                open_meteo_data,
                target_date,
                label,
                include_current=target_date.date() == today.date(),
                city=str(location["name"]),
            )
            if formatted:
                return formatted

    data = await (_fetch_raw() if city == "成都" else _fetch_raw(city))
    if not data:
        if location is None:
            return f"(´･ω･`) 没定位到“{city}”，换个城市名再问我？"
        return "(´;ω;`) 天气数据获取失败喵..."

    if target_date.date() == today.date():
        return _format_weather(data, label, city=city)
    else:
        days = (target_date.date() - today.date()).days
        forecast = data.get("weather", [])
        if 0 <= days < len(forecast):
            f = forecast[days]
            high = f.get("maxtempC", "?")
            low = f.get("mintempC", "?")
            hourly = f.get("hourly") or []
            hour_data = hourly[4] if len(hourly) > 4 else (hourly[0] if hourly else {})
            desc_en = hour_data.get("weatherDesc", [{}])[0].get("value", "未知")
            desc = _WEATHER_CN.get(desc_en, desc_en)
            return (
                f"(=^･ω･^=) {city} {label} 预报\n"
                f"☁ {desc}  |  🌡 {low}°C ~ {high}°C\n"
                f"仅供参考喵～临近日期会更准确"
            )
        return f"(´･ω･`) {label} 的预报暂时查不到喵，等临近一点再问？"


async def query_weather(date_str: str = "") -> str:
    """Query weather with one bounded end-to-end timeout."""

    try:
        return await asyncio.wait_for(_query_weather_inner(date_str), timeout=9.0)
    except asyncio.TimeoutError:
        logger.warning("Weather query timed out: %s", date_str[:80])
        return "(´;ω;`) 天气服务这会儿有点慢，过一会儿再问我吧"


async def _post_weather():
    """每天早上 8:00 定时推送"""
    city = _default_city()
    label = datetime.now(CST).strftime("%m月%d日")
    text = await query_weather(f"{city}今天天气")
    weather_ok = not any(
        cue in text
        for cue in ("天气数据获取失败", "天气服务这会儿", "没定位到")
    )
    if weather_ok:
        lines = text.splitlines()
        if lines:
            lines[0] = f"(=^･ω･^=) 早上好！小源天气播报～ {label} {city}"
            text = "\n".join(lines)
    if weather_ok:
        text += "\n\n今天也要元气满满喵～"
        # 附上今日黄历宜忌
        try:
            from .lunar import format_almanac
            almanac = format_almanac()
            # 把黄历转成自然口语化的一两句话
            lines = almanac.split("\n")
            yi_line = next((line for line in lines if line.startswith("- 宜：")), "")
            ji_line = next((line for line in lines if line.startswith("- 忌：")), "")
            yi = yi_line.replace("- 宜：", "").strip() if yi_line else ""
            ji = ji_line.replace("- 忌：", "").strip() if ji_line else ""
            if yi:
                text += f"\n\n📅 今日宜：{yi}"
            if ji:
                text += f"\n⚠ 今日忌：{ji}"
        except Exception:
            pass

    try:
        bot = get_bot()
        from .delivery import send_group_text

        await send_group_text(bot, TARGET_GROUP, text)
        logger.info("Daily weather posted")
    except Exception as e:
        logger.exception("Failed to post weather: %s", e)


def start_weather_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    global TARGET_GROUP
    TARGET_GROUP = _get_target_group()
    if not TARGET_GROUP:
        logger.warning("Weather scheduler skipped: no target_group configured")
        return
    _scheduler = AsyncIOScheduler(timezone=CST)
    _scheduler.add_job(
        _post_weather, "cron", hour=8, minute=0,
        id="daily_weather", misfire_grace_time=300,
    )
    _scheduler.start()
    logger.info("Weather scheduler started (daily 08:00 CST, group=%s)", TARGET_GROUP)


def stop_weather_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
