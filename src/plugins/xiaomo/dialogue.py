"""Short-lived conversation ownership for natural no-mention follow-ups."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import state


_GROUP_ADDRESS_RE = re.compile(
    r"^(?:大家|各位|兄弟们|姐妹们|群友们|群里|全体|有人|有没有人|谁来|来个人|"
    r"问下大家|问一下大家|求助大家|@全体成员)"
)
_CLOSING_RE = re.compile(
    r"^(?:好|好的|好嘞|行|可以|收到|懂了|明白了|知道了|谢谢|谢了|多谢|"
    r"没事了|不用了|先这样|拜拜|晚安|回头聊)[啊呀啦呢哈哦～~.!！。]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContinuationDecision:
    candidate: bool
    reason: str
    direct_reply: bool = False
    elapsed_seconds: float = 0.0
    intervening_humans: int = 0
    close_after_reply: bool = False
    session: dict | None = None


def is_closing_message(text: str) -> bool:
    clean = (text or "").strip()
    if _CLOSING_RE.fullmatch(clean):
        return True
    compact = re.sub(r"[\s,，。.!！～~啊呀啦呢哈哦]+", "", clean)
    acknowledgement = any(
        cue in compact
        for cue in ("懂了", "明白了", "知道了", "好的", "好嘞", "收到")
    )
    gratitude = any(cue in compact for cue in ("谢谢", "谢了", "多谢"))
    return len(compact) <= 12 and acknowledgement and gratitude


def evaluate_continuation(
    *,
    group_id: str,
    user_qq: str,
    text: str,
    bot_qq: str | None,
    mentioned_qqs: list[str] | None = None,
    reply_to_message_id: str | None = None,
    quoted_user_qq: str | None = None,
    source_message_id: str | None = None,
    now: float,
    config: dict | None = None,
) -> ContinuationDecision:
    """Conservatively decide whether a no-mention message merits an AI check."""

    cfg = config or {}
    if cfg.get("enabled", True) is False:
        return ContinuationDecision(False, "disabled")

    owner = str(user_qq or "")
    bot_id = str(bot_qq or "")
    clean = (text or "").strip()
    session = state.get_dialogue_session(group_id, now=now)
    session_source = str((session or {}).get("last_bot_source_message_id") or "")
    reply_source = str(reply_to_message_id or "")
    direct_reply = bool(
        (bot_id and str(quoted_user_qq or "") == bot_id)
        or (session_source and reply_source == session_source)
    )

    other_mentions = {
        str(qq)
        for qq in (mentioned_qqs or [])
        if str(qq) and str(qq) != bot_id
    }
    if other_mentions:
        return ContinuationDecision(False, "mentions-another-member", session=session)

    if reply_source and not direct_reply:
        return ContinuationDecision(False, "replies-to-another-message", session=session)

    if direct_reply:
        return ContinuationDecision(
            True,
            "direct-reply-to-bot",
            direct_reply=True,
            close_after_reply=is_closing_message(clean),
            session=session,
        )

    if not session:
        return ContinuationDecision(False, "no-active-dialogue")
    if str(session.get("user_qq") or "") != owner:
        return ContinuationDecision(False, "different-dialogue-owner", session=session)
    if _GROUP_ADDRESS_RE.match(clean):
        return ContinuationDecision(False, "addresses-the-group", session=session)

    try:
        last_bot_at = float(session.get("last_bot_at") or 0)
        last_explicit_at = float(session.get("last_explicit_at") or 0)
    except (TypeError, ValueError):
        return ContinuationDecision(False, "invalid-dialogue-state", session=session)

    if session.get("awaiting_bot_reply") or last_bot_at <= 0:
        pending_seconds = max(1.0, float(cfg.get("pending_seconds", 15)))
        elapsed = max(0.0, now - last_explicit_at)
        if elapsed > pending_seconds:
            return ContinuationDecision(
                False,
                "bot-has-not-replied",
                elapsed_seconds=elapsed,
                session=session,
            )
        return ContinuationDecision(
            True,
            "same-owner-after-explicit",
            elapsed_seconds=elapsed,
            close_after_reply=is_closing_message(clean),
            session=session,
        )

    elapsed = max(0.0, now - last_bot_at)
    timeout_seconds = max(1.0, float(cfg.get("timeout_seconds", 240)))
    if elapsed > timeout_seconds:
        state.close_dialogue_session(group_id, user_qq=owner)
        return ContinuationDecision(
            False,
            "dialogue-timed-out",
            elapsed_seconds=elapsed,
            session=session,
        )

    intervening = []
    for item in state.group_recent_texts.get(str(group_id), []):
        try:
            item_time = float(item.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if item_time <= last_bot_at:
            continue
        if source_message_id and str(item.get("source_message_id") or "") == str(source_message_id):
            continue
        item_user = str(item.get("user_qq") or "")
        if not item_user or item_user in {owner, bot_id}:
            continue
        intervening.append(item)

    max_intervening = max(0, int(cfg.get("max_intervening_human_messages", 2)))
    if len(intervening) > max_intervening:
        return ContinuationDecision(
            False,
            "group-conversation-moved-on",
            elapsed_seconds=elapsed,
            intervening_humans=len(intervening),
            session=session,
        )

    return ContinuationDecision(
        True,
        "recent-same-owner-turn",
        elapsed_seconds=elapsed,
        intervening_humans=len(intervening),
        close_after_reply=is_closing_message(clean),
        session=session,
    )


def build_continuation_instruction(decision: ContinuationDecision) -> str:
    """Ask the generation model to make the final addressee judgment once."""

    if not decision.candidate:
        return ""
    session = decision.session or {}
    last_bot_text = str(session.get("last_bot_text") or "").strip()
    if not last_bot_text:
        last_bot_text = "（同一静默窗口内，小源尚未发出上一轮回复）"
    direct_hint = "是" if decision.direct_reply else "否"
    owner_qq = str(session.get("user_qq") or "unknown")
    return (
        "[DIALOGUE_CONTINUATION_CHECK]\n"
        "这条消息没有重新 @ 或点名小源，但发送者最近与小源建立过对话。\n"
        f"候选原因: {decision.reason}\n"
        f"短期会话成员 QQ: {owner_qq}\n"
        f"是否直接回复小源消息: {direct_hint}\n"
        f"距小源上次回复: {decision.elapsed_seconds:.1f} 秒\n"
        f"期间插入的其他成员消息数: {decision.intervening_humans}\n"
        f"小源上一句: {last_bot_text[:300]}\n"
        "先结合近期群聊、引用关系和当前消息判断它自然是在对谁说话。"
        "只有它是在回答、追问或继续和小源聊天时才回复；如果它转向其他群友、"
        "面向全群、另起了明显无关的话题，或指向不够确定，只输出 [SILENT]，不要附加解释。\n"
        "若应回复，直接承接上一轮，不要重新打招呼，不要问‘是在和我说吗’，"
        "也不要要求对方再次 @ 小源。\n"
        "[/DIALOGUE_CONTINUATION_CHECK]"
    )
