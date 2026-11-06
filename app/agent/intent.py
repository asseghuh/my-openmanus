import json
import re
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from app.llm import LLM
from app.logger import logger
from app.prompt.intent import (
    CLASSIFY_SYSTEM,
    DIRECT_ANSWER_SYSTEM,
    INTENT_AGENT,
    INTENT_DIRECT,
)
from app.schema import Message

# 高置信：需要工具 / 实时外部数据
_AGENT_HINT = re.compile(
    r"(打开|搜索|访问|下载|写入|保存到|运行代码|12306|浏览器|截图|爬取|"
    r"天气|股价|新闻|车票|订票|机票|行程|路线|导航|周边|景点|旅游|"
    r"browse|search|download|screenshot|crawl|weather|stock\s*price)",
    re.I,
)
_URL_OR_PATH = re.compile(
    r"(https?://|www\.|workspace[/\\]|[a-zA-Z]:\\|/[\w.\-]+/)",
    re.I,
)
# 高置信：整句就是寒暄
_DIRECT_HINT = re.compile(
    r"^\s*(你好|您好|hi|hello|hey|谢谢|感谢|你是谁|who\s+are\s+you)[！!。.?？\s]*$",
    re.I,
)
# 相对日期词：用服务器时钟即可推算，不需要工具
_REL_DAY = r"(今天|明天|昨天|后天|前天|大后天)"
_DATE_ASK = r"(几号|哪天|日期|星期几|周几|礼拜几|什么日子)"
# 高置信：问日期 / 星期 / 当前时刻，或「明天呢」这类省略式追问
_DATE_DIRECT = re.compile(
    rf"("
    rf"{_REL_DAY}是?{_DATE_ASK}|"
    rf"现在是?(几点|什么时间|几号|日期)|"
    rf"^\s*(那|然后|还有)?{_REL_DAY}(是?{_DATE_ASK})?呢?[！!。.?？\s]*$|"
    rf"what(?:'s| is) (?:the )?(?:today'?s |tomorrow'?s )?(?:date|day)|"
    rf"what time is it|"
    rf"what day is (?:it|tomorrow)"
    rf")",
    re.I,
)
# 上一轮已在问日期时，更短的追问（如「那呢」）
_DATE_FOLLOWUP = re.compile(
    rf"^\s*(那|然后|还有)?({_REL_DAY}|星期几|周几|日期)?呢[！!。.?？\s]*$",
    re.I,
)


def last_user_text(history: Optional[Sequence[dict]] = None) -> str:
    """取会话历史里最近一条用户话。"""
    for item in reversed(list(history or [])):
        if str((item or {}).get("role", "")).strip() == "user":
            return str((item or {}).get("content") or "").strip()
    return ""


def screen_intent(
    prompt: str, history: Optional[Sequence[dict]] = None
) -> Optional[Tuple[str, str]]:
    """启发式快筛。高置信才返回 (intent, reason)，否则交给分类模型。"""
    text = (prompt or "").strip()
    if not text:
        return INTENT_AGENT, "empty_prompt"

    if _URL_OR_PATH.search(text):
        return INTENT_AGENT, "url_or_path"
    if _AGENT_HINT.search(text):
        return INTENT_AGENT, "agent_keyword"
    if _DIRECT_HINT.match(text):
        return INTENT_DIRECT, "greeting"
    if _DATE_DIRECT.search(text):
        return INTENT_DIRECT, "current_datetime"
    prev = last_user_text(history)
    if prev and _DATE_DIRECT.search(prev) and _DATE_FOLLOWUP.match(text):
        return INTENT_DIRECT, "date_followup"

    return None


def parse_intent_response(text: str) -> Tuple[str, str]:
    """解析分类模型输出；失败则回落 agent_task。"""
    raw = (text or "").strip()
    try:
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0) if match else raw)
        intent = str(data.get("intent", "")).strip()
        reason = str(data.get("reason", "")).strip() or "llm"
        if intent in {INTENT_DIRECT, INTENT_AGENT}:
            return intent, reason
    except Exception:
        pass
    return INTENT_AGENT, "classify_parse_failed"


def _looks_like_process_dump(text: str) -> bool:
    """agent.run() 逐步拼接、工具观察等，不能当作聊天答复。"""
    value = (text or "").strip()
    if not value:
        return True
    if value.startswith("Step ") or "Observed output of cmd" in value:
        return True
    if value.startswith("Terminated: Reached max steps"):
        return True
    return False


def extract_user_facing_result(agent) -> str:
    """从 agent 记忆中取最后一条有实质内容的 assistant 答复。"""
    messages = getattr(getattr(agent, "memory", None), "messages", None) or []
    last_plain = ""
    last_any = ""
    for msg in messages:
        role = getattr(msg.role, "value", msg.role)
        content = (getattr(msg, "content", None) or "").strip()
        if role != "assistant" or not content or _looks_like_process_dump(content):
            continue
        last_any = content
        if not getattr(msg, "tool_calls", None):
            last_plain = content
    return last_plain or last_any


def history_to_messages(history: Optional[Sequence[dict]] = None) -> List[Message]:
    """把会话历史转成 LLM / Agent 可用的 Message 列表。"""
    messages: List[Message] = []
    for item in history or []:
        role = str((item or {}).get("role", "")).strip()
        content = str((item or {}).get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(Message.user_message(content))
        elif role == "assistant":
            messages.append(Message.assistant_message(content))
    return messages


class IntentGate:
    """入口分流：简单问答直呼 LLM，其余交给 Manus。"""

    def __init__(self, llm: Optional[LLM] = None):
        self.llm = llm or LLM()

    async def classify(
        self, prompt: str, history: Optional[Sequence[dict]] = None
    ) -> Tuple[str, str]:
        screened = screen_intent(prompt, history)
        if screened:
            intent, reason = screened
            logger.info(f"intent={intent} reason={reason}")
            return screened

        try:
            messages = history_to_messages(history)
            messages.append(Message.user_message(prompt))
            raw = await self.llm.ask(
                messages,
                system_msgs=[Message.system_message(CLASSIFY_SYSTEM)],
                stream=False,
                temperature=0.0,
            )
            intent, reason = parse_intent_response(raw)
        except Exception as e:
            intent, reason = INTENT_AGENT, f"classify_error:{e}"

        logger.info(f"intent={intent} reason={reason}")
        return intent, reason

    async def direct_answer(self, prompt: str, history: Optional[Sequence[dict]] = None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        system = DIRECT_ANSWER_SYSTEM.format(now=now)
        messages = history_to_messages(history)
        messages.append(Message.user_message(prompt))
        return await self.llm.ask(
            messages,
            system_msgs=[Message.system_message(system)],
            stream=False,
        )

    async def run_agent(
        self,
        prompt: str,
        history: Optional[Sequence[dict]] = None,
        on_final_answer=None,
    ) -> str:
        from app.agent.manus import Manus

        agent = await Manus.create()
        if on_final_answer is not None:
            agent.on_final_answer = on_final_answer
        try:
            prior = history_to_messages(history)
            if prior:
                agent.memory.add_messages(prior)
            await agent.run(prompt)
            return extract_user_facing_result(agent)
        finally:
            await agent.cleanup()

    async def handle(self, prompt: str) -> str:
        intent, reason = await self.classify(prompt)
        if intent == INTENT_DIRECT:
            try:
                answer = await self.direct_answer(prompt)
                logger.info(answer)
                return answer
            except Exception as e:
                logger.error(f"direct_answer failed, fallback to agent: {e}")
                return await self.run_agent(prompt)
        return await self.run_agent(prompt)
