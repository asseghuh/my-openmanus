"""内置 MCP 默认项。

只放「未写 mcp.json 时也要有的」服务。新增高德等第三方 MCP 请写到
config/mcp.json，不要在这里加 if server_id == ... 的分支。
"""

from typing import Dict

from app.config import MCPServerConfig

BROWSER_USE_INSTRUCTIONS = """\
Browser Use CLI 3.0 在此以 MCP 工具暴露。当 Browser Use skill 显示
`browser-use <<'PY'` 时，请把 Python 代码体传给 `browser_exec`，不要走 CLI。
需要视觉检查时使用 `browser_screenshot`。这两个工具与 CLI 3.0 共用同一持久
browser-harness 会话。

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


def builtin_mcp_servers() -> Dict[str, MCPServerConfig]:
    """代码内置默认 MCP。mcp.json 里同名项会覆盖这里。"""
    return {
        "browser_use": MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["browser-use", "--cli-mcp"],
            enabled=True,
            tool_name_prefix=False,
            inherit_process_env=False,
            env_passthrough=[
                "BROWSER_USE_API_KEY",
                "BROWSER_USE_CLOUD_API_URL",
                "BU_BROWSER_ID",
                "BU_CDP_URL",
                "BU_CDP_WS",
                "BU_NAME",
            ],
            instructions=BROWSER_USE_INSTRUCTIONS,
        )
    }
