"""按配置连接 MCP：代码只做通用循环，不针对某个 server_id 写死分支。"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional

from app.config import MCPServerConfig, config
from app.logger import logger
from app.mcp.defaults import builtin_mcp_servers
from app.schema import Message

_ENV_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
_BROWSER_DISABLE_VALUES = {"1", "true", "yes"}


def expand_env_value(value: str) -> str:
    """把 ${VAR} 替换成当前进程环境变量。"""

    def _replace(match: re.Match) -> str:
        return os.getenv(match.group(1), "")

    return _ENV_PLACEHOLDER.sub(_replace, value or "")


def build_stdio_env(server_config: MCPServerConfig) -> Dict[str, str]:
    """组装 stdio 子进程环境。

    inherit_process_env=True 时带上 PATH 等，否则 npx/uvx 在 Windows 上会找不到。
    为 False 时只传 env_passthrough + env，避免把无关密钥泄漏给子进程。
    """
    env: Dict[str, str] = dict(os.environ) if server_config.inherit_process_env else {}
    for name in server_config.env_passthrough:
        value = os.getenv(name)
        if value:
            env[name] = value
    for key, raw in (server_config.env or {}).items():
        env[key] = expand_env_value(raw)
    return env


def get_enabled_mcp_servers() -> Dict[str, MCPServerConfig]:
    """内置默认 ∪ config/mcp.json，同名以 json 为准；enabled=false 的跳过。"""
    merged: Dict[str, MCPServerConfig] = dict(builtin_mcp_servers())
    file_servers = getattr(getattr(config, "mcp_config", None), "servers", None) or {}
    merged.update(file_servers)

    disable_browser = os.getenv("OPENMANUS_DISABLE_BROWSER_USE", "").lower() in (
        _BROWSER_DISABLE_VALUES
    )
    if disable_browser:
        merged.pop("browser_use", None)

    return {
        server_id: cfg
        for server_id, cfg in merged.items()
        if getattr(cfg, "enabled", True)
    }


async def connect_configured_mcp_servers(agent) -> None:
    """按配置逐个连接 MCP，单个失败不影响其余。"""
    for server_id, cfg in get_enabled_mcp_servers().items():
        try:
            await _connect_one(agent, server_id, cfg)
            logger.info(f"Connected to MCP server {server_id}")
        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_id}: {e}")


async def _connect_one(agent, server_id: str, cfg: MCPServerConfig) -> None:
    extra: Optional[str] = cfg.instructions or None
    if cfg.type == "sse":
        if not cfg.url:
            raise ValueError(f"MCP server {server_id} 缺少 url")
        await agent.connect_mcp_server(
            cfg.url,
            server_id,
            extra_instructions=extra,
        )
        return

    if cfg.type == "stdio":
        if not cfg.command:
            raise ValueError(f"MCP server {server_id} 缺少 command")
        await agent.connect_mcp_server(
            cfg.command,
            server_id,
            use_stdio=True,
            stdio_args=cfg.args,
            tool_name_prefix=cfg.tool_name_prefix,
            stdio_env=build_stdio_env(cfg),
            extra_instructions=extra,
        )
        return

    raise ValueError(f"MCP server {server_id} 不支持的 type: {cfg.type}")


def inject_mcp_instructions(
    agent, server_id: str, extra_instructions: Optional[str] = None
) -> None:
    """把配置说明与 MCP initialize 返回的 instructions 写入 agent 记忆。"""
    already = getattr(agent, "mcp_instruction_servers", None)
    if already is not None and server_id in already:
        return

    parts = []
    if extra_instructions:
        parts.append(extra_instructions.strip())
    server_notes = getattr(
        getattr(agent, "mcp_clients", None), "server_instructions", {}
    ).get(server_id)
    if server_notes:
        parts.append(f"MCP 服务器说明：\n{server_notes}")
    if not parts:
        return

    memory = getattr(agent, "memory", None)
    if memory is None:
        return
    memory.add_message(Message.system_message("\n\n".join(parts)))
    if already is not None:
        already.add(server_id)
