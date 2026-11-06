import json
from typing import TYPE_CHECKING, Optional

from app.agent.mcp import MCPAgent
from app.logger import logger
from app.prompt.browser import NEXT_STEP_PROMPT
from app.schema import Message
from app.tool.terminate import Terminate


# Avoid circular import if BrowserAgent needs BrowserContextHelper
if TYPE_CHECKING:
    from app.agent.base import BaseAgent  # Or wherever memory is defined


_SANDBOX_BROWSER_TOOL_NAME = "sandbox_browser"


class BrowserContextHelper:
    def __init__(self, agent: "BaseAgent"):
        self.agent = agent
        self._current_base64_image: Optional[str] = None

    async def get_browser_state(self) -> Optional[dict]:
        browser_tool = self.agent.available_tools.get_tool(_SANDBOX_BROWSER_TOOL_NAME)
        if not browser_tool or not hasattr(browser_tool, "get_current_state"):
            logger.warning("SandboxBrowserTool not found or has no current state")
            return None
        try:
            result = await browser_tool.get_current_state()
            if result.error:
                logger.debug(f"Browser state error: {result.error}")
                return None
            if hasattr(result, "base64_image") and result.base64_image:
                self._current_base64_image = result.base64_image
            else:
                self._current_base64_image = None
            return json.loads(result.output)
        except Exception as e:
            logger.debug(f"Failed to get browser state: {str(e)}")
            return None

    async def format_next_step_prompt(self) -> str:
        """Gets browser state and formats the browser prompt."""
        browser_state = await self.get_browser_state()
        url_info, tabs_info, content_above_info, content_below_info = "", "", "", ""
        results_info = ""  # Or get from agent if needed elsewhere

        if browser_state and not browser_state.get("error"):
            url_info = f"\n   URL: {browser_state.get('url', 'N/A')}\n   标题: {browser_state.get('title', 'N/A')}"
            tabs = browser_state.get("tabs", [])
            if tabs:
                tabs_info = f"\n   可用标签页 {len(tabs)} 个"
            pixels_above = browser_state.get("pixels_above", 0)
            pixels_below = browser_state.get("pixels_below", 0)
            if pixels_above > 0:
                content_above_info = f"（{pixels_above} 像素）"
            if pixels_below > 0:
                content_below_info = f"（{pixels_below} 像素）"

            if self._current_base64_image:
                image_message = Message.user_message(
                    content="当前浏览器截图：",
                    base64_image=self._current_base64_image,
                )
                self.agent.memory.add_message(image_message)
                self._current_base64_image = None  # Consume the image after adding

        return NEXT_STEP_PROMPT.format(
            url_placeholder=url_info,
            tabs_placeholder=tabs_info,
            content_above_placeholder=content_above_info,
            content_below_placeholder=content_below_info,
            results_placeholder=results_info,
        )

    async def cleanup_browser(self):
        browser_tool = self.agent.available_tools.get_tool(_SANDBOX_BROWSER_TOOL_NAME)
        if browser_tool and hasattr(browser_tool, "cleanup"):
            await browser_tool.cleanup()


class BrowserAgent(MCPAgent):
    """Browser-only agent backed by the Browser Use CLI 3.0 MCP server."""

    name: str = "browser"
    description: str = "浏览器智能体，可通过控制浏览器完成任务"
    system_prompt: str = """\
通过 `browser_exec` 使用 Browser Use CLI 3.0。将 CLI 示例中的 Python 代码体作为
`code` 参数传入。需要视觉上下文时使用 `browser_screenshot`。browser-harness
会话在多次调用间保持。任务完成后使用 `terminate`。

`browser_exec` 的 `code` 参数规则：
1. `code` 会通过 exec() 作为 Python 执行，因此必须是合法 Python。
   不要嵌套同一风格的引号：Python 字符串优先用双引号，JavaScript 内部用单引号；
   若 JavaScript 需要双引号，则使用 Python 三引号。
     正确: js("document.querySelector('input').value = 'x'")
     正确: js('''document.querySelector("input[placeholder='xx']")''')
     错误（SyntaxError）: js('document.querySelector("input[placeholder='xx']")')
2. 先观察再操作。每次导航（new_tab/goto_url）之后，先调用
   `print(page_info())` 或使用 `browser_screenshot` 查看真实页面。
   不要猜测 CSS 选择器或点击坐标——必须对照观察到的页面再确认。
3. 查询优先直达 URL。若搜索/筛选条件（日期、出发地、目的地、关键词）能通过
   URL 参数携带，则直接打开结果页并在该页抽取数据。不要逐字段填写搜索表单，
   除非站点没有可参数化的 URL，或流程确实需要交互。
4. 填写输入框请使用 fill_input(selector, text)。它能处理框架托管的输入
   （React/Vue 受控组件），并能可靠清空字段。不要手写原始 CDP 按键序列来清空
   字段：用 type="keyDown" 发送 Ctrl+A 会注入可打印字符 "a"，而不是全选
   （快捷键需要 type="rawKeyDown"）；Input.insertText 会绕过框架事件监听，
   默认值会静默残留并污染输入。
5. 每次写入后都要校验。填写输入框或选择下拉项之后，用 js(...) 读回真实值
   （例如 input 的 value 属性），与目标文本比对后再继续。截图里出现建议下拉框，
   并不能证明字段已经填入正确值。
6. URL 参数：只使用你确定存在的参数（例如列车列表页的 dStation、aStation、
   dDate）。不要臆造筛选参数（车次类型、时间段、席别）——错误参数会静默过滤掉
   你真正要找的结果。不确定时宁可不带该参数。
7. 判断前先等待异步数据。列表/结果页在 goto_url/new_tab 返回后仍会通过 XHR
   加载数据，因此必须先 wait_for_load()，再用 js(...) 读取内容（例如 innerText），
   不要凭过早的截图下结论。数据到达之前不要断言「没有结果」：如果截图调用本身
   异常耗时（>3s），说明页面仍在忙碌，此时任何读取都为时过早。
8. 先对齐今天的日期。任务提到具体日期（出发日、截止日期等）时，先运行
   datetime.now() 获取真实当前日期，再推理售票窗口或时刻表——你的训练截止日期
   不是今天。
"""

    async def initialize(self) -> None:
        await super().initialize(
            connection_type="stdio",
            command="uvx",
            args=["browser-use", "--cli-mcp"],
            server_id="browser_use",
            tool_name_prefix=False,
        )
        self.available_tools.add_tool(Terminate())

    @classmethod
    async def create(cls, **kwargs) -> "BrowserAgent":
        instance = cls(**kwargs)
        await instance.initialize()
        return instance

    async def run(self, request: Optional[str] = None) -> str:
        if not self.mcp_clients.sessions:
            await self.initialize()
        return await super().run(request)
