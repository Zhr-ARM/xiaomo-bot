"""小源 QQ 机器人 - 主入口

使用方式:
    python bot.py

前置条件:
    1. 安装 NapCatQQ 或兼容的 OneBot v11 实现
    2. 配置 .env 文件中的 LLM_API_KEY 和 OneBot 连接参数
    3. 安装依赖: pip install -e .

说明:
    小源仅支持群聊，不处理私聊消息。
"""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

# 初始化 NoneBot2
nonebot.init()

# 注册 OneBot v11 适配器
driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

# 加载插件
nonebot.load_plugins("src/plugins")

if __name__ == "__main__":
    nonebot.run()
