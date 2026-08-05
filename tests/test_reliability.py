from __future__ import annotations

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
import nonebot

nonebot.init()
from src.plugins.xiaomo import (
    auto_action,
    database,
    memory,
    vector_store,
    weather,
    web_search,
)


def _msg(content: str, created_at: float, role: str = "user", user_qq: str = "u1"):
    return SimpleNamespace(
        content=content,
        created_at=created_at,
        role=role,
        user_qq=user_qq,
        current_weight=lambda half_life: 1.0,
    )


def test_smart_truncate_respects_max_tokens(monkeypatch):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"memory": {"keep_recent_messages": 50}},
    )
    messages = [_msg("这是很长的一条消息" * 10, created_at=i) for i in range(20)]

    selected = memory._smart_truncate(messages, max_tokens=120, half_life=60)

    assert sum(memory.estimate_tokens(m.content) for m in selected) <= 120
    assert selected[-1].created_at == 19


def test_latest_user_content_uses_newest_matching_message():
    messages = [
        _msg("old question", created_at=1, user_qq="u1"),
        _msg("other user", created_at=2, user_qq="u2"),
        _msg("new question", created_at=3, user_qq="u1"),
    ]

    assert memory._latest_user_content(messages, "u1") == "new question"


def test_embedding_model_uses_local_cache_before_network_download():
    calls = []

    def model_factory(model_name, **kwargs):
        calls.append((model_name, kwargs))
        return object()

    result = vector_store._load_embedding_model("cached-model", model_factory)

    assert result is not None
    assert calls == [("cached-model", {"local_files_only": True})]


def test_embedding_model_downloads_only_after_local_cache_miss():
    calls = []

    def model_factory(model_name, **kwargs):
        calls.append((model_name, kwargs))
        if kwargs.get("local_files_only"):
            raise OSError("not cached")
        return object()

    result = vector_store._load_embedding_model("remote-model", model_factory)

    assert result is not None
    assert calls == [
        ("remote-model", {"local_files_only": True}),
        ("remote-model", {}),
    ]


@pytest.mark.asyncio
async def test_weather_missing_future_day_returns_friendly_message(monkeypatch):
    async def fake_fetch_raw():
        return {
            "current_condition": [],
            "weather": [
                {"maxtempC": "20", "mintempC": "10", "hourly": []},
                {"maxtempC": "21", "mintempC": "11", "hourly": []},
            ],
        }

    async def fake_fetch_open_meteo_raw():
        return None

    monkeypatch.setattr(weather, "_fetch_raw", fake_fetch_raw)
    monkeypatch.setattr(weather, "_fetch_open_meteo_raw", fake_fetch_open_meteo_raw)

    result = await weather.query_weather("后天")

    assert "暂时查不到" in result


@pytest.mark.asyncio
async def test_weather_falls_back_to_open_meteo_when_wttr_fails(monkeypatch):
    async def fake_fetch_raw():
        return None

    async def fake_fetch_open_meteo_raw():
        return {
            "current": {
                "temperature_2m": 26.1,
                "relative_humidity_2m": 68,
                "apparent_temperature": 27.5,
                "weather_code": 1,
                "wind_speed_10m": 9.2,
                "wind_direction_10m": 135,
                "uv_index": 5.1,
            },
            "daily": {
                "time": ["2026-06-16"],
                "temperature_2m_max": [31.2],
                "temperature_2m_min": [22.4],
                "sunrise": ["2026-06-16T06:01"],
                "sunset": ["2026-06-16T20:08"],
                "weather_code": [1],
            },
        }

    monkeypatch.setattr(weather, "_fetch_raw", fake_fetch_raw)
    monkeypatch.setattr(
        weather, "_fetch_open_meteo_raw", fake_fetch_open_meteo_raw, raising=False,
    )

    result = await weather.query_weather("今天")

    assert "天气数据获取失败" not in result
    assert "成都天气" in result
    assert "26" in result
    assert "22" in result and "31" in result


def test_weather_city_extraction_uses_default_only_when_city_is_absent(monkeypatch):
    monkeypatch.setattr(weather, "_default_city", lambda: "成都")

    assert weather.extract_city("上海明天天气怎么样") == "上海"
    assert weather.extract_city("北京会不会下雨") == "北京"
    assert weather.extract_city("明天天气怎么样") == "成都"


