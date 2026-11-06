import asyncio
import os
import threading
import tomllib
import uuid
import webbrowser
import json
from datetime import datetime
from functools import partial
from json import dumps
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional

from pydantic import BaseModel, Field

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SESSIONS_PATH = Path(__file__).parent / "data" / "web_sessions.json"


class Task(BaseModel):
    id: str
    prompt: str
    created_at: datetime
    status: str
    steps: list = Field(default_factory=list)
    title: str = ""
    turns: list = Field(default_factory=list)
    updated_at: Optional[datetime] = None

    def model_dump(self, *args, **kwargs):
        data = super().model_dump(*args, **kwargs)
        data["created_at"] = self.created_at.isoformat()
        updated = self.updated_at or self.created_at
        data["updated_at"] = updated.isoformat()
        return data


class TaskRename(BaseModel):
    title: str


class TaskCreate(BaseModel):
    prompt: str
    task_id: Optional[str] = None


class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.queues = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """启动时从本地文件恢复会话历史。"""
        if not SESSIONS_PATH.exists():
            return
        try:
            raw = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
            for item in raw:
                created_at = datetime.fromisoformat(item["created_at"])
                updated_raw = item.get("updated_at")
                updated_at = (
                    datetime.fromisoformat(updated_raw) if updated_raw else created_at
                )
                steps = item.get("steps") or []
                turns = item.get("turns") or []
                if not turns:
                    turns = _turns_from_legacy(item.get("prompt", ""), steps)
                task = Task(
                    id=item["id"],
                    prompt=item.get("prompt", ""),
                    title=item.get("title") or "",
                    created_at=created_at,
                    updated_at=updated_at,
                    status=item.get("status", "completed"),
                    steps=steps,
                    turns=turns,
                )
                self.tasks[task.id] = task
        except Exception as e:
            print(f"Failed to load web sessions: {e}")

    def _save(self) -> None:
        """将会话列表持久化，刷新页面后仍可重新进入。"""
        try:
            SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = [task.model_dump() for task in self.tasks.values()]
            SESSIONS_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"Failed to save web sessions: {e}")

    def create_task(self, prompt: str) -> Task:
        task_id = str(uuid.uuid4())
        title = " ".join(prompt.strip().split())[:40]
        now = datetime.now()
        task = Task(
            id=task_id,
            prompt=prompt,
            title=title,
            created_at=now,
            updated_at=now,
            status="pending",
            turns=[],
        )
        self.tasks[task_id] = task
        self.queues[task_id] = asyncio.Queue()
        with self._lock:
            self._save()
        return task

    def start_turn(self, task_id: str, prompt: str) -> Task:
        """在已有会话中开启新一轮，不新建历史项。"""
        task = self.tasks[task_id]
        task.status = "pending"
        task.steps = []
        task.updated_at = datetime.now()
        self.queues[task_id] = asyncio.Queue()
        with self._lock:
            self._save()
        return task

    def conversation_history(self, task_id: str) -> list:
        """已完成轮次的 user/assistant 对话，供下一轮续接。"""
        task = self.tasks.get(task_id)
        if not task:
            return []
        history = []
        for turn in task.turns or []:
            prompt = (turn.get("prompt") or "").strip()
            result = (turn.get("result") or "").strip()
            if prompt:
                history.append({"role": "user", "content": prompt})
            if result:
                history.append({"role": "assistant", "content": result})
        return history

    def _append_turn(self, task: Task, prompt: str, result: str) -> None:
        task.turns.append(
            {
                "prompt": prompt,
                "steps": list(task.steps or []),
                "result": result,
            }
        )
        task.updated_at = datetime.now()

    def rename_task(self, task_id: str, title: str) -> Task:
        task = self.tasks[task_id]
        task.title = title
        with self._lock:
            self._save()
        return task

    def delete_task(self, task_id: str) -> None:
        self.tasks.pop(task_id, None)
        self.queues.pop(task_id, None)
        with self._lock:
            self._save()

    def push_step(
        self, task_id: str, step: int, result: str, step_type: str = "step"
    ) -> None:
        """同步写入步骤并入队。日志回调必须走这里，避免 async sink 和 logger.remove 死锁。"""
        task = self.tasks.get(task_id)
        if not task:
            return
        task.steps.append({"step": step, "result": result, "type": step_type})
        queue = self.queues.get(task_id)
        if not queue:
            return
        try:
            queue.put_nowait({"type": step_type, "step": step, "result": result})
            # 不要把全部 steps 塞进每次 status：体积过大时 EventSource 会断，result/complete 就丢了
            queue.put_nowait({"type": "status", "status": task.status})
        except Exception:
            pass

    async def update_task_step(
        self, task_id: str, step: int, result: str, step_type: str = "step"
    ):
        self.push_step(task_id, step, result, step_type)

    async def complete_task(self, task_id: str, result: str, prompt: str = ""):
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = "completed"
            if prompt:
                self._append_turn(task, prompt, result)
            with self._lock:
                self._save()
            queue = self.queues.get(task_id)
            if queue:
                await queue.put({"type": "status", "status": task.status})
                await queue.put({"type": "complete", "result": result})

    async def fail_task(self, task_id: str, error: str, prompt: str = ""):
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.status = f"failed: {error}"
            if prompt:
                self._append_turn(task, prompt, f"错误：{error}")
            with self._lock:
                self._save()
            queue = self.queues.get(task_id)
            if queue:
                await queue.put({"type": "error", "message": error})


