"""Per-group behavior overrides and outbound language safeguards."""
from __future__ import annotations

import re
from copy import deepcopy

from .config import get_config


_CODE_SPAN_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")
_PRESERVED_I_WORDS = (
    "自我",
    "忘我",
    "无我",
    "敌我",
    "我方",
    "我国",
    "我校",
    "我司",
)
_SLUR = r"(?:傻[逼屄比]|煞笔|蠢货|废物|弱智|脑残|垃圾人|狗东西|(?<![A-Za-z])sb(?![A-Za-z]))"
_TARGETED_UNCIVIL_RE = re.compile(
    rf"(?:你|他|她|这人|那人|某人|群友)\s*"
    rf"(?:真是|就是|是|也太|怎么这么|简直是?|这种)?\s*"
    rf"(?:个|一个)?\s*{_SLUR}|"
    rf"{_SLUR}[^，。！？!?]{{0,4}}(?:你|他|她)|"
    r"有病吧|去死|滚开|滚蛋|闭嘴|不要脸|你配吗|活该|"
    r"都怪你|全是你的错|你就是故意|你在撒谎",
    flags=re.I,
)
_SLUR_ONLY_RE = re.compile(
    rf"^[“”'\"()（）\s]*{_SLUR}[，。！？!?“”'\"()（）\s]*$",
    flags=re.I,
)
_RECRUITMENT_TOPIC_RE = re.compile(
    r"招新|纳新|社团|学生组织|"
    r"(?:报名|加入|参加).{0,8}(?:协会|社团|部门|组织)|"
    r"(?:协会|社团|部门|组织).{0,8}(?:报名|加入|参加)"
)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def get_group_policy(
    group_id: str | int | None,
    *,
    config: dict | None = None,
) -> dict:
    if group_id is None:
        return {}
    effective_config = config if isinstance(config, dict) else get_config()
    policies = effective_config.get("group_policies", {})
    if not isinstance(policies, dict):
        return {}
    policy = policies.get(str(group_id), {})
    return policy if isinstance(policy, dict) else {}


def get_effective_proactive_join_config(
    group_id: str | int | None,
    *,
    config: dict | None = None,
) -> dict:
    effective_config = config if isinstance(config, dict) else get_config()
    base = effective_config.get("proactive_join", {})
    if not isinstance(base, dict):
        base = {}
    override = get_group_policy(group_id, config=effective_config).get(
        "proactive_join",
        {},
    )
    if not isinstance(override, dict):
        override = {}
    return _deep_merge(base, override)


def build_group_policy_instruction(group_id: str | int | None) -> str:
    policy = get_group_policy(group_id)
    if not policy:
        return ""

    lines = ["[GROUP_POLICY]", f"以下规则只适用于当前群 {group_id}。"]
    self_reference = str(policy.get("self_reference") or "").strip()
    if self_reference:
        lines.extend(
            [
                f"- 所有自指都用“{self_reference}”，不用“我”。例如“我爱你”要说成“{self_reference}爱你”。",
                f"- “我的、我会、我们、咱、咱们、俺、本人、本猫、本喵”等第一人称形式也必须改成“{self_reference}”或“{self_reference}和大家”，不能换一种第一人称绕过去。",
                f"- 不需要每句硬塞“{self_reference}”；可以省略主语，但需要自称时只能用“{self_reference}”。",
            ]
        )

    civil_cfg = policy.get("civil_language", {})
    if isinstance(civil_cfg, dict) and civil_cfg.get("enabled", False):
        lines.extend(
            [
                "- 说话得体，只评价事情和做法，不指责、羞辱、贬低、扣帽子或猜测任何群成员的动机。",
                "- 即使对方先骂人，也不跟骂、不站队攻击；可以简短降温，然后回到事情本身。",
                "- 友好吐槽只能轻微且明显不伤人，不使用针对人的贬义标签。",
            ]
        )

    recruitment = policy.get("recruitment", {})
    if isinstance(recruitment, dict) and recruitment.get("enabled", False):
        website = str(recruitment.get("website") or "").strip()
        lines.extend(
            [
                "- 这个群里可以更积极地介绍成都理工大学开源协会，欢迎对编程、Linux、嵌入式、硬件、机器人、AI、开源项目或竞赛感兴趣的同学了解和参与。",
                f"- 开源协会官方网站是 {website}；只要话题和新生、社团、学技术、项目、竞赛或如何加入有关，可以自然带一句招新邀请和这个链接。",
                "- 当前话题明确提到招新、纳新、社团、学生组织或报名时，如果近期没有发过官网，本轮应当顺势介绍一次开源协会和官网；先尊重其他组织，不拉踩、不贬低。",
                "- 不要在无关话题里硬插广告，不连续刷链接；官网刚发过就不重复。不确定当前报名时间时，只说以官网最新信息为准。",
            ]
        )

    lines.extend(
        [
            "- 可以比其他群更积极地接梗、回答或补充一句，但仍要紧跟当前话题，不抢群友之间的对话。",
            "- 夜间无人说话时保持安静，不为了活跃而发‘有人吗’或空泛招新广告。",
            "[/GROUP_POLICY]",
        ]
    )
    return "\n".join(lines)


