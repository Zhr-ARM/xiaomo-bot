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
        logger.error("Failed to fetch weather: %s", e)
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
        return "(´;ω;`) 天气数据获取失败喵..."

    if target_date.date() == today.date():
        return _format_weather(data, label)
    else:
        # wttr.in 免费版只提供今天数据，明天/后天给简要说明
        days = (target_date.date() - today.date()).days
        forecast = data.get("weather", [])
        if days <= len(forecast):
            f = forecast[days]
            high = f.get("maxtempC", "?")
            low = f.get("mintempC", "?")
            desc_en = f.get("hourly", [{}])[4].get("weatherDesc", [{}])[0].get("value", "未知")
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
        text = "(´;ω;`) 今天天气数据获取失败喵..."
    else:
        label = datetime.now(CST).strftime("%m月%d日")
        text = _format_weather(data, label, f"(=^･ω･^=) 早上好！小源天气播报～ {label} 成都")
        text += "\n\n今天也要元气满满地写代码喵～"

    try:
        bot = get_bot()
        await bot.send_group_msg(group_id=int(TARGET_GROUP), message=text)
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