def _turns_from_legacy(prompt: str, steps: list) -> list:
    """把旧版单轮会话补成 turns，便于历史回放与续聊。"""
    result = ""
    for step in reversed(steps or []):
        if step.get("type") == "result":
            result = step.get("result") or ""
            break
    if not prompt and not steps:
        return []
    return [{"prompt": prompt or "", "steps": list(steps or []), "result": result}]


task_manager = TaskManager()


def extract_user_facing_result(agent) -> str:
    """从 agent 记忆中提取面向用户的最终答复。

    agent.run() 的返回值是逐步工具观察的拼接，不宜直接展示在聊天窗。
    优先取最后一条不含 tool_calls 的 assistant 内容（总结/报告）；
    若全程都在调工具，则退回最后一条有文本的 assistant 内容。
    """
    messages = getattr(getattr(agent, "memory", None), "messages", None) or []
    last_plain = ""
    last_any = ""
    for msg in messages:
        role = getattr(msg.role, "value", msg.role)
        content = (getattr(msg, "content", None) or "").strip()
        if role != "assistant" or not content:
            continue
        if content.startswith("Step ") or "Observed output of cmd" in content:
            continue
        last_any = content
        if not getattr(msg, "tool_calls", None):
            last_plain = content
    return last_plain or last_any

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/download")
async def download_file(file_path: str):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, filename=os.path.basename(file_path))


@app.post("/tasks")
async def create_task(body: TaskCreate):
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if body.task_id:
        if body.task_id not in task_manager.tasks:
            raise HTTPException(status_code=404, detail="Task not found")
        task = task_manager.start_turn(body.task_id, prompt)
    else:
        task = task_manager.create_task(prompt)
    asyncio.create_task(run_task(task.id, prompt))
    return {"task_id": task.id}


from app.agent.intent import INTENT_DIRECT, IntentGate
from app.logger import logger


async def run_task(task_id: str, prompt: str):
    try:
        task_manager.tasks[task_id].status = "running"
        history = task_manager.conversation_history(task_id)
        gate = IntentGate()
        intent, reason = await gate.classify(prompt, history)

        # 直答：不创建 Manus、不挂 SSE 工具日志，只推最终结果
        if intent == INTENT_DIRECT:
            try:
                user_facing = await gate.direct_answer(prompt, history)
            except Exception as e:
                logger.error(f"direct_answer failed, fallback to agent: {e}")
                intent = "agent_task"
            else:
                await task_manager.update_task_step(task_id, 1, user_facing, "result")
                await asyncio.sleep(3)
                await task_manager.complete_task(task_id, user_facing, prompt)
                return

        class SSELogHandler:
            def __init__(self, task_id):
                self.task_id = task_id
                self.loop = asyncio.get_running_loop()
                self.closed = False

            def __call__(self, message):
                # 必须是同步回调：async sink 会在 logger.remove 时卡住事件循环，complete 发不出去
                if self.closed:
                    return
                import re

                cleaned_message = re.sub(r"^.*? - ", "", str(message))

                event_type = "log"
                if "thoughts:" in cleaned_message.lower():
                    event_type = "think"
                elif "Manus selected" in cleaned_message or "tools to use" in cleaned_message:
                    event_type = "tool"
                elif "completed its mission" in cleaned_message:
                    event_type = "act"
                elif "📝 Oops!" in cleaned_message:
                    event_type = "error"
                elif "🏁 Special tool" in cleaned_message:
                    event_type = "act"

                payload = cleaned_message
                if event_type in {"act", "log"} and len(payload) > 2000:
                    payload = payload[:2000] + "\n…(已截断)"

                self.loop.call_soon_threadsafe(
                    task_manager.push_step, self.task_id, 0, payload, event_type
                )

        sse_handler = SSELogHandler(task_id)
        hwnd = logger.add(sse_handler)

        def emit_final_answer(text: str) -> None:
            answer = (text or "").strip()
            if not answer:
                return
            logger.info(f"emit_final_answer_len={len(answer)}")
            task_manager.push_step(task_id, 1, answer, "result")

        try:
            result = await gate.run_agent(
                prompt, history, on_final_answer=emit_final_answer
            )
            user_facing = (result or "").strip()
            if not user_facing:
                import re as _re

                for step in reversed(task_manager.tasks[task_id].steps):
                    if step.get("type") != "think":
                        continue
                    text = _re.sub(
                        r"^.*?thoughts:\s*",
                        "",
                        step.get("result") or "",
                        flags=_re.I,
                    ).strip()
                    if text:
                        user_facing = text
                        break
            logger.info(f"user_facing_len={len(user_facing)}")
            await task_manager.update_task_step(task_id, 1, user_facing, "result")
            await task_manager.complete_task(task_id, user_facing, prompt)
        finally:
            sse_handler.closed = True
            try:
                logger.remove(hwnd)
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"run_task failed: {e}")
        await task_manager.fail_task(task_id, str(e), prompt)


