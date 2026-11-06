from typing import Dict, List, Optional

from pydantic import Field, model_validator

from app.agent.browser import BrowserContextHelper
from app.agent.toolcall import ToolCallAgent
from app.config import config
from app.daytona.sandbox import create_sandbox, delete_sandbox
from app.mcp.bootstrap import connect_configured_mcp_servers, inject_mcp_instructions
from app.daytona.tool_base import SandboxToolsBase
from app.logger import logger
from app.prompt.manus import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.tool import Terminate, ToolCollection
from app.tool.ask_human import AskHuman
from app.tool.mcp import MCPClients, MCPClientTool
from app.tool.sandbox.sb_browser_tool import SandboxBrowserTool
from app.tool.sandbox.sb_files_tool import SandboxFilesTool
from app.tool.sandbox.sb_shell_tool import SandboxShellTool
from app.tool.sandbox.sb_vision_tool import SandboxVisionTool


class SandboxManus(ToolCallAgent):
    """A versatile general-purpose agent with support for both local and MCP tools."""

    name: str = "SandboxManus"
    description: str = "通用智能体，可使用包括 MCP 工具在内的多种沙箱工具完成各类任务"

    system_prompt: str = SYSTEM_PROMPT.format(directory=config.workspace_root)
    next_step_prompt: str = NEXT_STEP_PROMPT

    max_observe: int = 10000
    max_steps: int = 20

    # MCP clients for remote tool access
    mcp_clients: MCPClients = Field(default_factory=MCPClients)

    # Add general-purpose tools to the tool collection
    available_tools: ToolCollection = Field(
        default_factory=lambda: ToolCollection(
            # PythonExecute(),
            # BrowserUseTool(),
            # StrReplaceEditor(),
            AskHuman(),
            Terminate(),
        )
    )

    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])
    browser_context_helper: Optional[BrowserContextHelper] = None

    # Track connected MCP servers
    connected_servers: Dict[str, str] = Field(
        default_factory=dict
    )  # server_id -> url/command
    mcp_instruction_servers: set[str] = Field(default_factory=set, exclude=True)
    _initialized: bool = False
    sandbox_link: Optional[dict[str, dict[str, str]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def initialize_helper(self) -> "SandboxManus":
        """Initialize basic components synchronously."""
        self.browser_context_helper = BrowserContextHelper(self)
        return self

    @classmethod
    async def create(cls, **kwargs) -> "SandboxManus":
        """Factory method to create and properly initialize a Manus instance."""
        instance = cls(**kwargs)
        await instance.initialize_mcp_servers()
        await instance.initialize_sandbox_tools()
        instance._initialized = True
        return instance

    async def initialize_sandbox_tools(
        self,
        password: str = config.daytona.VNC_password,
    ) -> None:
        try:
            # 创建新沙箱
            if password:
                sandbox = create_sandbox(password=password)
                self.sandbox = sandbox
            else:
                raise ValueError("password must be provided")
            vnc_link = sandbox.get_preview_link(6080)
            website_link = sandbox.get_preview_link(8080)
            vnc_url = vnc_link.url if hasattr(vnc_link, "url") else str(vnc_link)
            website_url = (
                website_link.url if hasattr(website_link, "url") else str(website_link)
            )

            # Get the actual sandbox_id from the created sandbox
            actual_sandbox_id = sandbox.id if hasattr(sandbox, "id") else "new_sandbox"
            if not self.sandbox_link:
                self.sandbox_link = {}
            self.sandbox_link[actual_sandbox_id] = {
                "vnc": vnc_url,
                "website": website_url,
            }
            logger.info(f"VNC URL: {vnc_url}")
            logger.info(f"Website URL: {website_url}")
            SandboxToolsBase._urls_printed = True
            sb_tools = [
                SandboxBrowserTool(sandbox),
                SandboxFilesTool(sandbox),
                SandboxShellTool(sandbox),
                SandboxVisionTool(sandbox),
            ]
            self.available_tools.add_tools(*sb_tools)

        except Exception as e:
            logger.error(f"Error initializing sandbox tools: {e}")
            raise

    async def initialize_mcp_servers(self) -> None:
        """按配置连接全部 MCP，不针对某个服务写死。"""
        await connect_configured_mcp_servers(self)

    async def connect_mcp_server(
        self,
        server_url: str,
        server_id: str = "",
        use_stdio: bool = False,
        stdio_args: List[str] = None,
        tool_name_prefix: bool = True,
        stdio_env: Optional[Dict[str, str]] = None,
        extra_instructions: Optional[str] = None,
    ) -> None:
        """Connect to an MCP server and add its tools."""
        if use_stdio:
            await self.mcp_clients.connect_stdio(
                server_url,
                stdio_args or [],
                server_id,
                tool_name_prefix=tool_name_prefix,
                env=stdio_env,
            )
            self.connected_servers[server_id or server_url] = server_url
        else:
            await self.mcp_clients.connect_sse(server_url, server_id)
            self.connected_servers[server_id or server_url] = server_url

        # Update available tools with only the new tools from this server
        new_tools = [
            tool for tool in self.mcp_clients.tools if tool.server_id == server_id
        ]
        self.available_tools.add_tools(*new_tools)
        inject_mcp_instructions(self, server_id or server_url, extra_instructions)

    async def disconnect_mcp_server(self, server_id: str = "") -> None:
        """Disconnect from an MCP server and remove its tools."""
        await self.mcp_clients.disconnect(server_id)
        if server_id:
            self.connected_servers.pop(server_id, None)
        else:
            self.connected_servers.clear()

        # Rebuild available tools without the disconnected server's tools
        base_tools = [
            tool
            for tool in self.available_tools.tools
            if not isinstance(tool, MCPClientTool)
        ]
        self.available_tools = ToolCollection(*base_tools)
        self.available_tools.add_tools(*self.mcp_clients.tools)

    async def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox by ID."""
        try:
            await delete_sandbox(sandbox_id)
            logger.info(f"Sandbox {sandbox_id} deleted successfully")
            if sandbox_id in self.sandbox_link:
                del self.sandbox_link[sandbox_id]
        except Exception as e:
            logger.error(f"Error deleting sandbox {sandbox_id}: {e}")
            raise e

    async def cleanup(self):
        """Clean up Manus agent resources."""
        if self.browser_context_helper:
            await self.browser_context_helper.cleanup_browser()
        # Disconnect from all MCP servers only if we were initialized
        if self._initialized:
            await self.disconnect_mcp_server()
            await self.delete_sandbox(self.sandbox.id if self.sandbox else "unknown")
            self._initialized = False

    async def think(self) -> bool:
        """Process current state and decide next actions with appropriate context."""
        if not self._initialized:
            await self.initialize_mcp_servers()
            self._initialized = True

        original_prompt = self.next_step_prompt
        recent_messages = self.memory.messages[-3:] if self.memory.messages else []
        browser_in_use = any(
            tc.function.name == SandboxBrowserTool().name
            for msg in recent_messages
            if msg.tool_calls
            for tc in msg.tool_calls
        )

        if browser_in_use:
            self.next_step_prompt = (
                await self.browser_context_helper.format_next_step_prompt()
            )

        result = await super().think()

        # Restore original prompt
        self.next_step_prompt = original_prompt

        return result
