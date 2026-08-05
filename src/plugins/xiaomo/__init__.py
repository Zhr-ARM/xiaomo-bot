"""小源 QQ 机器人 - 插件入口"""
from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from .observability import configure_logging

__plugin_meta__ = PluginMetadata(
    name="小源",
    description="自然参与群聊、能认真处理技术问题的小源",
    usage="仅支持群聊：@小源或直接叫小源即可触发",
    config=None,
)

driver = get_driver()
configure_logging()


@driver.on_startup
async def _startup():
    """NoneBot2 启动时初始化"""
    from .database import init_database
    await init_database()

    from . import state
    state.db_initialized = True

    from .runtime_state import restore
    await restore()

    from .health import install_health_routes
    install_health_routes()

    from .vector_store import start_vector_store_init
    start_vector_store_init()

    from .handlers import setup_silent_callback
    setup_silent_callback()

    from .auto_action import start_bubble_loop
    await start_bubble_loop()

    from .weather import start_weather_scheduler
    start_weather_scheduler()


@driver.on_shutdown
async def _shutdown():
    """关闭时清理"""
    from .weather import stop_weather_scheduler
    stop_weather_scheduler()

    from .auto_action import stop_bubble_loop
    await stop_bubble_loop()

    from .window import get_silent_window
    await get_silent_window().close()

    from .runtime_state import shutdown as shutdown_runtime_state
    await shutdown_runtime_state()

    from .memory import close_memory_tasks
    await close_memory_tasks()

    from .vector_store import close_vector_store
    await close_vector_store()

    from .database import close_database
    await close_database()