@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def event_generator():
        if task_id not in task_manager.queues:
            yield f"event: error\ndata: {dumps({'message': 'Task not found'})}\n\n"
            return

        queue = task_manager.queues[task_id]

        task = task_manager.tasks.get(task_id)
        if task:
            yield f"event: status\ndata: {dumps({'type': 'status', 'status': task.status, 'steps': task.steps})}\n\n"

        while True:
            try:
                event = await queue.get()
                formatted_event = dumps(event)

                yield ": heartbeat\n\n"

                if event["type"] == "complete":
                    yield f"event: complete\ndata: {formatted_event}\n\n"
                    break
                elif event["type"] == "error":
                    yield f"event: error\ndata: {formatted_event}\n\n"
                    break
                elif event["type"] == "step":
                    task = task_manager.tasks.get(task_id)
                    if task:
                        yield f"event: status\ndata: {dumps({'type': 'status', 'status': task.status, 'steps': task.steps})}\n\n"
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"
                elif event["type"] in ["think", "tool", "act", "run"]:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"
                else:
                    yield f"event: {event['type']}\ndata: {formatted_event}\n\n"

            except asyncio.CancelledError:
                print(f"Client disconnected for task {task_id}")
                break
            except Exception as e:
                print(f"Error in event stream: {str(e)}")
                yield f"event: error\ndata: {dumps({'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/tasks")
async def get_tasks():
    sorted_tasks = sorted(
        task_manager.tasks.values(),
        key=lambda task: task.updated_at or task.created_at,
        reverse=True,
    )
    return JSONResponse(
        content=[task.model_dump() for task in sorted_tasks],
        headers={"Content-Type": "application/json"},
    )


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return task_manager.tasks[task_id]


@app.patch("/tasks/{task_id}")
async def rename_task(task_id: str, body: TaskRename):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    title = " ".join((body.title or "").split())
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    task = task_manager.rename_task(task_id, title[:80])
    return task.model_dump()


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if task_id not in task_manager.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    task_manager.delete_task(task_id)
    return {"status": "ok"}


@app.get("/config/status")
async def check_config_status():
    config_path = Path(__file__).parent / "config" / "config.toml"
    example_config_path = Path(__file__).parent / "config" / "config.example.toml"

    if config_path.exists():
        return {"status": "exists"}
    elif example_config_path.exists():
        try:
            with open(example_config_path, "rb") as f:
                example_config = tomllib.load(f)
            return {"status": "missing", "example_config": example_config}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {"status": "no_example"}


@app.post("/config/save")
async def save_config(config_data: dict = Body(...)):
    try:
        config_dir = Path(__file__).parent / "config"
        config_dir.mkdir(exist_ok=True)

        config_path = config_dir / "config.toml"

        toml_content = ""

        if "llm" in config_data:
            toml_content += "# Global LLM configuration\n[llm]\n"
            llm_config = config_data["llm"]
            for key, value in llm_config.items():
                if key != "vision":
                    if isinstance(value, str):
                        toml_content += f'{key} = "{value}"\n'
                    else:
                        toml_content += f"{key} = {value}\n"

        if "server" in config_data:
            toml_content += "\n# Server configuration\n[server]\n"
            server_config = config_data["server"]
            for key, value in server_config.items():
                if isinstance(value, str):
                    toml_content += f'{key} = "{value}"\n'
                else:
                    toml_content += f"{key} = {value}\n"

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(toml_content)

        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500, content={"message": f"Server error: {str(exc)}"}
    )


def open_local_browser(config):
    webbrowser.open_new_tab(f"http://{config['host']}:{config['port']}")


def load_config():
    try:
        config_path = Path(__file__).parent / "config" / "config.toml"

        if not config_path.exists():
            return {"host": "localhost", "port": 5172}

        with open(config_path, "rb") as f:
            config = tomllib.load(f)

        return {"host": config["server"]["host"], "port": config["server"]["port"]}
    except FileNotFoundError:
        return {"host": "localhost", "port": 5172}
    except KeyError as e:
        print(
            f"The configuration file is missing necessary fields: {str(e)}, use default configuration"
        )
        return {"host": "localhost", "port": 5172}


if __name__ == "__main__":
    import uvicorn

    config = load_config()
    open_with_config = partial(open_local_browser, config)
    threading.Timer(3, open_with_config).start()
    uvicorn.run(app, host=config["host"], port=config["port"])
