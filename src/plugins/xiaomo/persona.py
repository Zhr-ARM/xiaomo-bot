"""小源 QQ 机器人 - 猫娘人设"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))


def _get_data_dir() -> Path:
    return Path(__file__).parent.parent.parent.parent / "data"


def _load_persona() -> str:
    """加载机器人人设。优先 data/persona.md，回退到 data/persona.example.md"""
    persona_path = _get_data_dir() / "persona.md"
    example_path = _get_data_dir() / "persona.example.md"
    if persona_path.exists():
        return persona_path.read_text(encoding="utf-8")
    if example_path.exists():
        return example_path.read_text(encoding="utf-8")
    return "你是小源，一个友好的QQ聊天机器人。"


def _load_memory() -> str:
    """加载群聊记忆库。data/memory.md 不存在则返回空"""
    p = _get_data_dir() / "memory.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def build_system_prompt(
    scene: str = "group",
    user_profile: dict | None = None,
    mode: str = "normal",
) -> str:
    persona = _load_persona()
    memory = _load_memory()

    now = datetime.now(CST)

    # 时间段动态附加
    if 0 <= now.hour < 6:
        persona += (
            "\n\n## 深夜模式\n"
            "- 现在是凌晨，你是一只困倦的猫娘，说话带困意，多打哈欠 (´;ω;`)\n"
            "- 颜文字多用犯困系：(。-ω-)zzz (_　_)。゜zｚＺ (´-﹏-`；)\n"
            "- 每句回复末尾温柔地提醒对方早点休息\n"
            "- 不要聊太深入的技术问题，建议对方明天再弄"
        )
    elif 6 <= now.hour < 9:
        persona += (
            "\n\n## 早安模式\n"
            "- 现在是早上，元气满满的猫娘 (=^･ω･^=)\n"
            "- 打招呼可以说\"早上好\"，鼓励大家今天也好好写代码"
        )

    parts = [persona]

    if memory:
        parts.append(f"\n## 群聊记忆库\n{memory}")

    if user_profile and user_profile.get("exists"):
        p = ["\n## 当前成员"]
        if user_profile.get("nickname"):
            p.append(f"- 昵称：{user_profile['nickname']}")
        if user_profile.get("nicknames"):
            p.append(f"- 其他称呼：{', '.join(user_profile['nicknames'])}")
        if user_profile.get("total_messages"):
            p.append(f"- 已互动 {user_profile['total_messages']} 条消息")
        profile_data = user_profile.get("profile", {})
        if profile_data.get("topics"):
            p.append(f"- 感兴趣的方向：{', '.join(profile_data['topics'])}")
        parts.extend(p)

    if mode == "praise":
        parts.append(
            "\n## 当前任务：夸夸\n"
            "请根据用户的历史发言和画像，写一段真诚的夸赞（3-5句话）。\n"
            "- 要具体，结合对方的技术方向、性格特点\n"
            "- 语气热情但不油腻，像朋友之间的认可\n"
            "- 可以提对方在协会的贡献或特长"
        )
    elif mode == "roast":
        parts.append(
            "\n## 当前任务：点草（友好吐槽）\n"
            "请根据用户的历史发言和画像，写一段友好的吐槽（3-5句话）。\n"
            "- 要幽默不伤人，像损友之间的调侃\n"
            "- 结合对方的技术方向和性格来吐槽\n"
            "- 结尾加一句鼓励，别让人真的生气\n"
            "- 绝对禁止人身攻击、外貌评价、敏感话题"
        )
    elif mode == "joke":
        parts.append(
            "\n## 当前任务：嵌入式冷笑话\n"
            "讲一个嵌入式/电子/编程相关的冷笑话（1-3句话）。\n"
            "- 可以玩技术梗（STM32、ROS、PCB、示波器、寄存器等）\n"
            "- 可以调侃程序员和硬件工程师的日常\n"
            "- 好笑第一，不要太正经"
        )

    return "\n".join(parts)
