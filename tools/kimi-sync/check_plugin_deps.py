#!/usr/bin/env python3
"""check_plugin_deps.py — Audit Kimi plugin dependency usability (deeper runtime check beyond validate_plugin.py).

validate_plugin.py only checks manifest structure and component path existence; this script
answers whether the plugin will actually run once installed:

Baseline (capabilities provided by this runtime):
  - Built-in tools (kimi-code agent-core tools/builtin, background/, cron/): Bash / Read / Write /
    Edit / Glob / Grep / ReadMediaFile / FetchURL / WebSearch / Agent / AgentSwarm /
    AskUserQuestion / Skill / TodoList / EnterPlanMode / ExitPlanMode / CreateGoal / GetGoal /
    SetGoalBudget / UpdateGoal / CronCreate / CronDelete / CronList / TaskList / TaskOutput /
    TaskStop.
    Markdown bodies in skills/commands/agents are prompts; tool names not in the list above and
    without an equivalent cannot be called literally by the agent (logged as a warning; mapped
    names like WebFetch->FetchURL, TodoWrite->TodoList, Task->Agent also log a warning noting
    the name difference).
  - Commands invoked by skills go through Bash, so CLI availability means present locally or
    installable via a public registry.

Checks:
  A. mcpServers (hard dependency; core capability is missing if it cannot start):
     - URL-based: probe reachability when online; connection/DNS failure or 404 -> blocker;
       401/403/405 -> alive (auth required logged as warning).
     - stdio-based:
       * command is a ./ or relative path containing / -> file must exist in plugin dir;
       * command is an interpreter/launcher (python3/python/node/npx/uvx/uv/deno/bun/bash/sh/env) ->
         ./-prefixed scripts in args must exist in plugin dir; package names after npx/uvx/uv are
         checked against npm registry / PyPI when online, missing -> blocker;
       * other bare commands (unforgit-mcp, tessl, etc.) -> if not on PATH, check npm/PyPI;
         absent there too -> blocker; present in registry -> warning ("needs installation");
       * placeholders (all-caps like __CONFIGURED_BY_INSTALLER__) -> blocker ("needs installer config");
       * powershell.exe / pwsh -> Windows-only, cannot run on desktop -> blocker.
     - ${VAR} in env -> warning ("environment variable must be configured").
  B. hooks/commands: ./-prefixed paths inside command strings must exist in plugin dir, else -> blocker.
  C. Markdown bodies in skills/commands/agents:
     - mcp__<server>__<tool> references: server must be declared in manifest mcpServers, else -> warning;
     - mentions of Claude/Codex ecosystem tool names -> warning per mapping above.
  D. Companion setup: package.json in plugin root with local node MCP script -> warning ("needs npm install");
     setup.sh present -> warning ("must run setup.sh first").

Output: per-plugin {name, usable, blockers, warnings}; non-empty blockers means unusable.
Scope: evaluated against the standard "register-personal + Personal tab install" path
(daimon personal marketplace has no mcpServers restriction); sideload ("install from file")
MCP restrictions are a sideload-only constraint and excluded here.
Usage:
  python3 check_plugin_deps.py <plugins-parent-dir-or-single-plugin-dir> [--offline] [--json <path>]
"""
import json, os, re, shutil, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

# ---- Runtime built-in tools baseline (source: kimi-code
# packages/agent-core/src/tools/builtin/index.ts plus background/ and cron/;
# keep in sync when new tools are added) ----
KIMI_BUILTIN_TOOLS = {
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "ReadMediaFile",
    "FetchURL", "WebSearch", "Agent", "AgentSwarm", "AskUserQuestion", "Skill",
    "TodoList", "EnterPlanMode", "ExitPlanMode",
    "CreateGoal", "GetGoal", "SetGoalBudget", "UpdateGoal",
    "CronCreate", "CronDelete", "CronList", "TaskList", "TaskOutput", "TaskStop",
}

# Claude/Codex ecosystem tool names -> Kimi equivalents (mapped -> name-diff warning; None -> unsupported warning)
TOOL_NAME_EQUIVALENTS = {
    "WebFetch": "FetchURL", "TodoWrite": "TodoList", "Task": "Agent",
    "NotebookRead": "Read", "NotebookEdit": "Edit", "BashOutput": None,
    "KillShell": None, "SlashCommand": None, "ListMcpResources": None,
    "ReadMcpResource": None, "WebSearch": "WebSearch",
}

# Standard interpreters/launchers (expected on desktop or officially installable; uv/docker/bun/deno warn if missing)
INTERPRETERS_OK = {"python3", "python", "node", "npx", "bash", "sh", "env"}
INSTALLABLE_LAUNCHERS = {"uvx": "uv (https://docs.astral.sh/uv/)", "uv": "uv (https://docs.astral.sh/uv/)",
                         "docker": "Docker", "bun": "Bun", "deno": "Deno"}
