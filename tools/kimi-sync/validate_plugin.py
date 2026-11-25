#!/usr/bin/env python3
"""validate_plugin.py — Locally validate a Kimi plugin.
Checks kimi.plugin.json required fields, formats, component paths, icons, and TODOs.
No network or tokens required. Exits non-zero if any ERROR is found."""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

def load(path):
    mf = os.path.join(path, "kimi.plugin.json")
    if not os.path.isfile(mf):
        print(f"[ERROR] missing {mf}"); sys.exit(1)
    try:
        return json.load(open(mf, encoding="utf-8")), mf
    except Exception as e:
        print(f"[ERROR] kimi.plugin.json is not valid JSON: {e}"); sys.exit(1)

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
    # Force UTF-8 stdio to avoid UnicodeEncodeError under Windows GBK locale with piped stdout
    pc.reconfigure_stdio_utf8()
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 validate_plugin.py <plugin-dir>")
    root = sys.argv[1]
    m, mf = load(root)
    errs, warns = [], []

    name = m.get("name", "")
    if not name: errs.append("missing name")
    elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name): errs.append(f"name is not kebab-case: {name}")

    ver = m.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", ver):
        errs.append(f"version must be standard semver x.y.z (optional -prerelease or +local.timestamp): {ver!r}")

    itf = m.get("interface", {})
    if not isinstance(itf, dict):
        errs.append("interface must be an object"); itf = {}
    if not itf.get("displayName"): warns.append("interface.displayName is empty")
    if not itf.get("category"): errs.append("missing interface.category (plugin may be hidden in marketplace)")
    icon = itf.get("iconUrl", "")
    if icon and not re.match(r"https?://.+\.(png|jpg|jpeg|svg|webp|ico)(\?.*)?$", icon, re.I):
        warns.append(f"suspicious iconUrl format (direct image URL recommended): {icon}")
    # hostKind/platforms/non-spec fields: aligned with online schema and runtime
    SPEC_INTERFACE_FIELDS = ("displayName", "shortDescription", "longDescription", "developerName",
                             "websiteURL", "iconUrl", "category", "hostKind", "platforms",
                             "mcpOverrides")
    for f in itf:
        if f not in SPEC_INTERFACE_FIELDS:
            warns.append(f"interface.{f} is not in the schema spec (unrecognized by runtime/marketplace; consider removing)")
    host_kind = itf.get("hostKind")
    if host_kind is not None and host_kind not in ("hosted", "local"):
        errs.append(f"invalid interface.hostKind (must be hosted/local): {host_kind!r}")
    platforms = itf.get("platforms")
    if platforms is not None:
        VALID_PLATFORMS = ("macos", "windows", "linux", "ios", "android", "harmonyos")
        if not isinstance(platforms, list) or any(p not in VALID_PLATFORMS for p in platforms):
            errs.append(f"interface.platforms contains invalid platform (allowed: {'/'.join(VALID_PLATFORMS)}): {platforms!r}")

    # Personal marketplace icon convention: icon.png|jpg|jpeg|svg|webp in plugin root (first match, <= 256 KiB).
    # Either or both of local icon and iconUrl are valid; missing both falls back to the client default icon.
    for icon_file in ("icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp"):
        icon_path = os.path.join(root, icon_file)
        if os.path.isfile(icon_path):
            size = os.path.getsize(icon_path)
            if size > 256 * 1024:
                warns.append(f"{icon_file} exceeds 256 KiB ({size} bytes); personal marketplace will omit it")
            break

    for f in ("id", "entryPoints", "permissions", "tools", "apps", "inject", "configFile"):
        if f in m: warns.append(f"unsupported top-level field (ignored by client; consider removing): {f}")
    for f in ("displayName", "category", "iconUrl"):
        if f in m: warns.append(f"{f} should be placed under interface, not at top level")

    if "skills" in m:
        # skills officially accepts a single "./" path or an array of paths; report ERROR for other types
        raw = m["skills"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"skills must be a path string or array of strings: {raw!r}")
        else:
            for p in paths:
                # normpath normalizes trailing and forward slashes: \\?\ (Windows long paths) skip
                # slash conversion, so joining raw "/" field values directly would falsely report missing
                sd = os.path.normpath(os.path.join(root, p[2:] if p.startswith("./") else p))
                if not os.path.isdir(sd): errs.append(f"skills path does not exist: {sd}")
                else:
                    skill_mds = [os.path.join(sd, d, "SKILL.md") for d in os.listdir(sd)
                                 if os.path.isdir(os.path.join(sd, d))
                                 and os.path.isfile(os.path.join(sd, d, "SKILL.md"))]
                    if not skill_mds:
                        warns.append(f"no <name>/SKILL.md found under skills/: {sd}")
                    for smd in skill_mds:
                        # Directory-based SKILL.md requires frontmatter name + description to be loaded
                        head = open(smd, encoding="utf-8").read(65536)
                        fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                        if not fm:
                            errs.append(f"{smd} missing frontmatter (--- name/description ---); skill will not auto-trigger")
                        else:
                            body = fm.group(1)
                            if not re.search(r"^name:\s*\S+", body, re.M):
                                errs.append(f"{smd} frontmatter missing name")
                            if not re.search(r"^description:\s*\S+", body, re.M):
                                errs.append(f"{smd} frontmatter missing description (used by agent to decide when to load)")
    mcp = m.get("mcpServers")
    if mcp is not None and not isinstance(mcp, dict):
        errs.append("mcpServers must be an object ({name: {url|command}})")
    MCP_ALLOWED_KEYS = {"command", "args", "env", "cwd", "executor", "url", "headers", "auth",
                        "bearerTokenEnvVar", "transport", "enabledTools", "disabledTools",
                        "enabled", "startupTimeoutMs", "toolTimeoutMs"}
    for k, s in ((mcp or {}) if isinstance(mcp, dict) else {}).items():
        if not isinstance(s, dict):
            errs.append(f"mcpServers.{k} must be an object")
        else:
            extra = [sk for sk in s if sk not in MCP_ALLOWED_KEYS]
            if extra:
                warns.append(f"mcpServers.{k} contains non-spec keys (stripped by runtime): {', '.join(extra)}")
            if not (s.get("url") or s.get("command")):
                errs.append(f"mcpServers.{k} has neither url nor command")

    # ---- Path helper: agents/commands/systemPromptPath must start with "./" and stay within plugin root ----
    abs_root = os.path.realpath(root)
    def resolve_in_root(p):
        dest = os.path.realpath(os.path.join(abs_root, p))
        if dest != abs_root and not dest.startswith(abs_root + os.sep):
            return None
        return dest

    # ---- agents: string or string array of "./"-prefixed dirs, each with >= 1 .md file
    # having frontmatter name + description (missing fields prevent recognition -> ERROR) ----
    if "agents" in m:
        raw = m["agents"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"agents must be a path string or array of strings: {raw!r}")
        else:
            for p in paths:
                if not p.startswith("./"):
                    errs.append(f"agents path must start with ./: {p!r}"); continue
                ad = resolve_in_root(p)
                if ad is None:
                    errs.append(f"agents path escapes plugin directory: {p!r}"); continue
                if not os.path.isdir(ad):
                    errs.append(f"agents path does not exist or is not a directory: {ad}"); continue
                agent_mds = [os.path.join(ad, f) for f in os.listdir(ad)
                             if f.endswith(".md") and os.path.isfile(os.path.join(ad, f))]
                if not agent_mds:
                    errs.append(f"no .md files found under agents/: {ad}")
                for amd in agent_mds:
                    head = open(amd, encoding="utf-8").read(65536)
                    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                    if not fm:
                        errs.append(f"{amd} missing frontmatter (--- name/description ---); agent will not be recognized")
                    else:
                        body = fm.group(1)
                        if not re.search(r"^name:\s*\S+", body, re.M):
                            errs.append(f"{amd} frontmatter missing name")
                        if not re.search(r"^description:\s*\S+", body, re.M):
                            errs.append(f"{amd} frontmatter missing description")

    # ---- commands: string or string array of "./"-prefixed .md files or dirs
    # (dirs searched recursively for >= 1 .md); frontmatter name/description optional (defaults to path name) ----
    if "commands" in m:
        raw = m["commands"]
        paths = raw if isinstance(raw, list) else [raw]
        if not all(isinstance(p, str) for p in paths):
            errs.append(f"commands must be a path string or array of strings: {raw!r}")
        else:
            for p in paths:
                if not p.startswith("./"):
                    errs.append(f"commands path must start with ./: {p!r}"); continue
                cd = resolve_in_root(p)
                if cd is None:
                    errs.append(f"commands path escapes plugin directory: {p!r}"); continue
                cmd_mds = []
                if os.path.isfile(cd) and cd.endswith(".md"):
                    cmd_mds = [cd]
                elif os.path.isdir(cd):
                    for dirpath, _dirnames, filenames in os.walk(cd):
                        cmd_mds += [os.path.join(dirpath, f) for f in filenames if f.endswith(".md")]
                    if not cmd_mds:
                        errs.append(f"no .md files found under commands directory: {cd}")
                else:
                    errs.append(f"commands path does not exist or is not a .md file/directory: {cd}")
                for cmd_md in cmd_mds:
                    head = open(cmd_md, encoding="utf-8").read(65536)
                    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                    body = fm.group(1) if fm else ""
                    if not fm or not (re.search(r"^name:\s*\S+", body, re.M)
                                      and re.search(r"^description:\s*\S+", body, re.M)):
                        warns.append(f"{cmd_md} frontmatter missing name/description (path name will be used)")

    # ---- hooks: array of {event, command, timeout?} objects; event must be one of 16 enum values,
    # command non-empty string, optional timeout number in 1-600 (HookDefSchema) ----
    HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
                   "PermissionResult", "UserPromptSubmit", "Stop", "StopFailure", "Interrupt",
                   "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop",
                   "PreCompact", "PostCompact", "Notification")
    if "hooks" in m:
        raw = m["hooks"]
        if not isinstance(raw, list):
            errs.append(f"hooks must be an array: {raw!r}")
        else:
            for i, h in enumerate(raw):
                if not isinstance(h, dict):
                    errs.append(f"hooks[{i}] must be an object: {h!r}"); continue
                if h.get("event") not in HOOK_EVENTS:
                    errs.append(f"invalid hooks[{i}].event (allowed: {'/'.join(HOOK_EVENTS)}): {h.get('event')!r}")
                extra = [k for k in h if k not in ("event", "command", "matcher", "timeout")]
                if extra:
                    warns.append(f"hooks[{i}] contains non-spec keys (runtime strict schema will reject entry; remove): {', '.join(extra)}")
                if not (isinstance(h.get("command"), str) and h["command"].strip()):
                    errs.append(f"hooks[{i}].command must be a non-empty string")
                if "timeout" in h and not (isinstance(h["timeout"], (int, float))
                                           and not isinstance(h["timeout"], bool)
                                           and 1 <= h["timeout"] <= 600):
                    errs.append(f"hooks[{i}].timeout must be a number in 1-600: {h.get('timeout')!r}")
            if isinstance(raw, list) and raw:
                warns.append("hooks automatically execute commands on session events; review and confirm with user before registering")

    # ---- sessionStart: {skill: <name>}; skill name must resolve within plugin skills ----
    if "sessionStart" in m:
        raw = m["sessionStart"]
        if not isinstance(raw, dict):
            errs.append(f"sessionStart must be an object: {raw!r}")
        else:
            skill = raw.get("skill")
            if not (isinstance(skill, str) and skill.strip()):
                errs.append("sessionStart.skill must be a non-empty string")
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
                    # Root SKILL.md fallback (when skills is omitted or "./", skill name may come from root SKILL.md)
                    root_skill_md = os.path.join(abs_root, "SKILL.md")
                    if os.path.isfile(root_skill_md):
                        head = open(root_skill_md, encoding="utf-8").read(65536)
                        fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
                        if fm and re.search(rf"^name:\s*{re.escape(skill)}\s*$", fm.group(1), re.M):
                            found = True
                if not found:
                    errs.append(f"sessionStart.skill not found in plugin skills: {skill}")

    # ---- systemPrompt / systemPromptPath: injected system prompt text, max 32 KB ----
    if "systemPrompt" in m:
        sp = m["systemPrompt"]
        if not isinstance(sp, str):
            errs.append("systemPrompt must be a string")
        elif len(sp.encode("utf-8")) > 32 * 1024:
            errs.append(f"systemPrompt exceeds 32 KB ({len(sp.encode('utf-8'))} bytes) and will be ignored")
    if "systemPromptPath" in m:
        spp = m["systemPromptPath"]
        if not (isinstance(spp, str) and spp.strip()):
            errs.append("systemPromptPath must be a non-empty string")
        elif not spp.startswith("./"):
            errs.append(f"systemPromptPath must start with ./: {spp!r}")
        else:
            sp_file = resolve_in_root(spp)
            if sp_file is None:
                errs.append(f"systemPromptPath escapes plugin directory: {spp!r}")
            elif not os.path.isfile(sp_file):
                errs.append(f"systemPromptPath file does not exist: {sp_file}")
            elif os.path.getsize(sp_file) > 32 * 1024:
                errs.append(f"systemPromptPath exceeds 32 KB ({os.path.getsize(sp_file)} bytes) and will be ignored: {sp_file}")

    todos = walk_todo(m)
    if todos: errs.append("manifest contains TODO placeholders: " + ", ".join(todos))

    for e in errs: print(f"[ERROR] {e}")
    for w in warns: print(f"[WARN]  {w}")
    if not errs and not warns: print("OK: no issues")
    print(f"=> {'FAIL' if errs else 'OK'}  ({len(errs)} error, {len(warns)} warning)")
    sys.exit(1 if errs else 0)

if __name__ == "__main__":
    main()
