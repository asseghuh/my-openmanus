"""解析 stdio MCP 启动命令。

Windows 上 CreateProcess 不能直接跑 npx.cmd / npm.cmd 等脚本，
会报 WinError 2。统一转成 cmd.exe /c，配置里仍写 npx 即可。
"""

from __future__ import annotations

import os
import shutil
from typing import List, Tuple

_WIN_SCRIPT_EXTS = {".cmd", ".bat", ".com"}


def resolve_stdio_launch(command: str, args: List[str] | None = None) -> Tuple[str, List[str]]:
    """把配置中的 command/args 转成当前 OS 可执行的进程参数。"""
    args = list(args or [])
    if not command:
        return command, args

    resolved = shutil.which(command) or command
    if os.name != "nt":
        return resolved, args

    ext = os.path.splitext(resolved)[1].lower()
    if ext in _WIN_SCRIPT_EXTS:
        return _cmd_exe(), ["/c", resolved, *args]

    # which 可能命中无扩展名的 npx 包装脚本，CreateProcess 同样无法启动
    if not ext:
        for wrapper_ext in (".cmd", ".bat"):
            wrapper = shutil.which(command + wrapper_ext)
            if wrapper:
                return _cmd_exe(), ["/c", wrapper, *args]

    return resolved, args


def _cmd_exe() -> str:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    candidate = os.path.join(system_root, "System32", "cmd.exe")
    return candidate if os.path.exists(candidate) else "cmd.exe"