@pytest.mark.asyncio
async def test_weather_queries_the_requested_city_coordinates(monkeypatch):
    calls = []
    date_key = datetime.now(weather.CST).date().isoformat()

    async def resolve(city):
        assert city == "上海"
        return {
            "name": "上海",
            "latitude": 31.23,
            "longitude": 121.47,
            "timezone": "Asia/Shanghai",
        }

    async def fetch(latitude, longitude, timezone_name):
        calls.append((latitude, longitude, timezone_name))
        return {
            "current": {
                "temperature_2m": 30,
                "relative_humidity_2m": 50,
                "apparent_temperature": 31,
                "weather_code": 0,
                "wind_speed_10m": 5,
                "wind_direction_10m": 90,
                "uv_index": 3,
            },
            "daily": {
                "time": [date_key],
                "temperature_2m_max": [32],
                "temperature_2m_min": [25],
                "sunrise": [f"{date_key}T05:20"],
                "sunset": [f"{date_key}T18:45"],
                "weather_code": [0],
            },
        }

    monkeypatch.setattr(weather, "_resolve_location", resolve)
    monkeypatch.setattr(weather, "_fetch_open_meteo_raw", fetch)

    result = await weather.query_weather("上海今天天气")

    assert calls == [(31.23, 121.47, "Asia/Shanghai")]
    assert "上海天气" in result