def _replace_self_reference(segment: str, self_reference: str) -> str:
    placeholders: dict[str, str] = {}
    clean = segment
    for index, phrase in enumerate(_PRESERVED_I_WORDS):
        marker = f"\x00I{index}\x00"
        if phrase in clean:
            clean = clean.replace(phrase, marker)
            placeholders[marker] = phrase
    clean = clean.replace("你我", f"你和{self_reference}")
    for collective in ("我们的", "咱们的", "俺们的"):
        clean = clean.replace(collective, f"{self_reference}和大家的")
    for collective in ("我们", "咱们", "俺们"):
        clean = clean.replace(collective, f"{self_reference}和大家")
    for singular in ("本人", "本猫", "本喵", "本助手", "鄙人", "俺", "咱"):
        clean = clean.replace(singular, self_reference)
    clean = clean.replace("我", self_reference)
    for marker, phrase in placeholders.items():
        clean = clean.replace(marker, phrase)
    return clean


def apply_outgoing_group_policy(
    content: str,
    group_id: str | int | None,
    *,
    recent_bot_texts: list[str] | tuple[str, ...] | None = None,
) -> str:
    text = (content or "").strip()
    policy = get_group_policy(group_id)
    if not text or not policy:
        return text

    self_reference = str(policy.get("self_reference") or "").strip()
    if self_reference:
        parts = _CODE_SPAN_RE.split(text)
        text = "".join(
            part if index % 2 else _replace_self_reference(part, self_reference)
            for index, part in enumerate(parts)
        )

    civil_cfg = policy.get("civil_language", {})
    if (
        isinstance(civil_cfg, dict)
        and civil_cfg.get("enabled", False)
        and (
            _TARGETED_UNCIVIL_RE.search(text)
            or _SLUR_ONLY_RE.fullmatch(text)
        )
    ):
        fallback = str(civil_cfg.get("fallback") or "").strip()
        return fallback or "这句容易伤人，小源不评价人，还是聊事情本身吧。"

    recruitment = policy.get("recruitment", {})
    if (
        isinstance(recruitment, dict)
        and recruitment.get("enabled", False)
        and recruitment.get("append_on_relevant_topic", True)
    ):
        website = str(recruitment.get("website") or "").strip()
        site_was_recent = bool(
            website
            and any(website in str(item) for item in (recent_bot_texts or ()))
        )
        if (
            website
            and website not in text
            and not site_was_recent
            and _RECRUITMENT_TOPIC_RE.search(text)
        ):
            text = (
                f"{text} 小源也给开源协会打个招新：对编程、Linux、硬件、AI "
                f"或开源项目感兴趣，可以来 {website} 看看。"
            )
    return text
