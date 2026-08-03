"""小源 QQ 机器人 - 互动调速器。

用于在“想互动”和“该不该出现”之间加一层本地判断。
"""
from __future__ import annotations

from dataclasses import dataclass


HELP_KEYWORDS = (
    "怎么", "如何", "为什么", "咋", "报错", "bug", "异常", "救命",
    "帮我", "看看", "不会", "卡住", "配置", "编译", "运行",
)


@dataclass
class InteractionSignals:
    reason: str
    candidate_text: str
    trigger_text: str = ""
    explicit_trigger: bool = False
    quiet_hours: bool = False
    messages_last_5m: int = 0
    bot_messages_last_5m: int = 0
    seconds_since_bot_reply: float = 9999.0
    user_total_messages: int = 0
    topic_match: bool = False


@dataclass
class InteractionDecision:
    score: int
    action: str
    reason: str

    def to_payload(self) -> dict:
        return {
            "score": self.score,
            "action": self.action,
            "reason": self.reason,
        }


QUESTION_CUES = (
    "\u5417", "\u5462", "\u600e\u4e48", "\u600e\u529e", "\u4e3a\u4ec0\u4e48",
    "\u5982\u4f55", "\u6709\u6ca1\u6709", "\u662f\u4e0d\u662f", "?", "\uff1f",
)

EMOTION_CUES = (
    "\u96be\u53d7", "\u5d29\u4e86", "\u5bc4\u4e86", "\u6551\u547d", "\u70e6",
    "\u7b11\u6b7b", "\u54c8\u54c8", "\u8349", "\u79bb\u8c31", "\u7ef7\u4e0d\u4f4f",
)

OPINION_CUES = (
    "\u6211\u89c9\u5f97", "\u611f\u89c9", "\u6709\u70b9", "\u5176\u5b9e",
    "\u8bf4\u5b9e\u8bdd", "\u600e\u4e48\u770b", "\u9510\u8bc4", "\u8bc4\u4ef7",
)

SOCIAL_CUES = (
    "\u6709\u4eba", "\u8c01\u6765", "\u6765\u4e0d\u6765", "\u804a\u804a",
    "\u6c34\u7fa4", "\u5192\u4e2a\u6ce1", "\u5728\u5e72\u561b", "\u5e72\u5565",
    "\u60f3\u95ee", "\u6c42\u63a8\u8350", "\u6709\u65e0", "\u6709\u6ca1",
)


@dataclass
class JoinOpportunitySignals:
    trigger_text: str = ""
    topic_match: bool = False
    quiet_hours: bool = False
    messages_last_5m: int = 0
    bot_messages_last_5m: int = 0
    seconds_since_bot_reply: float = 9999.0
    human_messages_since_bot: int = 999
    user_total_messages: int = 0


@dataclass
class JoinOpportunityDecision:
    score: int
    action: str
    reason: str
    max_chars: int

    def to_payload(self) -> dict:
        return {
            "score": self.score,
            "action": self.action,
            "reason": self.reason,
            "max_chars": self.max_chars,
        }


def _has_help_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(kw.lower() in lowered for kw in HELP_KEYWORDS)


def _has_question_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(cue.lower() in lowered for cue in QUESTION_CUES)


def _has_emotional_opening(text: str) -> bool:
    lowered = (text or "").lower()
    return any(cue.lower() in lowered for cue in EMOTION_CUES)


def _has_opinion_opening(text: str) -> bool:
    lowered = (text or "").lower()
    return any(cue.lower() in lowered for cue in OPINION_CUES)


def _has_social_opening(text: str) -> bool:
    lowered = (text or "").lower()
    return any(cue.lower() in lowered for cue in SOCIAL_CUES)


def _action_for_score(score: int) -> str:
    if score < 40:
        return "silent"
    if score < 65:
        return "light_touch"
    if score < 85:
        return "brief_reply"
    return "normal_reply"


def _join_action_for_score(score: int) -> tuple[str, int]:
    if score < 32:
        return "silent", 0
    if score < 46:
        return "react", 80
    if score < 64:
        return "short_reply", 160
    return "helpful_reply", 360


