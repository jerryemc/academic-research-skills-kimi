#!/usr/bin/env python3
"""validate_plugin.py — 本地校验一个 Kimi 插件。
检查 kimi.plugin.json 的必填/格式/组件路径/图标/TODO。无网络、无 token。
有 ERROR 时退出码非 0。"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

def load(path):
    mf = os.path.join(path, "kimi.plugin.json")
    if not os.path.isfile(mf):
        print(f"[ERROR] 找不到 {mf}"); sys.exit(1)
    try:
        return json.load(open(mf, encoding="utf-8")), mf
    except Exception as e:
        print(f"[ERROR] kimi.plugin.json 不是合法 JSON: {e}"); sys.exit(1)

def walk_todo(o, path=""):
    hits = []
    if isinstance(o, str):
        if "TODO" in o: hits.append(path)
    elif isinstance(o, dict):
        for k, v in o.items(): hits += walk_todo(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o): hits += walk_todo(v, f"{path}[{i}]")
    return hits

def main():
    # 中文输出钉死 UTF-8：Windows GBK locale + 管道捕获下 print 会 UnicodeEncodeError
    pc.reconfigure_stdio_utf8()
    if len(sys.argv) < 2:
        sys.exit("用法: python3 validate_plugin.py <plugin-dir>")
    root = sys.argv[1]
    m, mf = load(root)
    errs, warns = [], []

    name = m.get("name", "")
    if not name: errs.append("缺 name")
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name): errs.append(f"name 非 kebab-case: {name}")

    ver = m.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", ver):
        errs.append(f"version 需为标准 semver x.y.z（可带 -prerelease 或 +local.时间戳）: {ver!r}")

    itf = m.get("interface", {})
    if not isinstance(itf, dict):
        errs.append("interface 必须是对象"); itf = {}
    if not itf.get("displayName"): warns.append("interface.displayName 空")
    if not itf.get("category"): errs.append("interface.category 缺（缺了插件在市场可能不可见）")
    icon = itf.get("iconUrl", "")
    if icon and not re.match(r"https?://.+\.(png|jpg|jpeg|svg|webp|ico)(\?.*)?$", icon, re.I):
        warns.append(f"iconUrl 格式可疑（建议直链图片）: {icon}")
    # hostKind/platforms/规范外字段：对齐线上 schema 与 runtime
    SPEC_INTERFACE_FIELDS = ("displayName", "shortDescription", "longDescription", "developerName",
                             "websiteURL", "iconUrl", "category", "hostKind", "platforms",
                             "mcpOverrides")
    for f in itf:
        if f not in SPEC_INTERFACE_FIELDS:
            warns.append(f"interface.{f} 不在字段规范内（runtime/市场不认识，建议删除）")
    host_kind = itf.get("hostKind")
    if host_kind is not None and host_kind not in ("hosted", "local"):
        errs.append(f"interface.hostKind 非法（限 hosted/local）: {host_kind!r}")
    platforms = itf.get("platforms")
    if platforms is not None:
        VALID_PLATFORMS = ("macos", "windows", "linux", "ios", "android", "harmonyos")
        if not isinstance(platforms, list) or any(p not in VALID_PLATFORMS for p in platforms):
            errs.append(f"interface.platforms 含非法平台（限 {'/'.join(VALID_PLATFORMS)}）: {platforms!r}")

    # 个人市场图标约定：插件根目录 icon.png|jpg|jpeg|svg|webp（首个匹配，<= 256 KiB）。
    # 与 iconUrl 二选一或共存均可；两者都缺时不报错（客户端默认图标兜底）。
    for icon_file in ("icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp"):
        icon_path = os.path.join(root, icon_file)
        if os.path.isfile(icon_path):
            size = os.path.getsize(icon_path)
            if size > 256 * 1024:
                warns.append(f"{icon_file} 超过 256 KiB（{size} bytes），个人市场将省略该图标")
            break

    for f in ("id", "entryPoints", "permissions", "tools", "apps", "inject", "configFile"):
        if f in m: warns.append(f"不支持的顶层字段（客户端会忽略，建议删除）: {f}")
    for f in ("displayName", "category", "iconUrl"):
        if f in m: warns.append(f"{f} 应放在 interface 下，不是顶层字段")

    if "skills" in m:
        # skills 官方支持单个 "./" 路径或路径数组；其他类型报 ERROR 而不是崩溃
        raw = m["skills"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"skills 必须是路径字符串或字符串数组: {raw!r}")
        else:
            for p in paths:
                # normpath 归一尾斜杠与正斜杠：\\?\ 前缀路径（Windows 长路径）不做
                # 斜杠转换，直接 join 带 "/" 的字段值会误判不存在
                sd = os.path.normpath(os.path.join(root, p[2:] if p.startswith("./") else p))
                if not os.path.isdir(sd): errs.append(f"skills 路径不存在: {sd}")
                else:
                    skill_mds = [os.path.join(sd, d, "SKILL.md") for d in os.listdir(sd)
                                 if os.path.isdir(os.path.join(sd, d))
                                 and os.path.isfile(os.path.join(sd, d, "SKILL.md"))]
                    if not skill_mds:
                        warns.append(f"skills/ 下没找到任何 <名>/SKILL.md: {sd}")
                    for smd in skill_mds:
                        # 目录型 SKILL.md 必须有 frontmatter name + description，否则技能不会被加载
                        head = open(smd, encoding="utf-8").read(65536)
                        fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                        if not fm:
                            errs.append(f"{smd} 缺 frontmatter（--- name/description ---），技能不会被自动触发")
                        else:
                            body = fm.group(1)
                            if not re.search(r"^name:\s*\S+", body, re.M):
                                errs.append(f"{smd} frontmatter 缺 name")
                            if not re.search(r"^description:\s*\S+", body, re.M):
                                errs.append(f"{smd} frontmatter 缺 description（agent 靠它决定何时加载技能）")
    mcp = m.get("mcpServers")
    if mcp is not None and not isinstance(mcp, dict):
        errs.append("mcpServers 必须是对象（{名称: {url|command}}）")
    MCP_ALLOWED_KEYS = {"command", "args", "env", "cwd", "executor", "url", "headers", "auth",
                        "bearerTokenEnvVar", "transport", "enabledTools", "disabledTools",
                        "enabled", "startupTimeoutMs", "toolTimeoutMs"}
    for k, s in ((mcp or {}) if isinstance(mcp, dict) else {}).items():
        if not isinstance(s, dict):
            errs.append(f"mcpServers.{k} 必须是对象")
        else:
            extra = [sk for sk in s if sk not in MCP_ALLOWED_KEYS]
            if extra:
                warns.append(f"mcpServers.{k} 含规范外键（runtime 会剥掉）: {', '.join(extra)}")
            if not (s.get("url") or s.get("command")):
                errs.append(f"mcpServers.{k} 既无 url 也无 command")

    # ---- 路径工具：原始 JSON 里 agents/commands/systemPromptPath 都必须 "./" 开头且解析后不越出插件目录 ----
    abs_root = os.path.realpath(root)
    def resolve_in_root(p):
        dest = os.path.realpath(os.path.join(abs_root, p))
        if dest != abs_root and not dest.startswith(abs_root + os.sep):
            return None
        return dest

    # ---- agents：字符串或字符串数组，每个是 "./" 开头的目录，目录下至少一个 .md，
    # 且每个 .md 必须有 frontmatter name + description（缺了 agent 不会被识别，报 ERROR）----
    if "agents" in m:
        raw = m["agents"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"agents 必须是路径字符串或字符串数组: {raw!r}")
        else:
            for p in paths:
                if not p.startswith("./"):
                    errs.append(f"agents 路径必须 ./ 开头: {p!r}"); continue
                ad = resolve_in_root(p)
                if ad is None:
                    errs.append(f"agents 路径越出插件目录: {p!r}"); continue
                if not os.path.isdir(ad):
                    errs.append(f"agents 路径不存在或不是目录: {ad}"); continue
                agent_mds = [os.path.join(ad, f) for f in os.listdir(ad)
                             if f.endswith(".md") and os.path.isfile(os.path.join(ad, f))]
                if not agent_mds:
                    errs.append(f"agents/ 下没找到任何 .md 文件: {ad}")
                for amd in agent_mds:
                    head = open(amd, encoding="utf-8").read(65536)
                    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                    if not fm:
                        errs.append(f"{amd} 缺 frontmatter（--- name/description ---），agent 不会被识别")
                    else:
                        body = fm.group(1)
                        if not re.search(r"^name:\s*\S+", body, re.M):
                            errs.append(f"{amd} frontmatter 缺 name")
                        if not re.search(r"^description:\s*\S+", body, re.M):
                            errs.append(f"{amd} frontmatter 缺 description")

    # ---- commands：字符串或字符串数组，每个是 "./" 开头的 .md 文件或目录
    # （目录递归找 .md，至少一个）；frontmatter name/description 可选，缺了用路径名 ----
    if "commands" in m:
        raw = m["commands"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"commands 必须是路径字符串或字符串数组: {raw!r}")
        else:
            for p in paths:
                if not p.startswith("./"):
                    errs.append(f"commands 路径必须 ./ 开头: {p!r}"); continue
                cd = resolve_in_root(p)
                if cd is None:
                    errs.append(f"commands 路径越出插件目录: {p!r}"); continue
                cmd_mds = []
                if os.path.isfile(cd) and cd.endswith(".md"):
                    cmd_mds = [cd]
                elif os.path.isdir(cd):
                    for dirpath, _dirnames, filenames in os.walk(cd):
                        cmd_mds += [os.path.join(dirpath, f) for f in filenames if f.endswith(".md")]
                    if not cmd_mds:
                        errs.append(f"commands 目录下没找到任何 .md 文件: {cd}")
                else:
                    errs.append(f"commands 路径不存在、或不是 .md 文件/目录: {cd}")
                for cmd_md in cmd_mds:
                    head = open(cmd_md, encoding="utf-8").read(65536)
                    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                    body = fm.group(1) if fm else ""
                    if not fm or not (re.search(r"^name:\s*\S+", body, re.M)
                                      and re.search(r"^description:\s*\S+", body, re.M)):
                        warns.append(f"{cmd_md} frontmatter 缺 name/description（将用路径名作为命令名）")

    # ---- hooks：{event, command, timeout?} 对象数组；event 限 16 种枚举，command 非空字符串，
    # timeout 若存在必须是 1-600 的数字（HookDefSchema）----
    HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
                   "PermissionResult", "UserPromptSubmit", "Stop", "StopFailure", "Interrupt",
                   "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop",
                   "PreCompact", "PostCompact", "Notification")
    if "hooks" in m:
        raw = m["hooks"]
        if not isinstance(raw, list):
            errs.append(f"hooks 必须是数组: {raw!r}")
        else:
            for i, h in enumerate(raw):
                if not isinstance(h, dict):
                    errs.append(f"hooks[{i}] 必须是对象: {h!r}"); continue
                if h.get("event") not in HOOK_EVENTS:
                    errs.append(f"hooks[{i}].event 非法（限 {'/'.join(HOOK_EVENTS)}）: {h.get('event')!r}")
                extra = [k for k in h if k not in ("event", "command", "matcher", "timeout")]
                if extra:
                    warns.append(f"hooks[{i}] 含规范外键（runtime strict schema 会整条作废，建议删除）: {', '.join(extra)}")
                if not (isinstance(h.get("command"), str) and h["command"].strip()):
                    errs.append(f"hooks[{i}].command 必须是非空字符串")
                if "timeout" in h and not (isinstance(h["timeout"], (int, float))
                                           and not isinstance(h["timeout"], bool)
                                           and 1 <= h["timeout"] <= 600):
                    errs.append(f"hooks[{i}].timeout 必须是 1-600 的数字: {h.get('timeout')!r}")
            if isinstance(raw, list) and raw:
                warns.append("hooks 会在会话事件时自动执行插件内的命令，登记前应向用户展示并获得确认")

    # ---- sessionStart：{skill: <名>}；skill 名必须能在插件的 skills 里找到 ----
    if "sessionStart" in m:
        raw = m["sessionStart"]
        if not isinstance(raw, dict):
            errs.append(f"sessionStart 必须是对象: {raw!r}")
        else:
            skill = raw.get("skill")
            if not (isinstance(skill, str) and skill.strip()):
                errs.append("sessionStart.skill 必须是非空字符串")
            else:
                skill = skill.strip()
                found = False
                raw_skills = m.get("skills")
                skill_dirs = raw_skills if isinstance(raw_skills, list) else [raw_skills]
                for sd in (d for d in skill_dirs if isinstance(d, str)):
                    sd_abs = resolve_in_root(sd) if sd.startswith("./") else None
                    if sd_abs and os.path.isfile(os.path.join(sd_abs, skill, "SKILL.md")):
                        found = True; break
                if not found:
                    # 根 SKILL.md fallback（skills 未声明或声明了 "./" 时 skill 名也可来自根 SKILL.md）
                    root_skill_md = os.path.join(abs_root, "SKILL.md")
                    if os.path.isfile(root_skill_md):
                        head = open(root_skill_md, encoding="utf-8").read(65536)
                        fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                        if fm and re.search(rf"^name:\s*{re.escape(skill)}\s*$", fm.group(1), re.M):
                            found = True
                if not found:
                    errs.append(f"sessionStart.skill 在插件 skills 里找不到: {skill}")

    # ---- systemPrompt / systemPromptPath：注入系统提示的文本，上限 32 KB ----
    if "systemPrompt" in m:
        sp = m["systemPrompt"]
        if not isinstance(sp, str):
            errs.append("systemPrompt 必须是字符串")
        elif len(sp.encode("utf-8")) > 32 * 1024:
            errs.append(f"systemPrompt 超过 32 KB（{len(sp.encode('utf-8'))} bytes），会被忽略")
    if "systemPromptPath" in m:
        spp = m["systemPromptPath"]
        if not (isinstance(spp, str) and spp.strip()):
            errs.append("systemPromptPath 必须是非空字符串")
        elif not spp.startswith("./"):
            errs.append(f"systemPromptPath 必须 ./ 开头: {spp!r}")
        else:
            sp_file = resolve_in_root(spp)
            if sp_file is None:
                errs.append(f"systemPromptPath 越出插件目录: {spp!r}")
            elif not os.path.isfile(sp_file):
                errs.append(f"systemPromptPath 指向的文件不存在: {sp_file}")
            elif os.path.getsize(sp_file) > 32 * 1024:
                errs.append(f"systemPromptPath 超过 32 KB（{os.path.getsize(sp_file)} bytes），会被忽略: {sp_file}")

    todos = walk_todo(m)
    if todos: errs.append("manifest 含 TODO 占位: " + ", ".join(todos))

    for e in errs: print(f"[ERROR] {e}")
    for w in warns: print(f"[WARN]  {w}")
    if not errs and not warns: print("OK: no issues")
    print(f"=> {'FAIL' if errs else 'OK'}  ({len(errs)} error, {len(warns)} warning)")
    sys.exit(1 if errs else 0)

if __name__ == "__main__":
    main()
