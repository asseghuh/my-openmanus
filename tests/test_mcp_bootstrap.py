import os

from app.config import MCPServerConfig
from app.mcp.bootstrap import build_stdio_env, expand_env_value, get_enabled_mcp_servers
from app.mcp.stdio_cmd import resolve_stdio_launch


def test_expand_env_value(monkeypatch):
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "amap-test")
    assert expand_env_value("${AMAP_MAPS_API_KEY}") == "amap-test"
    assert expand_env_value("literal") == "literal"


def test_from_raw_accepts_cursor_style():
    cfg = MCPServerConfig.from_raw(
        {
            "command": "npx",
            "args": ["-y", "@amap/amap-maps-mcp-server"],
            "disabled": False,
            "env": {"AMAP_MAPS_API_KEY": "${AMAP_MAPS_API_KEY}"},
        }
    )
    assert cfg.type == "stdio"
    assert cfg.enabled is True
    assert cfg.command == "npx"


def test_stdio_env_passthrough_does_not_inherit(monkeypatch):
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "amap-test")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    cfg = MCPServerConfig(
        type="stdio",
        command="npx",
        inherit_process_env=False,
        env_passthrough=["AMAP_MAPS_API_KEY"],
    )
    env = build_stdio_env(cfg)
    assert env == {"AMAP_MAPS_API_KEY": "amap-test"}


def test_stdio_env_expands_placeholders(monkeypatch):
    monkeypatch.setenv("AMAP_MAPS_API_KEY", "amap-test")
    cfg = MCPServerConfig(
        type="stdio",
        command="npx",
        inherit_process_env=False,
        env={"AMAP_MAPS_API_KEY": "${AMAP_MAPS_API_KEY}"},
    )
    env = build_stdio_env(cfg)
    assert env["AMAP_MAPS_API_KEY"] == "amap-test"


def test_configured_server_merges_without_special_case(monkeypatch):
    from app.agent import manus as manus_module

    monkeypatch.setenv("OPENMANUS_DISABLE_BROWSER_USE", "1")
    monkeypatch.setattr(
        manus_module.config.mcp_config,
        "servers",
        {
            "amap-maps": MCPServerConfig(
                type="stdio",
                command="npx",
                args=["-y", "@amap/amap-maps-mcp-server"],
                tool_name_prefix=False,
                inherit_process_env=False,
                env={"AMAP_MAPS_API_KEY": "${AMAP_MAPS_API_KEY}"},
            )
        },
    )
    servers = get_enabled_mcp_servers()
    assert "browser_use" not in servers
    assert "amap-maps" in servers
    assert servers["amap-maps"].command == "npx"


def test_builtin_browser_use_when_no_file_servers(monkeypatch):
    from app.agent import manus as manus_module

    monkeypatch.delenv("OPENMANUS_DISABLE_BROWSER_USE", raising=False)
    monkeypatch.setattr(manus_module.config.mcp_config, "servers", {})
    servers = get_enabled_mcp_servers()
    assert "browser_use" in servers
    assert servers["browser_use"].command == "uvx"


def test_windows_npx_is_launched_via_cmd(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    def fake_which(name: str):
        mapping = {
            "npx": r"S:\Program Files\nodejs\npx.CMD",
            "npx.cmd": r"S:\Program Files\nodejs\npx.cmd",
        }
        return mapping.get(name)

    monkeypatch.setattr("app.mcp.stdio_cmd.shutil.which", fake_which)
    command, args = resolve_stdio_launch("npx", ["-y", "@amap/amap-maps-mcp-server"])
    assert command.lower().endswith("cmd.exe")
    assert args[0] == "/c"
    assert args[1].lower().endswith("npx.cmd")
    assert args[2:] == ["-y", "@amap/amap-maps-mcp-server"]


def test_windows_exe_is_not_wrapped(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        "app.mcp.stdio_cmd.shutil.which",
        lambda name: r"C:\tools\uvx.exe" if name == "uvx" else None,
    )
    command, args = resolve_stdio_launch("uvx", ["browser-use", "--cli-mcp"])
    assert command.lower().endswith("uvx.exe")
    assert args == ["browser-use", "--cli-mcp"]
