"""小源 QQ 机器人 - 插件入口"""
from nonebot import get_driver
from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="小源",
    description="一只可爱的猫娘QQ聊天机器人 (｡･ω･｡)",
    usage="私聊直接对话 / 群聊@或叫小源即可触发喵~",
    config=None,
)

driver = get_driver()


@driver.on_startup
async def _startup():
    """NoneBot2 启动时初始化"""
    from .database import init_database
    await init_database()

    from .vector_store import init_vector_store
    await init_vector_store()

    from .handlers import setup_silent_callback
    setup_silent_callback()

    from .auto_action import start_bubble_loop
    await start_bubble_loop()

    from .weather import start_weather_scheduler
    start_weather_scheduler()


@driver.on_shutdown
async def _shutdown():
    """关闭时清理"""
    from .database import close_database
    await close_database()

    from .vector_store import close_vector_store
    await close_vector_store()

    from .weather import stop_weather_scheduler
    stop_weather_scheduler()