WINDOWS_ONLY_COMMANDS = {"powershell.exe", "powershell", "pwsh.exe", "cmd.exe"}

PLACEHOLDER_RE = re.compile(r"^__[A-Z_]+__$")
ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MCP_TOOL_REF_RE = re.compile(r"mcp__([A-Za-z0-9_-]+)__")
# Tool mentions in markdown: exact word-boundary match against preset list to avoid false positives
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
    except Exception:  # noqa: BLE001 — network errors do not prove non-existence; skip check
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
            alive = e.code  # 401/403/405 still mean the service is online
            break
        except Exception:  # noqa: BLE001 — DNS/connection/timeout network-layer errors
            alive = 0
            if attempt == 1:
                # Retry once for transient network jitter during large batch checks so a
                # single timeout doesn't flag a working plugin as unreachable.
                time.sleep(1)
    _URL_CACHE[url] = alive
    return alive


def strip_version_spec(pkg):
    """Strip version suffix from package arg: pkg@1.2.3 -> pkg; @scope/name@1.2.3 -> @scope/name;
    pkg==1.2.3 (uvx/pip form) -> pkg."""
    if pkg.startswith("@"):
        head, _, _ver = pkg.rpartition("@")
        return head or pkg
    if "==" in pkg:
        return pkg.split("==", 1)[0]
    return pkg.split("@", 1)[0]


def first_npx_package(args):
    """Return the first non-flag token in npx args (or the value of -p/--package)."""
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
    """For `uv run <pkg>`, `uv tool run <pkg>`, or `uvx <pkg>`: first non-flag token after subcommand."""
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
    """Extract ./-prefixed relative path tokens from a command string (unquoted)."""
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
            warnings.append(f"MCP server {sname!r} points to a loopback address; user must run the service locally: {url}")
            return
        if online:
            status = url_alive(url)
            if status in (0, None):
                blockers.append(f"MCP server {sname!r} URL is unreachable: {url}")
            elif status == 404:
                blockers.append(f"MCP server {sname!r} URL returned 404: {url}")
            elif status in (401, 403):
                warnings.append(f"MCP server {sname!r} URL requires authentication (HTTP {status}): {url}")
        return
    command = cfg.get("command") or ""
    args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
    if PLACEHOLDER_RE.match(command) or any(PLACEHOLDER_RE.match(str(a)) for a in args):
        blockers.append(f"MCP server {sname!r} command is an unconfigured placeholder: {command}")
        return
    base = os.path.basename(command)
    if base.lower() in WINDOWS_ONLY_COMMANDS:
        blockers.append(f"MCP server {sname!r} command is Windows-only: {command}")
        return
    # Plugin-relative command path (normpath normalizes slashes; \\?\ paths skip slash conversion)
    if command.startswith("./") or ("/" in command and not os.path.isabs(command)):
        if not os.path.isfile(os.path.normpath(os.path.join(plugin_dir, command))):
            blockers.append(f"MCP server {sname!r} executable does not exist in plugin dir: {command}")
        return
    if base in INTERPRETERS_OK:
        pass  # Interpreter itself is fine; continue checking args
    elif base in INSTALLABLE_LAUNCHERS:
        if not shutil.which(base):
            warnings.append(f"MCP server {sname!r} requires installing {INSTALLABLE_LAUNCHERS[base]} first")
    else:
        # Uncommon bare command: check local PATH -> npm/PyPI (try both command and plugin name,
        # since package names often differ from bin names, e.g. npm package unforgit provides unforgit-mcp)
        if shutil.which(command):
            pass
        elif not online:
            warnings.append(f"MCP server {sname!r} command {command!r} not found locally; "
                            f"skipped online lookup (--offline)")
        else:
            plugin_name = os.path.basename(plugin_dir)
            npm_hit = next((c for c in (command, plugin_name) if npm_package_exists(c)), None)
            pypi_hit = next((c for c in (command, plugin_name) if pypi_package_exists(c)), None)
            if npm_hit:
                warnings.append(f"MCP server {sname!r} requires global npm package {npm_hit} "
                                f"(provides {command})")
            elif pypi_hit:
                warnings.append(f"MCP server {sname!r} requires PyPI package {pypi_hit} "
                                f"(provides {command})")
            else:
                blockers.append(f"MCP server {sname!r} command {command!r} has no verifiable public install path "
                                f"(not on PATH, npm, or PyPI; manual install from source repo/release required)")
    # Plugin-relative scripts in args
    for a in args:
        a = str(a)
        if a.startswith("./") and not os.path.exists(os.path.normpath(os.path.join(plugin_dir, a))):
            blockers.append(f"MCP server {sname!r} references missing path in plugin dir: {a}")
    # Verify npx/uvx/uv package names against registries
    if online and base == "npx":
        pkg = first_npx_package(args)
        if pkg and npm_package_exists(pkg) is False:
            blockers.append(f"MCP server {sname!r} npm package not found in registry: {pkg}")
    if online and base in ("uvx", "uv"):
        pkg = first_uv_package(args)
        if pkg and not pkg.startswith(".") and "/" not in pkg \
                and pypi_package_exists(pkg) is False:
            blockers.append(f"MCP server {sname!r} PyPI package not found: {pkg}")
    for k, v in (cfg.get("env") or {}).items():
        for var in ENV_VAR_RE.findall(str(v)):
            warnings.append(f"MCP server {sname!r} requires environment variable {var}")


