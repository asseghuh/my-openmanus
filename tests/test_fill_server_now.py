from datetime import datetime

from app.agent.toolcall import fill_server_now
from app.prompt.manus import NEXT_STEP_PROMPT, SYSTEM_PROMPT


def test_next_step_prompt_has_now_placeholder():
    assert "{now}" in NEXT_STEP_PROMPT


def test_system_prompt_keeps_directory_placeholder():
    assert "{directory}" in SYSTEM_PROMPT
    assert "{now}" not in SYSTEM_PROMPT


def test_fill_server_now_replaces_placeholder():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    filled = fill_server_now(NEXT_STEP_PROMPT)
    assert filled is not None
    assert "{now}" not in filled
    assert now[:10] in filled
    assert "2025年1月" in filled  # 作为反例写在提示里，提醒模型不要用


def test_fill_server_now_leaves_plain_text():
    assert fill_server_now("不需要占位符") == "不需要占位符"
    assert fill_server_now(None) is None
    assert fill_server_now("") == ""
