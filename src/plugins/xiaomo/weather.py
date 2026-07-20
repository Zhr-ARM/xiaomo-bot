"""小源 QQ 机器人 - 成都天气预报 (每日定时 + 随时查询)"""
import logging
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
        return target
    allowed = cfg.get("allowed_group_ids", [])
    return allowed[0] if allowed else ""

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


async def _fetch_raw() -> dict | None:
    """从 wttr.in 获取原始天气 JSON"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://wttr.in/Chengdu",
                params={"format": "j1"},
                headers={"Accept-Language": "zh-CN"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch wttr.in weather: %s: %r", type(e).__name__, str(e))
        return None


async def _fetch_open_meteo_raw() -> dict | None:
    """从 Open-Meteo 获取成都天气，作为 wttr.in 失败时的兜底数据源。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
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
                    "timezone": "Asia/Shanghai",
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("Failed to fetch Open-Meteo weather: %s: %r", type(e).__name__, str(e))
        return None


def _format_weather(data: dict, date_label: str, greeting: str = "") -> str:
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
            lines.append(f"(=^･ω･^=) 成都天气 {date_label}")

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
) -> str:
    """格式化 Open-Meteo 数据。"""
    try:
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        dates = daily.get("time") or []
        date_key = target_date.date().isoformat()
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
            lines.append(f"(=^･ω･^=) 成都天气 {date_label}（Open-Meteo 兜底）")

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


async def query_weather(date_str: str = "") -> str:
    """查询成都天气。date_str 为空则返回今天，支持 "明天"、"后天" """
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

    data = await _fetch_raw()
    if not data:
        open_meteo_data = await _fetch_open_meteo_raw()
        if open_meteo_data:
            formatted = _format_open_meteo_weather(
                open_meteo_data,
                target_date,
                label,
                include_current=target_date.date() == today.date(),
            )
            if formatted:
                return formatted
        return "(´;ω;`) 天气数据获取失败喵..."

    if target_date.date() == today.date():
        return _format_weather(data, label)
    else:
        # wttr.in 免费版只提供今天数据，明天/后天给简要说明
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
                f"(=^･ω･^=) 成都 {label} 预报\n"
                f"☁ {desc}  |  🌡 {low}°C ~ {high}°C\n"
                f"仅供参考喵～临近日期会更准确"
            )
        return f"(´･ω･`) {label} 的预报暂时查不到喵，等临近一点再问？"


async def _post_weather():
    """每天早上 8:00 定时推送"""
    data = await _fetch_raw()
    if not data:
        label = datetime.now(CST).strftime("%m月%d日")
        open_meteo_data = await _fetch_open_meteo_raw()
        if open_meteo_data:
            text = _format_open_meteo_weather(
                open_meteo_data,
                datetime.now(CST),
                label,
                greeting=f"(=^･ω･^=) 早上好！小源天气播报～ {label} 成都",
            )
        else:
            text = "(´;ω;`) 今天天气数据获取失败喵..."
    else:
        label = datetime.now(CST).strftime("%m月%d日")
        text = _format_weather(data, label, f"(=^･ω･^=) 早上好！小源天气播报～ {label} 成都")
    if "天气数据获取失败" not in text:
        text += "\n\n今天也要元气满满喵～"
        # 附上今日黄历宜忌
        try:
            from .lunar import format_almanac
            almanac = format_almanac()
            # 把黄历转成自然口语化的一两句话
            lines = almanac.split("\n")
            yi_line = next((l for l in lines if l.startswith("- 宜：")), "")
            ji_line = next((l for l in lines if l.startswith("- 忌：")), "")
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
        await bot.send_group_msg(group_id=int(TARGET_GROUP), message=text)
        from . import state
        state.record_bot_reply(TARGET_GROUP)
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