@pytest.mark.asyncio
async def test_natural_web_search_can_be_disabled(monkeypatch):
    monkeypatch.setattr(
        web_search,
        "get_config",
        lambda: {"web_search": {"natural_query": False}},
        raising=False,
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("search_web should not be called")

    monkeypatch.setattr(web_search, "search_web", fail_if_called)

    assert await web_search.run_smart_search("今天成都有什么新闻") is None


def test_quiet_hours_cross_midnight():
    cst = timezone(timedelta(hours=8))

    assert auto_action.is_quiet_hours(datetime(2026, 6, 15, 23, 0, tzinfo=cst))
    assert auto_action.is_quiet_hours(datetime(2026, 6, 16, 6, 59, tzinfo=cst))
    assert not auto_action.is_quiet_hours(datetime(2026, 6, 16, 12, 0, tzinfo=cst))


def test_contextual_bubble_rejects_generic_presence_checks():
    assert auto_action._clean_contextual_bubble("有人吗？") is None
    assert auto_action._clean_contextual_bubble("刚才那个后来咋样？") is None
    assert (
        auto_action._clean_contextual_bubble("机械制图那课最后真不教 CAD 吗？")
        == "机械制图那课最后真不教 CAD 吗？"
    )


@pytest.mark.asyncio
async def test_contextual_bubble_generation_uses_one_grounded_message(monkeypatch):
    calls = []

    class FakeLLM:
        async def chat(self, **kwargs):
            calls.append(kwargs)
            return "消息：机械制图那课最后真不教 CAD 吗？"

    monkeypatch.setattr(auto_action, "get_llm", lambda: FakeLLM())

    result = await auto_action._generate_contextual_bubble(
        "g1",
        "A: 我们专业机械制图居然不教 CAD",
    )

    assert result == "机械制图那课最后真不教 CAD 吗？"
    assert calls[0]["temperature"] == 0.72
    assert "具体对象" in calls[0]["user_message"]


@pytest.mark.asyncio
async def test_proactive_decision_blocks_quiet_hours_without_ai():
    cst = timezone(timedelta(hours=8))
    called = False

    async def decider(_payload):
        nonlocal called
        called = True
        return True

    allowed = await auto_action.should_send_proactive_message(
        group_id="g1",
        reason="bubble",
        candidate_text="有人吗",
        now=datetime(2026, 6, 15, 23, 30, tzinfo=cst),
        ai_decider=decider,
    )

    assert not allowed
    assert not called


@pytest.mark.asyncio
async def test_proactive_decision_uses_ai_gate_during_day():
    cst = timezone(timedelta(hours=8))
    payloads = []

    async def decider(payload):
        payloads.append(payload)
        return payload["reason"] == "reaction"

    allowed = await auto_action.should_send_proactive_message(
        group_id="g1",
        reason="reaction",
        candidate_text="不客气",
        trigger_text="谢谢",
        now=datetime(2026, 6, 15, 12, 0, tzinfo=cst),
        ai_decider=decider,
    )

    assert allowed
    assert payloads[0]["candidate_text"] == "不客气"
    assert payloads[0]["trigger_text"] == "谢谢"


@pytest.mark.asyncio
async def test_default_proactive_gate_keeps_caller_context(monkeypatch):
    payloads = []

    async def default_decider(payload):
        payloads.append(payload)
        return True

    async def fail_context_load(_group_id):
        raise AssertionError("provided context must not be overwritten")

    monkeypatch.setattr(auto_action, "_default_proactive_ai_decider", default_decider)
    monkeypatch.setattr(auto_action, "_recent_group_context", fail_context_load)

    allowed = await auto_action.should_send_proactive_message(
        group_id="g-context",
        reason="reaction",
        candidate_text="确实",
        trigger_text="这个有点离谱",
        recent_context="甲：刚才在聊部署",
        now=datetime(2026, 6, 15, 12, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert allowed is True
    assert payloads[0]["recent_context"] == "甲：刚才在聊部署"


@pytest.mark.asyncio
async def test_proactive_decision_blocks_when_bot_just_spoke(monkeypatch):
    from src.plugins.xiaomo import state

    now = 1000.0
    group_id = "g-recent"
    monkeypatch.setattr(auto_action.time, "time", lambda: now)
    state.bot_reply_times[group_id] = [now - 10]
    state.group_message_times[group_id] = [now - i for i in range(20)]

    called = False

    async def decider(_payload):
        nonlocal called
        called = True
        return True

    allowed = await auto_action.should_send_proactive_message(
        group_id=group_id,
        reason="reaction",
        candidate_text="不客气",
        trigger_text="谢谢",
        ai_decider=decider,
    )

    assert not allowed
    assert not called


@pytest.mark.asyncio
async def test_default_proactive_ai_decider_parses_json(monkeypatch):
    class FakeLLM:
        async def chat(self, **kwargs):
            assert "候选发言" in kwargs["user_message"]
            return '{"send": true, "reason": "上下文合适"}'

    monkeypatch.setattr(auto_action, "get_llm", lambda: FakeLLM(), raising=False)

    allowed = await auto_action._default_proactive_ai_decider(
        {
            "group_id": "g1",
            "reason": "bubble",
            "candidate_text": "有人在吗",
            "trigger_text": "",
            "recent_context": "刚才大家在聊调试",
        }
    )

    assert allowed


@pytest.mark.asyncio
async def test_topic_poke_prefers_llbot_api_without_counting_as_text_reply(monkeypatch):
    from src.plugins.xiaomo import state

    class FakeBot:
        def __init__(self):
            self.calls = []

        async def call_api(self, action, **params):
            self.calls.append((action, params))

        async def send_group_msg(self, **kwargs):
            raise AssertionError("CQ fallback should not run when send_poke succeeds")

    bot = FakeBot()
    state.poke_group_last_time.clear()
    state.poke_user_last_time.clear()
    state.bot_reply_times.clear()
    monkeypatch.setattr(auto_action, "get_bot", lambda: bot)

    sent = await auto_action.try_poke_topic(
        "123",
        "10001",
        "AI",
        probability=1.0,
        user_cooldown_hours=0,
        group_cooldown_seconds=0,
    )

    assert sent is True
    assert bot.calls == [
        ("send_poke", {"user_id": 10001, "group_id": 123})
    ]
    assert state.bot_reply_times.get("123") is None


@pytest.mark.asyncio
async def test_database_init_and_close_are_idempotent(monkeypatch, tmp_path):
    await database.close_database()
    db_path = tmp_path / "xiaomo-test.db"
    monkeypatch.setattr(
        database,
        "get_config",
        lambda: {"database_path": str(db_path)},
    )

    await database.init_database()
    first_engine = database._engine
    await database.init_database()

    assert database._engine is first_engine

    await database.close_database()
    with pytest.raises(RuntimeError):
        await database.get_session()