def decide_interaction(signals: InteractionSignals) -> InteractionDecision:
    reasons: list[str] = []

    if signals.quiet_hours and not signals.explicit_trigger:
        return InteractionDecision(0, "silent", "quiet hours")

    score = 82 if signals.explicit_trigger else 45
    if signals.explicit_trigger:
        reasons.append("explicit trigger")

    reason_weights = {
        "bubble": -12,
        "repeat": -8,
        "reaction": 0,
        "topic_engage": 8,
        "mention": 10,
        "join_react": 2,
        "join_short": 6,
        "join_helpful": 12,
    }
    score += reason_weights.get(signals.reason, 0)

    if _has_help_intent(signals.trigger_text):
        score += 25
        reasons.append("help intent")
    if signals.topic_match:
        score += 10
        reasons.append("topic match")
    if signals.user_total_messages >= 50:
        score += 5
        reasons.append("familiar user")
    elif signals.user_total_messages <= 2 and not signals.explicit_trigger:
        score -= 5

    if not signals.explicit_trigger:
        if signals.messages_last_5m >= 15:
            score -= 22
            reasons.append("busy group")
        elif signals.messages_last_5m >= 8:
            score -= 12
            reasons.append("active group")

        if signals.bot_messages_last_5m >= 2:
            score -= 30
            reasons.append("bot spoke recently")
        elif signals.bot_messages_last_5m == 1:
            score -= 18

        if signals.seconds_since_bot_reply < 60:
            score -= 32
            reasons.append("recent bot reply")
        elif signals.seconds_since_bot_reply < 180:
            score -= 18

    if len(signals.candidate_text or "") > 180 and not signals.explicit_trigger:
        score -= 8
        reasons.append("long candidate")

    score = max(0, min(100, int(score)))
    action = _action_for_score(score)
    reason = ", ".join(reasons) if reasons else "default"
    return InteractionDecision(score=score, action=action, reason=reason)


def decide_join_opportunity(signals: JoinOpportunitySignals) -> JoinOpportunityDecision:
    """Score whether the bot should naturally join a non-explicit group chat."""
    text = (signals.trigger_text or "").strip()
    if not text:
        return JoinOpportunityDecision(0, "silent", "empty", 0)
    if signals.quiet_hours:
        return JoinOpportunityDecision(0, "silent", "quiet hours", 0)
    if signals.bot_messages_last_5m >= 2 and signals.seconds_since_bot_reply < 90:
        return JoinOpportunityDecision(0, "silent", "bot spoke too much", 0)
    if (
        signals.messages_last_5m >= 18
        and signals.seconds_since_bot_reply < 120
        and signals.human_messages_since_bot < 3
    ):
        return JoinOpportunityDecision(0, "silent", "busy group and recent bot reply", 0)

    reasons: list[str] = []
    score = 34

    if _has_help_intent(text):
        score += 34
        reasons.append("help intent")
    if _has_question_intent(text):
        score += 20
        reasons.append("question")
    if signals.topic_match:
        score += 16
        reasons.append("topic match")
    if _has_emotional_opening(text):
        score += 12
        reasons.append("emotion")
    if _has_opinion_opening(text):
        score += 10
        reasons.append("opinion opening")
    if _has_social_opening(text):
        score += 12
        reasons.append("social opening")

    text_len = len(text)
    if text_len < 3:
        score -= 14
        reasons.append("too short")
    elif 8 <= text_len <= 90:
        score += 4
    elif text_len > 220:
        score -= 8
        reasons.append("long trigger")

    if signals.user_total_messages >= 50:
        score += 4
        reasons.append("familiar user")

    if signals.messages_last_5m >= 18:
        score -= 26
        reasons.append("very busy group")
    elif signals.messages_last_5m >= 10:
        score -= 14
        reasons.append("busy group")
    elif signals.messages_last_5m <= 3:
        score += 10
        reasons.append("room to speak")

    if signals.bot_messages_last_5m >= 2:
        score -= 34
        reasons.append("bot spoke too much")
    elif signals.bot_messages_last_5m == 1:
        score -= 18
        reasons.append("bot already spoke")

    if signals.seconds_since_bot_reply < 120:
        score -= 36
        reasons.append("recent bot reply")
    elif signals.seconds_since_bot_reply < 300:
        score -= 14
    elif signals.seconds_since_bot_reply < 600:
        score -= 4

    if signals.human_messages_since_bot < 3 and signals.seconds_since_bot_reply < 900:
        score -= 10
        reasons.append("not enough human turns")

    score = max(0, min(100, int(score)))
    action, max_chars = _join_action_for_score(score)
    return JoinOpportunityDecision(
        score=score,
        action=action,
        reason=", ".join(reasons) if reasons else "default",
        max_chars=max_chars,
    )
