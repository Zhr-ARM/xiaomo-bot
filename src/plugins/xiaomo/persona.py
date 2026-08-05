"""小源 QQ 机器人 - 猫娘人设"""
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .lunar import format_almanac

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
    group_id: str | None = None,
) -> str:
    persona = _load_persona()
    memory = _load_memory()

    now = datetime.now(CST)

    # 时间感知：让 LLM 知道当前精确时间
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    time_context = f"\n\n## 当前时间\n现在是 {now.strftime('%Y年%m月%d日')} {weekday}，北京时间 {now.strftime('%H:%M')}"

    # Keep time awareness subtle; it must never imply physical presence.
    if 0 <= now.hour < 6:
        persona += (
            "\n\n现在是凌晨。闲聊可以稍微显得困一点，语气短些；"
            "有人认真求助时仍要正常、准确地回答。"
        )
    elif 6 <= now.hour < 9:
        persona += (
            "\n\n现在是早上。有人先打招呼时可以轻松回应，"
            "不要无缘无故播报问候、黄历或天气。"
        )

    parts = [persona, time_context, format_almanac(now.date())]

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
        if profile_data.get("style_notes"):
            p.append(f"- 互动习惯：{', '.join(profile_data['style_notes'])}")
        parts.extend(p)

    # 情绪惯性：保持跨轮次角色连贯
    if group_id:
        from .state import get_group_mood
        current_mood = get_group_mood(group_id)
        if current_mood:
            mood_hints = {
                "snarky": "还在毒舌损人模式——保持这个调调但可以自然过渡，别突然变脸",
                "playful": "还在调皮捣蛋模式——保持这个调调但可以自然过渡，别突然变脸",
                "gentle": "还在温柔善良模式——保持这个调调但可以自然过渡，别突然变脸",
                "energetic": "还在元气满满模式——保持这个调调但可以自然过渡，别突然变脸",
                "elegant": "还在优雅知性模式——保持这个调调但可以自然过渡，别突然变脸",
                "cute": "还在可爱撒娇模式——保持这个调调但可以自然过渡，别突然变脸",
            }
            hint = mood_hints.get(current_mood["mood"], "")
            if hint:
                parts.append(f"\n## 当前情绪状态\n你刚才{hint}。")

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
            "\n## 当前任务：讲个冷笑话\n"
            "讲一个冷笑话（1-3句话），用猫娘语气讲。\n"
            "- 可以玩技术梗，也可以玩日常梗——好笑第一\n"
            "- 可以调侃程序员日常、群友翻车、猫娘日常等\n"
            "- 不要太正经，讲完自己先笑了最好"
        )

    return "\n".join(parts)