def check_plugin(plugin_dir, online=True):
    name = os.path.basename(plugin_dir)
    blockers, warnings = [], []
    mf = os.path.join(plugin_dir, "kimi.plugin.json")
    if not os.path.isfile(mf):
        return {"name": name, "usable": False, "blockers": ["missing kimi.plugin.json"], "warnings": []}
    m = json.load(open(mf, encoding="utf-8"))

    for sname, cfg in (m.get("mcpServers") or {}).items():
        if isinstance(cfg, dict):
            check_mcp_server(plugin_dir, sname, cfg, online, blockers, warnings)

    for h in (m.get("hooks") or []):
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        for p in rel_paths_in_command(cmd):
            if not os.path.exists(os.path.normpath(os.path.join(plugin_dir, p))):
                blockers.append(f"hook ({h.get('event')}) references missing file in plugin dir: {p}")

    # Scan markdown bodies: skills/commands/agents + root SKILL.md
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
                # mcp__<server>__<tool>: prompt references an undeclared MCP server; the agent
                # will gracefully degrade at runtime, so warn that user setup may be needed.
                if server not in declared_servers and server != m.get("name"):
                    warnings.append(f"{rel} references undeclared MCP server mcp__{server}__* "
                                    f"(configure this server manually if required)")
        for tool in set(TOOL_MENTION_RE.findall(text)):
            if tool in seen_tool_notes:
                continue
            seen_tool_notes.add(tool)
            equiv = TOOL_NAME_EQUIVALENTS.get(tool)
            if equiv:
                warnings.append(f"{rel} mentions tool {tool} (Kimi equivalent is {equiv}; name difference is harmless)")
            else:
                warnings.append(f"{rel} mentions tool {tool} (no equivalent in Kimi built-in tools)")

    if os.path.isfile(os.path.join(plugin_dir, "package.json")):
        uses_local_node = any(
            (c.get("command") == "node" and any(str(a).startswith("./") for a in (c.get("args") or [])))
            for c in (m.get("mcpServers") or {}).values() if isinstance(c, dict))
        if uses_local_node and not os.path.isdir(os.path.join(plugin_dir, "node_modules")):
            warnings.append("contains package.json and local node MCP script; run npm install in plugin dir first")
    if os.path.isfile(os.path.join(plugin_dir, "setup.sh")):
        warnings.append("contains setup.sh; run it first to install plugin binaries/dependencies")

    return {"name": m.get("name", name), "usable": not blockers,
            "blockers": blockers, "warnings": sorted(set(warnings))}


def main():
    # Force UTF-8 stdio to avoid UnicodeEncodeError under Windows GBK locale with piped stdout
    pc.reconfigure_stdio_utf8()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    online = "--offline" not in sys.argv
    json_out = None
    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        json_out = sys.argv[i + 1]
    if not args:
        sys.exit("Usage: python3 check_plugin_deps.py <plugins-parent-dir-or-single-plugin-dir> [--offline] [--json <path>]")
    root = args[0]
    if os.path.isfile(os.path.join(root, "kimi.plugin.json")):
        dirs = [root]
    else:
        dirs = sorted(os.path.join(root, d) for d in os.listdir(root)
                      if os.path.isfile(os.path.join(root, d, "kimi.plugin.json")))
    results = [check_plugin(d, online=online) for d in dirs]
    usable = [r for r in results if r["usable"]]
    unusable = [r for r in results if not r["usable"]]
    print(f"Total {len(results)} plugins: {len(usable)} usable, {len(unusable)} unusable")
    for r in unusable:
        print(f"  [UNUSABLE] {r['name']}: " + "; ".join(r["blockers"]))
    if json_out:
        Path(json_out).write_text(json.dumps(
            {"total": len(results), "usable": len(usable), "unusable": len(unusable),
             "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON -> {json_out}")


if __name__ == "__main__":
    main()
