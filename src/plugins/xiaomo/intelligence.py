"""Small social-intelligence layer for group-chat understanding.

This module stays local and deterministic: it does not call the LLM.  Its job is
to summarize the current social situation, choose obvious tools, and apply a
light post-check so the final reply feels more like a restrained group member.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .filter_utils import trim_to_length
from .web_search import detect_explicit_search, is_natural_candidate


TECHNICAL_CUES = (
    "bug", "报错", "异常", "怎么修", "咋修", "配置", "代码", "编译", "运行",
    "接口", "数据库", "python", "node", "git", "部署", "依赖", "环境",
)
SUPPORT_CUES = (
    "难受", "崩了", "烦", "寄了", "救命", "不会了", "裂开", "累", "焦虑",
    "害怕", "压力", "失眠", "想哭", "委屈", "孤独", "撑不住", "顶不住",
)
IMAGE_CUES = ("图片", "图", "看图", "识图", "识别", "这张图", "那个图")
WEATHER_CUES = (
    "天气", "天气预报", "冷不冷", "热不热", "下雨", "带伞", "几度", "多少度",
    "温度", "气温", "降温", "刮风", "风大", "外面冷", "外面热",
)
SOCIAL_ACK_RE = re.compile(
    r"^(?:谢了|谢谢|多谢|不客气|收到|懂了|明白了|好嘞|行|可以|早|早上好|"
    r"晚上好|晚安|你好|嗨|拜拜|回头见)[啊呀啦呢哈～~!！。]*$"
)
PERSONAL_SHARE_CUES = (
    "我刚", "我今天", "我终于", "我准备", "我打算", "我发现", "我感觉",
    "我好像", "我已经", "我现在", "刚刚我", "终于把", "总算把",
)


@dataclass
class ToolPlan:
    needs_search: bool = False
    search_query: str = ""
    search_required: bool = False
    needs_weather: bool = False
    weather_query: str = ""
    needs_image: bool = False
    needs_memory: bool = True
    confidence: float = 0.5
    reason: str = "default"


@dataclass
class ConversationFrame:
    current_user_qq: str
    current_text: str
    explicit_trigger: bool
    scene: str
    tone: str
    reply_goal: str
    max_chars: int
    batch_context: str = ""
    tool_plan: ToolPlan | None = None


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(cue.lower() in lowered for cue in cues)


def classify_scene(text: str, *, explicit_trigger: bool = False) -> str:
    clean = text.strip()
    if not clean:
        return "empty"
    if _contains_any(clean, WEATHER_CUES):
        return "weather"
    if _contains_any(clean, IMAGE_CUES):
        return "image_question"
    if _contains_any(clean, TECHNICAL_CUES):
        return "technical_help"
    if _contains_any(clean, SUPPORT_CUES):
        return "support"
    if SOCIAL_ACK_RE.fullmatch(clean):
        return "social_ack"
    if detect_explicit_search(clean) or is_natural_candidate(clean):
        return "live_info"
    if _contains_any(clean, PERSONAL_SHARE_CUES):
        return "personal_share"
    has_question_shape = any(
        cue in clean
        for cue in ("?", "？", "吗", "呢", "怎么", "咋", "为什么", "为何", "谁", "哪", "什么", "多少")
    )
    if len(clean) <= 14 or has_question_shape:
        return "casual_question" if explicit_trigger else "casual_banter"
    return "group_flow"


def plan_tools(
    text: str,
    *,
    explicit_trigger: bool = False,
    existing_search_query: str = "",
    existing_weather_query: str = "",
) -> ToolPlan:
    clean = (text or "").strip()
    plan = ToolPlan()

    if existing_weather_query:
        plan.needs_weather = True
        plan.weather_query = existing_weather_query
        plan.confidence = 0.95
        plan.reason = "weather intent from message intake"
        return plan

    if _contains_any(clean, WEATHER_CUES):
        plan.needs_weather = True
        plan.weather_query = clean
        plan.confidence = 0.9
        plan.reason = "weather cue"
        return plan

    if _contains_any(clean, IMAGE_CUES):
        plan.needs_image = True
        plan.confidence = 0.8
        plan.reason = "image cue"

    candidate = existing_search_query or clean
    explicit_query = detect_explicit_search(candidate)
    if explicit_query:
        plan.needs_search = True
        plan.search_query = candidate
        plan.search_required = True
        plan.confidence = 0.95
        plan.reason = "explicit search command"
        return plan

    if explicit_trigger and is_natural_candidate(candidate):
        plan.needs_search = True
        plan.search_query = candidate
        plan.search_required = True
        plan.confidence = 0.78
        plan.reason = "explicit live-info candidate"
        return plan

    if is_natural_candidate(candidate):
        plan.needs_search = True
        plan.search_query = candidate
        plan.search_required = False
        plan.confidence = 0.68
        plan.reason = "live-info cue"

    return plan


def build_conversation_frame(
    *,
    current_msg: dict,
    raw_text: str,
    batch_context: str = "",
    explicit_trigger: bool = False,
    search_query: str = "",
    weather_query: str = "",
) -> ConversationFrame:
    text = (raw_text or "").strip()
    scene = classify_scene(text, explicit_trigger=explicit_trigger)
    tool_plan = plan_tools(
        text,
        explicit_trigger=explicit_trigger,
        existing_search_query=search_query,
        existing_weather_query=weather_query,
    )

    tone = "brief"
    goal = "像群友一样自然接一句，别抢话。"
    max_chars = 220
    if scene == "technical_help":
        tone = "serious"
        goal = "先给判断，再给必要步骤；不确定就说怎么验证。"
        max_chars = 700
    elif scene == "support":
        tone = "supportive"
        goal = "先接住情绪，再给一点实际建议。"
        max_chars = 320
    elif scene == "live_info":
        tone = "serious_brief"
        goal = "需要实时信息时先查；没查到就承认，不要编。"
        max_chars = 520
    elif scene == "weather":
        tone = "casual_use_tool"
        goal = "把天气数据揉进一句自然建议里，不要像播报表格。"
        max_chars = 320
    elif scene == "image_question":
        tone = "observational"
        goal = "基于图片内容回答，看不清就说明限制。"
        max_chars = 380
    elif scene in {"casual_banter", "casual_question"}:
        tone = "playful_brief"
        goal = "短短接住，可以反问，别展开成长答案。"
        max_chars = 180
    elif scene == "personal_share":
        tone = "warm_brief"
        goal = "回应分享里最具体的那一点，别复述、别默认追问，也别追加未被请求的建议。"
        max_chars = 160
    elif scene == "social_ack":
        tone = "brief"
        goal = "像群友一样顺手回半句，不延伸新话题。"
        max_chars = 60

    return ConversationFrame(
        current_user_qq=str(current_msg.get("user_qq") or ""),
        current_text=text,
        explicit_trigger=explicit_trigger,
        scene=scene,
        tone=tone,
        reply_goal=goal,
        max_chars=max_chars,
        batch_context=batch_context,
        tool_plan=tool_plan,
    )


def build_frame_instruction(frame: ConversationFrame) -> str:
    plan = frame.tool_plan or ToolPlan()
    lines = [
        "[群聊理解]",
        f"- 场景: {frame.scene}",
        f"- 语气: {frame.tone}",
        f"- 回复目标: {frame.reply_goal}",
        f"- 建议长度: {frame.max_chars} 字以内",
        f"- 工具计划: search={plan.needs_search}, weather={plan.needs_weather}, image={plan.needs_image}, memory={plan.needs_memory}",
        f"- 工具原因: {plan.reason}",
        "- 像群友一样接话：先回应语境，再给答案；别复述本段分析。",
    ]
    if plan.needs_search and plan.search_required:
        lines.append("- 这轮需要实时信息；没有搜索结果时必须明说无法确认。")
    return "\n".join(lines) + "\n[/群聊理解]"


def post_check_reply(
    reply: str,
    *,
    frame: ConversationFrame,
    style: str,
    default_max_chars: int = 800,
) -> str:
    text = (reply or "").strip()
    if not text:
        return text

    # Drop accidental prompt leakage.
    banned_prefixes = (
        "[CURRENT_SPEAKER]", "[CURRENT_MESSAGE]", "[回复策略]", "[群聊理解]",
        "作为AI", "作为一个AI", "我是一个AI",
    )
    for prefix in banned_prefixes:
        if text.startswith(prefix):
            text = re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
            text = text.replace("作为一个AI", "").replace("作为AI", "").strip()

    # Soften repetitive verbal tics.
    text = re.sub(r"(喵[～~嗷呜]*){3,}", "喵～", text)
    text = re.sub(r"(哈){5,}", "哈哈", text)

    limit = min(default_max_chars, frame.max_chars or default_max_chars)
    if style in {"brief", "playful", "ask_back"}:
        limit = min(limit, 220)
    if frame.scene in {"casual_banter", "casual_question"}:
        limit = min(limit, 180)
    return trim_to_length(text, limit)
