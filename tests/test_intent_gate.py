from types import SimpleNamespace

from app.agent.intent import (
    extract_user_facing_result,
    history_to_messages,
    parse_intent_response,
    screen_intent,
)
from app.prompt.intent import INTENT_AGENT, INTENT_DIRECT
from app.schema import Function, Message, ToolCall


def test_screen_greeting_is_direct():
    intent, reason = screen_intent("你好")
    assert intent == INTENT_DIRECT
    assert reason == "greeting"


def test_screen_today_date_is_direct():
    intent, reason = screen_intent("今天是几号")
    assert intent == INTENT_DIRECT
    assert reason == "current_datetime"


def test_screen_today_weekday_is_direct():
    intent, _ = screen_intent("今天星期几")
    assert intent == INTENT_DIRECT


def test_screen_tomorrow_date_is_direct():
    intent, reason = screen_intent("明天是几号")
    assert intent == INTENT_DIRECT
    assert reason == "current_datetime"


def test_screen_tomorrow_followup_is_direct():
    intent, reason = screen_intent("明天呢")
    assert intent == INTENT_DIRECT
    assert reason == "current_datetime"


def test_screen_date_followup_with_history():
    history = [
        {"role": "user", "content": "今天是几号"},
        {"role": "assistant", "content": "今天是2026年11月5日。"},
    ]
    intent, reason = screen_intent("那呢", history)
    assert intent == INTENT_DIRECT
    assert reason == "date_followup"


def test_screen_weather_is_agent():
    intent, _ = screen_intent("查明天北京天气")
    assert intent == INTENT_AGENT


def test_screen_today_weather_stays_agent():
    intent, _ = screen_intent("今天北京天气怎么样")
    assert intent == INTENT_AGENT


def test_screen_url_is_agent():
    intent, _ = screen_intent("打开 https://www.ecjtu.edu.cn/ 做 SEO 审查")
    assert intent == INTENT_AGENT


def test_screen_file_write_is_agent():
    intent, _ = screen_intent("帮我把报告写到 workspace/xxx.md")
    assert intent == INTENT_AGENT


def test_screen_tickets_is_agent():
    intent, _ = screen_intent("用浏览器查 12306 车票")
    assert intent == INTENT_AGENT


def test_screen_itinerary_is_agent():
    intent, _ = screen_intent("帮我安排杭州两日游行程")
    assert intent == INTENT_AGENT


def test_screen_react_question_defers_to_llm():
    assert screen_intent("用一句话解释什么是 ReAct") is None


def test_parse_intent_json():
    intent, reason = parse_intent_response(
        '{"intent":"direct_answer","reason":"definition"}'
    )
    assert intent == INTENT_DIRECT
    assert reason == "definition"


def test_parse_intent_invalid_falls_back_to_agent():
    intent, reason = parse_intent_response("not json")
    assert intent == INTENT_AGENT
    assert reason == "classify_parse_failed"


def test_history_to_messages_keeps_order():
    messages = history_to_messages(
        [
            {"role": "user", "content": "今天是几号"},
            {"role": "assistant", "content": "今天是2026年11月5日。"},
            {"role": "user", "content": "明天呢"},
        ]
    )
    assert [msg.role for msg in messages] == ["user", "assistant", "user"]
    assert messages[-1].content == "明天呢"


def test_extract_skips_empty_terminate_and_run_dump():
    terminate = Message.from_tool_calls(
        content="",
        tool_calls=[
            ToolCall(id="1", function=Function(name="terminate", arguments="{}"))
        ],
    )
    agent = SimpleNamespace(
        memory=SimpleNamespace(
            messages=[
                Message.assistant_message("## 今日娱乐资讯汇总\n肖战演唱会彩排"),
                Message.assistant_message("Step 1: Observed output of cmd `tavily-search`"),
                terminate,
            ]
        )
    )
    assert extract_user_facing_result(agent) == "## 今日娱乐资讯汇总\n肖战演唱会彩排"


def test_history_to_messages_skips_empty():
    messages = history_to_messages(
        [
            {"role": "user", "content": "  "},
            {"role": "system", "content": "ignore"},
            {"role": "assistant", "content": "答复"},
        ]
    )
    assert len(messages) == 1
    assert messages[0].role == "assistant"
