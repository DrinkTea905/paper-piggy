# -*- coding: utf-8 -*-
"""把浏览器 MCP 的 stdio 连接指向 PaperPiggy 当前核验过的专用浏览器。

用法：python browser_devtools_mcp.py -- node 已安装的CLI.js [工具参数]
或：python browser_devtools_mcp.py -- npx --no-install chrome-devtools-mcp
本桥只读取统一关联，不打开浏览器、不安装依赖，也不修改外部客户端配置。
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import dedicated_browser as DB


class BridgeError(RuntimeError):
    pass


_BROWSER_START_OPTIONS = {
    "executablepath", "browserexecutablepath", "browserpath", "isolated", "autoconnect",
    "userdatadir", "channel", "headless", "wsendpoint", "browserwsendpoint",
    "chromeendpoint", "debuggingport", "remotedebuggingport", "chromearg",
}
_INSTALL_OPTIONS = {"--yes", "-y", "--package", "-p", "--call", "-c", "--install", "--force"}


def _existing_tool_configuration():
    """仅提取现有工具的命令和参数；不返回或记录其他客户端配置。"""
    import tomllib
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            raw = (tomllib.load(stream).get("mcp_servers") or {}).get("chrome-devtools") or {}
        command, arguments = raw.get("command"), raw.get("args")
        if isinstance(command, str) and isinstance(arguments, list) and all(isinstance(a, str) for a in arguments):
            return {"command": command, "args": arguments}
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def default_command():
    """为示例沿用本机已安装工具路径及明确版本；浏览器地址另由统一配置提供。"""
    existing = _existing_tool_configuration()
    package = "chrome-devtools-mcp"
    if existing:
        for value in existing["args"]:
            if re.fullmatch(r"chrome-devtools-mcp@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", value):
                package = value
                break
        configured = Path(existing["command"].strip('"'))
        if configured.name.lower() in ("npx", "npx.cmd", "npx.exe") and configured.is_file():
            return [str(configured), "--no-install", package, "--usageStatistics=false"]
    names = ("npx.cmd", "npx") if sys.platform == "win32" else ("npx",)
    for name in names:
        if located := shutil.which(name):
            return [located, "--no-install", package, "--usageStatistics=false"]
    if sys.platform == "win32":
        directories = []
        for key, suffix in (("PROGRAMFILES", "nodejs"), ("PROGRAMFILES(X86)", "nodejs"),
                            ("APPDATA", "npm"), ("LOCALAPPDATA", "Programs/nodejs")):
            if root := os.environ.get(key):
                directories.append(Path(root) / suffix)
        if node := shutil.which("node.exe"):
            directories.append(Path(node).parent)
        for directory in directories:
            candidate = directory / "npx.cmd"
            if candidate.is_file():
                return [str(candidate), "--no-install", package, "--usageStatistics=false"]
    raise BridgeError("未找到已安装的本机 npx 路径，暂时无法生成浏览器工具连接示例。请先自行安装工具和 Node.js，或明确指定已安装的 node CLI；本桥不会下载依赖。")


def _option_name(value):
    return value.split("=", 1)[0].lstrip("-").replace("-", "").replace("_", "").lower()


def _tool_arguments(arguments, endpoint):
    """保留工具参数，只替换连接地址；其他启动或旁路连接方式直接拒绝。"""
    output = []
    index = 0
    while index < len(arguments):
        value = arguments[index]
        option = _option_name(value) if value.startswith("-") else ""
        if option in _BROWSER_START_OPTIONS:
            raise BridgeError(f"不允许参数 {value.split('=', 1)[0]}：浏览器由 PaperPiggy 按钮管理，本桥只连接当前关联。")
        if option == "browserurl":
            if "=" not in value:
                index += 1
                if index >= len(arguments) or arguments[index].startswith("-"):
                    raise BridgeError("旧 browserUrl 参数缺少地址，请检查工具启动参数。")
            index += 1
            continue
        output.append(value)
        index += 1
    output.extend(["--browserUrl", endpoint])
    return output


def _npx_arguments(arguments):
    for value in arguments:
        option = value.split("=", 1)[0]
        if option in _INSTALL_OPTIONS or (value.startswith("-y") and not value.startswith("--")):
            raise BridgeError("本桥不会安装浏览器工具依赖。请先自行安装工具，再使用 npx --no-install 或本地 node CLI。")
        if option == "--no-install" and value not in ("--no-install", "--no-install=true"):
            raise BridgeError("不能关闭 npx 的 --no-install 限制；本桥不会安装浏览器工具依赖。")
    # 即使调用者忘填，也明确限制为使用已经存在的本地依赖。
    return arguments if "--no-install" in arguments else ["--no-install", *arguments]


def _resolve_command(command, arguments):
    """Windows 的 npx.cmd 改由 node 执行本地 CLI，不经过 cmd.exe 或 shell。"""
    name = Path(command).name.lower()
    is_npx = name in ("npx", "npx.cmd", "npx.exe")
    if is_npx:
        arguments = _npx_arguments(arguments)
    if sys.platform == "win32" and is_npx:
        resolved = shutil.which(command) or (command if Path(command).is_file() else None)
        if not resolved:
            raise BridgeError("未找到本机 npx。请先自行安装浏览器工具及 Node.js；本桥不会下载依赖。")
        directory = Path(resolved).resolve().parent
        cli = directory / "node_modules" / "npm" / "bin" / "npx-cli.js"
        if not cli.is_file():
            raise BridgeError("找不到 npx 对应的本地 npx-cli.js。请改用已安装工具的 node CLI 路径；本桥不会运行命令脚本或安装依赖。")
        node = directory / "node.exe"
        if not node.is_file():
            located = shutil.which("node.exe")
            if not located:
                raise BridgeError("未找到本机 node.exe，无法无窗启动已安装的浏览器工具。")
            node = Path(located)
        return [str(node), str(cli), *arguments]
    if sys.platform == "win32" and Path(command).suffix.lower() in (".cmd", ".bat", ".ps1"):
        raise BridgeError("本桥不通过终端脚本启动工具。请指定 node.exe 与已安装 CLI，或本机 npx --no-install。")
    return [command, *arguments]


def build_command(arguments, state=None):
    """可测试的纯连接准备；没有可核验的运行会话时不返回可执行命令。"""
    if not arguments:
        raise BridgeError("缺少浏览器 MCP 启动命令。用法：browser_devtools_mcp.py -- node 已安装CLI.js")
    state = DB.discovery() if state is None else state
    if state.get("error"):
        raise BridgeError(str(state["error"]))
    if not (state.get("configured") and state.get("running") and state.get("endpoint")):
        raise BridgeError("专用浏览器尚未运行或尚未关联。请先在 PaperPiggy 顶部手动点击“打开专用浏览器”，再重连浏览器工具。")
    # 即使以后发现接口增加其他地址，也不把非本机 URL 转交给工具。
    from urllib.parse import urlsplit
    try:
        address = urlsplit(state["endpoint"])
        if (address.scheme != "http" or address.hostname != "127.0.0.1" or not address.port
                or address.username or address.password or address.path not in ("", "/")
                or address.query or address.fragment):
            raise ValueError("not a local browser endpoint")
    except (TypeError, ValueError) as exc:
        raise BridgeError("专用浏览器未提供有效的本机连接地址，未启动工具。") from exc
    tool_args = _tool_arguments(list(arguments[1:]), state["endpoint"])
    return _resolve_command(arguments[0], tool_args)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if not arguments or arguments.pop(0) != "--":
            raise BridgeError("请在 -- 后指定已安装的浏览器 MCP 命令，例如：-- node 已安装CLI.js")
        command = build_command(arguments)
        # 继承原始 stdio 管道；stdout 仅传递子工具的 MCP JSON-RPC，不写日志。
        child = subprocess.Popen(command, shell=False, close_fds=False,
                                 creationflags=DB.C.SUBPROC_NO_WINDOW)
        return child.wait()
    except (BridgeError, DB.BrowserError, OSError) as exc:
        print(f"[PaperPiggy 浏览器工具] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
