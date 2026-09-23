#!/usr/bin/env python3
"""check_plugin_deps.py — 审计 Kimi 插件的依赖可用性（「真正可用性」检查，validate_plugin.py 的加深口径）。

validate_plugin.py 只查 manifest 结构与组件路径存在性；本脚本回答「这个插件装上能不能跑起来」：

判定基准（本项目运行时能提供的能力）：
  - 内置工具（kimi-code agent-core tools/builtin 及 background/、cron/）：Bash / Read / Write /
    Edit / Glob / Grep / ReadMediaFile / FetchURL / WebSearch / Agent / AgentSwarm /
    AskUserQuestion / Skill / TodoList / EnterPlanMode / ExitPlanMode / CreateGoal / GetGoal /
    SetGoalBudget / UpdateGoal / CronCreate / CronDelete / CronList / TaskList / TaskOutput /
    TaskStop。
    skill/commands/agents 的 .md 正文只是提示词，提到的工具名若不在上表且没有对应能力，
    agent 无法照字面调用（记 warning；WebFetch→FetchURL、TodoWrite→TodoList、Task→Agent
    这类有对应能力的也记 warning 提示名称差异）。
  - skill 里「调用命令」最终都走 Bash，所以 CLI 依赖是否可用 = 本机存在或有公开安装方式。

检查项：
  A. mcpServers（硬依赖，起不来该插件核心能力就缺失）：
     - URL 型：--online 时探测可达性；连接失败/DNS 失败/404 → blocker；401/403/405 → 活（需鉴权记 warning）。
     - stdio 型：
       * command 为 ./ 相对路径或含 / 的相对路径 → 插件内文件必须存在；
       * command 为解释器/启动器（python3/python/node/npx/uvx/uv/deno/bun/bash/sh/env）→
         args 里 ./ 开头的插件内脚本必须存在；npx/uvx/uv 后的包名 --online 查
         npm registry / PyPI，不存在 → blocker；
       * 其他裸命令（unforgit-mcp、tessl 等）→ 本机 PATH 没有时查 npm registry，
         registry 也没有 → blocker；registry 有 → warning「需先安装」；
       * 占位符（__CONFIGURED_BY_INSTALLER__ 等全大写占位）→ blocker「需安装器配置」；
       * powershell.exe / pwsh → 仅 Windows 平台，桌面端起不来 → blocker。
     - env 里的 ${VAR} → warning「需配置环境变量」。
  B. hooks/commands：command 字符串里 ./ 开头的插件内路径必须存在，不存在 → blocker。
  C. skills/commands/agents 的 .md 正文：
     - mcp__<server>__<tool> 引用：server 必须在 manifest mcpServers 里声明，否则 → blocker；
     - 提到 Claude/Codex 生态工具名 → 按上面的映射/无对应口径记 warning。
  D. 配套安装：插件根有 package.json 且 MCP/脚本引用本地 node 脚本 → warning「需 npm install」；
     有 setup.sh → warning「需先运行 setup.sh 安装本体」。

输出：每个插件 {name, usable, blockers, warnings}；blocker 非空即「不可用」。
判定口径：以「register-personal 登记 + 客户端「个人」页签安装」的标准安装路径为准
（daimon 个人市场无 mcpServers 限制）；客户端「从文件安装」（侧载）对 MCP 的限制
只是侧载自身约束，不纳入本脚本的可用性判定。
用法：
  python3 check_plugin_deps.py <plugins父目录或单个插件目录> [--offline] [--json <path>]
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

# ---- 本项目运行时内置工具（判定基准声明；对照来源：kimi-code
# packages/agent-core/src/tools/builtin/index.ts 及其 background/、cron/ 目录，
# 新增工具时同步更新）----
KIMI_BUILTIN_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "ReadMediaFile",
    "FetchURL", "WebSearch", "Agent", "AgentSwarm", "AskUserQuestion", "Skill",
    "TodoList", "EnterPlanMode", "ExitPlanMode",
    "CreateGoal", "GetGoal", "SetGoalBudget", "UpdateGoal",
    "CronCreate", "CronDelete", "CronList", "TaskList", "TaskOutput", "TaskStop",
}

# Claude/Codex 生态工具名 → Kimi 对应能力（有对应记 warning 名称差异；无对应记 warning 无此能力）
TOOL_NAME_EQUIVALENTS = {
    "WebFetch": "FetchURL", "TodoWrite": "TodoList", "Task": "Agent",
    "NotebookRead": "Read", "NotebookEdit": "Edit", "BashOutput": None,
    "KillShell": None, "SlashCommand": None, "ListMcpResources": None,
    "ReadMcpResource": None, "WebSearch": "WebSearch",
}

# 标准解释器/启动器（desktop 环境必有或有官方安装方式；uv/docker/bun/deno 需先装，记 warning）
INTERPRETERS_OK = {"python3", "python", "node", "npx", "bash", "sh", "env"}
INSTALLABLE_LAUNCHERS = {"uvx": "uv（https://docs.astral.sh/uv/）", "uv": "uv（https://docs.astral.sh/uv/）",
                         "docker": "Docker", "bun": "Bun", "deno": "Deno"}
WINDOWS_ONLY_COMMANDS = {"powershell.exe", "powershell", "pwsh.exe", "cmd.exe"}

PLACEHOLDER_RE = re.compile(r"^__[A-Z_]+__$")
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MCP_TOOL_REF_RE = re.compile(r"mcp__([A-Za-z0-9_-]+)__")
# 正文里提到的工具名：单词边界精确匹配预置清单（避免误报普通英文词）
TOOL_MENTION_RE = re.compile(r"\b(WebFetch|TodoWrite|Task|NotebookEdit|NotebookRead|BashOutput|"
                             r"KillShell|SlashCommand|ListMcpResources|ReadMcpResource)\b")

_NPM_CACHE = {}
_PYPI_CACHE = {}
_URL_CACHE = {}


def npm_package_exists(name):
    if name in _NPM_CACHE:
        return _NPM_CACHE[name]
    ok = False
    try:
        req = urllib.request.Request(f"https://registry.npmjs.org/{name.replace('/', '%2f')}",
                                     method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
    except urllib.error.HTTPError as e:
        ok = e.code != 404
    except Exception:  # noqa: BLE001 — 网络异常不算「不存在」，跳过该项判定
        ok = None
    _NPM_CACHE[name] = ok
    return ok


def pypi_package_exists(name):
    if name in _PYPI_CACHE:
        return _PYPI_CACHE[name]
    ok = False
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=15) as resp:
            ok = resp.status == 200
    except urllib.error.HTTPError as e:
        ok = e.code != 404
    except Exception:  # noqa: BLE001
        ok = None
    _PYPI_CACHE[name] = ok
    return ok


def url_alive(url):
    if url in _URL_CACHE:
        return _URL_CACHE[url]
    alive = None
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, method="GET",
                                         headers={"User-Agent": "kimi-plugin-dep-check"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                alive = resp.status
            break
        except urllib.error.HTTPError as e:
            alive = e.code  # 401/403/405 都算服务在线
            break
        except Exception:  # noqa: BLE001 — DNS/连接失败/超时等网络层问题
            alive = 0
            if attempt == 1:
                # 瞬时网络抖动重试一次：批量转换联网核实量大，单次超时不该
                # 直接把可用插件误判成「URL 不可达」（真实宕机第二次也会失败）
                time.sleep(1)
    _URL_CACHE[url] = alive
    return alive


def strip_version_spec(pkg):
    """包名参数去掉版本后缀：pkg@1.2.3 → pkg；@scope/name@1.2.3 → @scope/name；
    pkg==1.2.3（uvx/pip 形式）→ pkg。"""
    if pkg.startswith("@"):
        head, _, _ver = pkg.rpartition("@")
        return head or pkg
    if "==" in pkg:
        return pkg.split("==", 1)[0]
    return pkg.split("@", 1)[0]


def first_npx_package(args):
    """npx args 里第一个非 flag 的 token 是包名；-p/--package 的值也算。"""
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-p", "--package"):
            i += 1
            if i < len(args):
                return strip_version_spec(args[i])
        elif a.startswith("-"):
            pass
        else:
            return strip_version_spec(a)
        i += 1
    return None


def first_uv_package(args):
    """uv run <pkg> / uv tool run <pkg> / uvx <pkg>：取子命令后第一个非 flag token。"""
    rest = list(args)
    if rest and rest[0] == "tool":
        rest = rest[1:]
    if rest and rest[0] == "run":
        rest = rest[1:]
    for a in rest:
        if not a.startswith("-"):
            return strip_version_spec(a)
    return None


def rel_paths_in_command(command):
    """命令字符串里 ./ 开头的相对路径 token（去引号）。"""
    out = []
    for tok in re.split(r"\s+", command):
        tok = tok.strip('"\'').rstrip(";&|")
        if tok.startswith("./"):
            out.append(tok)
    return out


def check_mcp_server(plugin_dir, sname, cfg, online, blockers, warnings):
    if cfg.get("url"):
        url = cfg["url"]
        if re.match(r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)([:/]|$)", url):
            warnings.append(f"MCP server {sname!r} 指向本机回环地址，需用户先在本地部署该服务: {url}")
            return
        if online:
            status = url_alive(url)
            if status in (0, None):
                blockers.append(f"MCP server {sname!r} 的 URL 不可达: {url}")
            elif status == 404:
                blockers.append(f"MCP server {sname!r} 的 URL 返回 404: {url}")
            elif status in (401, 403):
                warnings.append(f"MCP server {sname!r} 的 URL 需要鉴权（HTTP {status}）: {url}")
        return
    command = cfg.get("command") or ""
    args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
    if PLACEHOLDER_RE.match(command) or any(PLACEHOLDER_RE.match(str(a)) for a in args):
        blockers.append(f"MCP server {sname!r} 的启动命令是未配置占位符: {command}")
        return
    base = os.path.basename(command)
    if base.lower() in WINDOWS_ONLY_COMMANDS:
        blockers.append(f"MCP server {sname!r} 的启动命令仅 Windows 可用: {command}")
        return
    # 插件内相对路径命令（normpath 归一尾斜杠/正斜杠：\\?\ 前缀路径不做斜杠转换）
    if command.startswith("./") or ("/" in command and not os.path.isabs(command)):
        if not os.path.isfile(os.path.normpath(os.path.join(plugin_dir, command))):
            blockers.append(f"MCP server {sname!r} 的启动文件在插件目录里不存在: {command}")
        return
    if base in INTERPRETERS_OK:
        pass  # 解释器本身没问题，继续查 args
    elif base in INSTALLABLE_LAUNCHERS:
        if not shutil.which(base):
            warnings.append(f"MCP server {sname!r} 需要先安装 {INSTALLABLE_LAUNCHERS[base]}")
    else:
        # 生僻裸命令：本机 PATH → npm/PyPI（命令名与插件名都试，包名常与命令名不同，
        # 如 npm 包 unforgit 提供 unforgit-mcp 命令）逐级判定
        if shutil.which(command):
            pass
        elif not online:
            warnings.append(f"MCP server {sname!r} 的启动命令 {command!r} 本机不存在，"
                            f"未能在线核实（--offline）")
        else:
            plugin_name = os.path.basename(plugin_dir)
            npm_hit = next((c for c in (command, plugin_name) if npm_package_exists(c)), None)
            pypi_hit = next((c for c in (command, plugin_name) if pypi_package_exists(c)), None)
            if npm_hit:
                warnings.append(f"MCP server {sname!r} 需要先全局安装 npm 包 {npm_hit}"
                                f"（提供 {command} 命令）")
            elif pypi_hit:
                warnings.append(f"MCP server {sname!r} 需要先安装 PyPI 包 {pypi_hit}"
                                f"（提供 {command} 命令）")
            else:
                blockers.append(f"MCP server {sname!r} 的启动命令 {command!r} 无法确认公开安装途径"
                                f"（本机 PATH、npm、PyPI 均无；如源仓库提供安装脚本/Release 需手动安装）")
    # args 里的插件内脚本
    for a in args:
        a = str(a)
        if a.startswith("./") and not os.path.exists(os.path.normpath(os.path.join(plugin_dir, a))):
            blockers.append(f"MCP server {sname!r} 引用的路径在插件目录里不存在: {a}")
    # npx/uvx/uv 包名 registry 核实
    if online and base == "npx":
        pkg = first_npx_package(args)
        if pkg and npm_package_exists(pkg) is False:
            blockers.append(f"MCP server {sname!r} 的 npm 包在 registry 不存在: {pkg}")
    if online and base in ("uvx", "uv"):
        pkg = first_uv_package(args)
        if pkg and not pkg.startswith(".") and "/" not in pkg \
                and pypi_package_exists(pkg) is False:
            blockers.append(f"MCP server {sname!r} 的 PyPI 包不存在: {pkg}")
    for k, v in (cfg.get("env") or {}).items():
        for var in ENV_VAR_RE.findall(str(v)):
            warnings.append(f"MCP server {sname!r} 需要配置环境变量 {var}")


def check_plugin(plugin_dir, online=True):
    name = os.path.basename(plugin_dir)
    blockers, warnings = [], []
    mf = os.path.join(plugin_dir, "kimi.plugin.json")
    if not os.path.isfile(mf):
        return {"name": name, "usable": False, "blockers": ["缺 kimi.plugin.json"], "warnings": []}
    m = json.load(open(mf, encoding="utf-8"))

    for sname, cfg in (m.get("mcpServers") or {}).items():
        if isinstance(cfg, dict):
            check_mcp_server(plugin_dir, sname, cfg, online, blockers, warnings)

    for h in (m.get("hooks") or []):
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        for p in rel_paths_in_command(cmd):
            if not os.path.exists(os.path.normpath(os.path.join(plugin_dir, p))):
                blockers.append(f"hook（{h.get('event')}）引用的文件在插件目录里不存在: {p}")

    # .md 正文扫描：skills/commands/agents + 根 SKILL.md
    md_files = []
    for field in ("skills", "commands", "agents"):
        raw = m.get(field)
        for e in (raw if isinstance(raw, list) else [raw]):
            if not isinstance(e, str):
                continue
            p = os.path.normpath(os.path.join(plugin_dir, e[2:] if e.startswith("./") else e))
            if os.path.isfile(p) and p.endswith(".md"):
                md_files.append(p)
            elif os.path.isdir(p):
                for dirpath, _d, files in os.walk(p):
                    md_files += [os.path.join(dirpath, f) for f in files if f.endswith(".md")]
    if os.path.isfile(os.path.join(plugin_dir, "SKILL.md")):
        md_files.append(os.path.join(plugin_dir, "SKILL.md"))

    declared_servers = set((m.get("mcpServers") or {}).keys())
    seen_tool_notes = set()
    for md in md_files:
        try:
            text = open(md, encoding="utf-8", errors="replace").read()
        except Exception:  # noqa: BLE001
            continue
        rel = os.path.relpath(md, plugin_dir)
        in_docs = any(part in ("references", "reference", "docs", "doc", "examples")
                      for part in rel.split(os.sep))
        if not in_docs:
            for server in set(MCP_TOOL_REF_RE.findall(text)):
                # mcp__<server>__<tool>：提示词里引用未声明的 MCP server——agent 运行时
                # 会发现工具不存在并自然降级，记 warning 提示用户可能需要自行配置
                if server not in declared_servers and server != m.get("name"):
                    warnings.append(f"{rel} 提到未在本插件声明的 MCP server mcp__{server}__*"
                                    f"（若流程强依赖它，需用户自行配置该 server）")
        for tool in set(TOOL_MENTION_RE.findall(text)):
            if tool in seen_tool_notes:
                continue
            seen_tool_notes.add(tool)
            equiv = TOOL_NAME_EQUIVALENTS.get(tool)
            if equiv:
                warnings.append(f"{rel} 提到工具 {tool}（Kimi 对应能力为 {equiv}，名称不同不影响使用）")
            else:
                warnings.append(f"{rel} 提到工具 {tool}（Kimi 内置工具集中没有对应能力）")

    if os.path.isfile(os.path.join(plugin_dir, "package.json")):
        uses_local_node = any(
            (c.get("command") == "node" and any(str(a).startswith("./") for a in (c.get("args") or [])))
            for c in (m.get("mcpServers") or {}).values() if isinstance(c, dict))
        if uses_local_node and not os.path.isdir(os.path.join(plugin_dir, "node_modules")):
            warnings.append("含 package.json 且 MCP 用本地 node 脚本，需先在插件目录 npm install")
    if os.path.isfile(os.path.join(plugin_dir, "setup.sh")):
        warnings.append("含 setup.sh，需先运行它安装插件本体/依赖")

    return {"name": m.get("name", name), "usable": not blockers,
            "blockers": blockers, "warnings": sorted(set(warnings))}


def main():
    # 中文输出钉死 UTF-8：Windows GBK locale + 管道捕获下 print 会 UnicodeEncodeError
    pc.reconfigure_stdio_utf8()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    online = "--offline" not in sys.argv
    json_out = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        json_out = sys.argv[i + 1]
    if not args:
        sys.exit("用法: python3 check_plugin_deps.py <plugins父目录或单个插件目录> [--offline] [--json <path>]")
    root = args[0]
    if os.path.isfile(os.path.join(root, "kimi.plugin.json")):
        dirs = [root]
    else:
        dirs = sorted(os.path.join(root, d) for d in os.listdir(root)
                      if os.path.isfile(os.path.join(root, d, "kimi.plugin.json")))
    results = [check_plugin(d, online=online) for d in dirs]
    usable = [r for r in results if r["usable"]]
    unusable = [r for r in results if not r["usable"]]
    print(f"共 {len(results)} 个插件：可用 {len(usable)}，不可用 {len(unusable)}")
    for r in unusable:
        print(f"  [不可用] {r['name']}: " + "；".join(r["blockers"]))
    if json_out:
        Path(json_out).write_text(json.dumps(
            {"total": len(results), "usable": len(usable), "unusable": len(unusable),
             "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON -> {json_out}")


if __name__ == "__main__":
    main()
