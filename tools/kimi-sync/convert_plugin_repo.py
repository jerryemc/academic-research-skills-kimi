#!/usr/bin/env python3
"""convert_plugin_repo.py — Batch-convert a plugin/skill repository (in any source dialect) into this project's (Kimi) plugin format.

Input: GitHub repository URL (https://github.com/<owner>/<repo>; also supports git@ form,
  protocol-less github.com/<o>/<r>, and <owner>/<repo> shorthand), generic git hosting URL
  (gitee.com is structurally isomorphic to GitHub and supports all forms; cnb.cool / atomgit.com /
  gitlab.com etc. support repo root URLs via git clone, without codeload fallback or tree/blob
  subpaths), or a local directory. Also supports tree/blob/blame subpath URLs:
  - https://github.com/<o>/<r>/tree/<ref>/<path...> -> clone the repo (checkout ref),
    treat <path> as a single plugin directory input, and convert only that plugin;
  - https://github.com/<o>/<r>/blob/<ref>/<path...> -> treat the file as a strict entry point
    (index file or manifest); relative paths resolve from that file's directory (falling back to
    repo root if not found), without scanning other configs in the repo.
  When the URL specifies a ref, it is fetched strictly at that ref; failure reports that the ref
  may not exist without silently falling back to the default branch.
  If a branch name contains '/', the URL cannot distinguish the branch name from the path boundary;
  use a commit SHA or a tag URL without '/'.
  Non-code repository pages (pull/issues/releases/commit/raw/archive, etc.),
  raw.githubusercontent.com, gist, and multi-level paths on non-GitHub hosts are rejected with
  rewrite hints rather than silently treated as a whole repo (see classify_source).
Automatic repository layout detection:
  A. Single-plugin repo: repo root (or a subdirectory) has a recognized plugin manifest.
  B. Multi-plugin monorepo: multiple subdirectories each have a manifest (e.g. kreuzberg-dev/plugins).
  C. Index repo: root has plugins.json ({"plugins": [...]} list) or a marketplace index
     (.agents/plugins/marketplace.json for Codex marketplace, .claude-plugin/marketplace.json
     for Claude marketplace); entries use url for external GitHub repos or path/source for
     in-repo (or local) relative directories; plugin contents may be mirrored under
     plugins/<owner>/<repo>/ in this repo, falling back to fetching from url if the mirror is
     incomplete. Official Claude marketplace external sources {"source": "url", "url", "sha"/"ref"}
     are fetched at the pinned commit; git-subdir sources use the in-repo subdirectory as the
     plugin directory; skill-collection entries {"source": "./", "skills": [...]} assemble a
     skill-only plugin from the array (multiple entries with the same source are not merged);
     when an entry path is missing, it falls back to a unique same-name manifest match in the
     repo (never guessing if names do not match).
     DeepSeek registry (registry-snapshot.json at root or any subdirectory such as data/): entries
     are fetched via url, multilingual description objects are flattened with zh preferred;
     cordis detection — declarative subsets with a recognized manifest or root SKILL.md/skills/
     are convertible, while pure-code cordis packages record a clear failure reason.
     When multiple index candidates coexist, selection is never silent: candidate list and conflict
     warning are printed, priority plugins.json > .agents/plugins/marketplace.json >
     .claude-plugin/marketplace.json is applied, and the report records which was selected and
     which were ignored. When no root index exists, marketplace.json and registry-snapshot.json
     in subdirectories are discovered recursively and expanded in index mode.
  D. Repo with no manifest but with SKILL.md or skills/ at root: whole repo is wrapped as a
     skill-only plugin.

Plugin manifest detection conventions (by priority; Codex is only one supported source dialect):
  1. kimi.plugin.json                -- Native project format: light conversion (normalize name/version/category and keep as-is)
  2. .kimi-plugin/plugin.json        -- Native project format (dot-dir form): light conversion
  3. .codex-plugin/plugin.json       -- Codex convention: full dialect conversion
  4. .claude-plugin/plugin.json      -- Claude convention: full dialect conversion
  5. .codebuddy-plugin/plugin.json   -- CodeBuddy convention (similar to Claude): full dialect conversion
  6. .cursor-plugin/plugin.json      -- Cursor convention (similar to Claude): full dialect conversion
  7. gemini-extension.json           -- Gemini extension convention: mcpServers isomorphic (${extensionPath}
                                        rewritten as plugin-root placeholder), contextFileName mapped to systemPromptPath
  8. server.json                     -- Official MCP registry convention: packages (npm/pypi, stdio) ->
                                        npx/uvx server, remotes -> url server, wrapped as mcpServers-only plugin
  9. plugin.json (at plugin root)    -- Generic convention: treated as Codex dialect if it has interface, else simple dialect
  When multiple platform definitions (kimi/codex/claude/codebuddy/gemini/serverjson) coexist in the
  same plugin directory, the first dialect in MANIFEST_CANDIDATES priority order is automatically
  selected and skipped dialects are recorded in report notes (visible and traceable without
  interrupting batch conversion). Single-platform plugins are used directly.

Conversion rules (non-Kimi dialect -> kimi.plugin.json):
  - Manifest is written to plugin root kimi.plugin.json (Kimi parser reads root manifest; see agent-core plugin/manifest.ts).
  - name is normalized to kebab-case; invalid version falls back to 0.1.0; duplicate names get -2/-3 suffixes.
  - interface keeps only Kimi-recognized fields (displayName/shortDescription/longDescription/
    developerName/websiteURL/iconUrl/category); source-dialect-specific fields (capabilities/defaultPrompt/
    brandColor/screenshots/composerIcon, etc.) are dropped and logged in report notes.
  - Free-text interface.category is mapped to Kimi enum PRODUCTIVITY/DEVELOPER/FINANCE/
    LIFESTYLE_HEALTH/SYSTEM (see CATEGORY_MAP).
  - mcpServers: source dialects often write a path string pointing to .mcp.json ("./.mcp.json"),
    whereas Kimi requires an inline object -- the file is read and inlined; supports mcpServers /
    mcp_servers / bare-dict forms. ${PLUGIN_ROOT} / $PLUGIN_ROOT / __REPO_ROOT__ in command are
    rewritten to "./" (Kimi resolves ./ command/cwd relative to plugin root); matching placeholders
    in args are replaced with "." and cwd "./" is added; absolute-path commands are kept by PATH
    command name (basename, since Kimi does not accept absolute paths), and whether they exist on
    PATH or have a public install path is verified by dependency check (unusable ones go to the
    hold bucket rather than being dropped during conversion).
  - hooks: source dialects often write a path string pointing to hooks.json in Claude style
    {"hooks": {Event: [{matcher, hooks: [{type: command, command, timeout, statusMessage}]}]}},
    whereas Kimi requires inline [{event, command, timeout?}] -- flattened during conversion; only
    the 16 Kimi-supported events (HookDefSchema) are kept, unsupported ones are dropped with notes;
    non-schema fields like statusMessage are dropped.
  - commands / agents: paths are uniformly prefixed with "./"; empty arrays omit the field.
  - The plugin directory is copied as a whole (excluding .git/.github/source manifest dirs/node_modules)
    so referenced files (skills/, hooks/, mcp sources, etc.) are included; shared repo-level sources
    referenced via __REPO_ROOT__ in monorepo subdirectory plugins are also copied in.

Outputs (grouped by market; market resolution is independent of input entry: --market flag >
  top-level name in source index config (root plugins.json / marketplace.json, including
  subdirectory-discovered marketplace.json; or the blob index file's name) > personal -- single-plugin
  inputs (tree subpath / blob manifest / repos without index) have no index and fall back to personal):
  <output-dir>/<market>/<name>/           One directory per successfully converted plugin (conforming to project plugin spec)
  <output-dir>/<market>/plugin-builder-report.md   Single human-readable report: conversion details + registration results
                                                   (registration results filled back by register_converted.py)
  <output-dir>/<market>/conversion-report.json     Machine contract: conversion data + registration_plan
  <output-dir>/<market>/marketplace.orig           Full source marketplace manifest (static snapshot)

When the market output directory already exists (including empty dirs), conversion refuses by
default so existing reports or plugin sources are not overwritten; use --output-dir for a new
output root, or --force after user confirmation to overwrite old reports and same-named sources.

After conversion, each plugin runs validate_plugin.py (0 structural errors) and then
check_plugin_deps.py (dependency availability); only passing both counts as "truly usable".
Fully local pipeline, zero tokens.
work_dir (git fetch cache) is created fresh per run and auto-cleaned on exit (including errors);
an explicitly specified --work-dir is not cleaned up.
"""

import argparse, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, urllib.parse, urllib.request
import concurrent.futures
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc
import check_plugin_deps as cpd
import source_guard as sg
import rights_scan as rs

# Plugin manifest detection conventions (by priority): (path relative to plugin dir, source dialect)
# kimi = native project format (light conversion); codex/claude/generic = source dialects (full conversion)
MANIFEST_CANDIDATES = [
    ("kimi.plugin.json", "kimi"),
    (os.path.join(".kimi-plugin", "plugin.json"), "kimi"),
    (os.path.join(".codex-plugin", "plugin.json"), "codex"),
    (os.path.join(".claude-plugin", "plugin.json"), "claude"),
    (os.path.join(".codebuddy-plugin", "plugin.json"), "codebuddy"),
    (os.path.join(".cursor-plugin", "plugin.json"), "cursor"),
    ("gemini-extension.json", "gemini"),
    ("server.json", "serverjson"),
    ("plugin.json", "generic"),
]

# Platform dialects participating in multi-platform dialect detection (generic root plugin.json
# does not count as a platform definition: when coexisting with a platform manifest, priority above
# applies without triggering selection confirmation)
PLATFORM_DIALECTS = ("kimi", "codex", "claude", "codebuddy", "cursor", "gemini", "serverjson")


def detect_manifest(plugin_dir):
    """Detect plugin manifest in directory by priority; returns (rel_path, dialect) or (None, None)."""
    plugin_dir = pc.winlong(plugin_dir)
    for rel, dialect in MANIFEST_CANDIDATES:
        if os.path.isfile(os.path.join(plugin_dir, rel)):
            return rel, dialect
    return None, None


def detect_all_manifests(plugin_dir):
    """All candidate manifests present in directory as [(rel_path, dialect)] in MANIFEST_CANDIDATES priority order."""
    plugin_dir = pc.winlong(plugin_dir)
    return [(rel, d) for rel, d in MANIFEST_CANDIDATES
            if os.path.isfile(os.path.join(plugin_dir, rel))]

# Hook events supported by Kimi manifest.ts (HookDefSchema enum, consistent with validate_plugin.py)
KIMI_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
                    "PermissionResult", "UserPromptSubmit", "Stop", "StopFailure", "Interrupt",
                    "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop",
                    "PreCompact", "PostCompact", "Notification")

# Free-text interface.category -> Kimi enum (defaults to PRODUCTIVITY)
CATEGORY_MAP = {
    "developer tools": "DEVELOPER", "coding": "DEVELOPER", "development": "DEVELOPER",
    "development & workflow": "DEVELOPER", "development & code tools": "DEVELOPER",
    "devops": "DEVELOPER", "engineering": "DEVELOPER", "code-quality": "DEVELOPER",
    "observability": "DEVELOPER", "harness": "DEVELOPER", "orchestration": "DEVELOPER",
    "security": "DEVELOPER", "web-scraping": "DEVELOPER", "document-intelligence": "DEVELOPER",
    "agent-reliability": "DEVELOPER", "cloud-services": "DEVELOPER",
    "data & analytics": "DEVELOPER", "data": "DEVELOPER", "performance": "DEVELOPER",
    "developer": "DEVELOPER",
    "commerce": "FINANCE", "finance": "FINANCE",
    "lifestyle": "LIFESTYLE_HEALTH", "health": "LIFESTYLE_HEALTH", "news": "LIFESTYLE_HEALTH",
    "content creation": "LIFESTYLE_HEALTH", "creativity": "LIFESTYLE_HEALTH",
    "design": "LIFESTYLE_HEALTH",
    "system": "SYSTEM",
}

# Common interpreters/launchers (used to identify interpreter context preceding a path token)
PATH_COMMAND_BASENAMES = {"python3", "python", "node", "npx", "uvx", "uv", "deno", "bun",
                          "bash", "sh", "env", "docker", "ruby", "perl", "php", "go", "java"}

# Directories/files excluded when copying plugin body (source dialect manifest dirs are appended
# by copy_excludes per dialect, replaced by root kimi.plugin.json after conversion)
COPY_EXCLUDE_DIRS = {".git", ".github", "node_modules", "__pycache__"}
DIALECT_MANIFEST_DIRS = {"kimi": ".kimi-plugin", "codex": ".codex-plugin",
                         "claude": ".claude-plugin", "codebuddy": ".codebuddy-plugin",
                         "cursor": ".cursor-plugin"}


def copy_excludes(dialect):
    """Additional source manifest dirs/files to exclude when copying plugin body: non-kimi dialects
    uniformly exclude all platform manifest dirs plus gemini-extension.json and server.json (output
    is governed by root kimi.plugin.json; other platform manifests are not carried over)."""
    if dialect == "kimi":
        return set(COPY_EXCLUDE_DIRS)
    return set(COPY_EXCLUDE_DIRS) | set(DIALECT_MANIFEST_DIRS.values()) | {"gemini-extension.json", "server.json"}

# Source-dialect-specific fields unrecognized by Kimi (dropped during conversion with notes)
CODEX_ONLY_TOP_FIELDS = ("apps", "userConfig", "scripts", "displayName", "privacyPolicyURL",
                         "termsOfServiceURL", "websiteURL", "composerIcon", "publisher", "type",
                         "commit", "verified", "entry", "id", "bundle", "logo")
CODEX_ONLY_INTERFACE_FIELDS = ("capabilities", "defaultPrompt", "composerIcon", "brandColor",
                               "screenshots", "privacyPolicyURL", "termsOfServiceURL", "logo",
                               "type", "logoDark", "logoURL", "logoLarge", "logoSmall",
                               "logoMedium", "icon", "thumbnail", "links")

PLUGIN_ROOT_TOKENS = ("${PLUGIN_ROOT}", "$PLUGIN_ROOT", "__REPO_ROOT__", "${extensionPath}")

# ---- kimi.plugin.json field specification (authoritative: agent-core plugin/manifest.ts +
# config/schema.ts zod schema + https://catalog.msh.team/misc/kimi.plugin.schema.json + personal
# market registration gate daimon plugin-market/service.ts). Output manifests may only contain these fields. ----
SPEC_TOP_FIELDS = ("$schema", "name", "version", "description", "keywords", "author",
                   "homepage", "license", "skills", "agents", "sessionStart", "mcpServers",
                   "hooks", "commands", "interface", "skillInstructions", "systemPrompt",
                   "systemPromptPath")
# interface: runtime reads the first 5; iconUrl/category/hostKind/platforms/mcpOverrides are market/schema fields
SPEC_INTERFACE_FIELDS = ("displayName", "shortDescription", "longDescription", "developerName",
                         "websiteURL", "iconUrl", "category", "hostKind", "platforms",
                         "mcpOverrides")
SPEC_HOST_KINDS = ("hosted", "local")
SPEC_PLATFORMS = ("macos", "windows", "linux", "ios", "android", "harmonyos")
# mcpServers entry (zod McpServerConfig): valid keys for stdio / remote respectively
MCP_COMMON_KEYS = ("enabledTools", "disabledTools", "enabled", "startupTimeoutMs", "toolTimeoutMs")
MCP_STDIO_KEYS = ("command", "args", "env", "cwd", "executor")
MCP_REMOTE_KEYS = ("url", "headers", "auth", "bearerTokenEnvVar", "transport")
# Upper limit for systemPrompt / systemPromptPath (runtime ignores values exceeding this limit)
SYSTEM_PROMPT_MAX_BYTES = 32 * 1024


# ---------- Repository acquisition ----------
# Leading path segments of non-code repo pages: these URLs point to PR/issue/release/commit pages
# rather than code; silently converting them as the whole repo violates user intent, so they are
# rejected with guidance (see classify_source).
NON_CODE_PAGE_SEGMENTS = ("pull", "issues", "issue", "releases", "commit", "commits",
                          "actions", "wiki", "security", "insights", "network", "graphs",
                          "pulse", "projects", "discussions", "settings", "compare",
                          "stargazers", "watchers", "forks", "packages", "raw", "archive")


# Hosting sites structurally isomorphic to GitHub (/<o>/<r>/tree|blob|blame/<ref>/<path> also work);
# tree/blob subpath parsing matches GitHub; fetch differences see acquire_repo (codeload is github.com only).
GITHUB_LIKE_HOSTS = ("github.com", "gitee.com")


def parse_github_target(url):
    """Parse a GitHub-like URL, returning {"host", "owner", "repo", "kind", "ref", "path"};
    returns None for non-GitHub-like hosts (github.com / gitee.com).

    kind:
      "repo" -- repo root (or without trailing path, preserving default repo-root behavior);
      "tree" -- https://<host>/<o>/<r>/tree/<ref>/<path...> (path may be empty);
      "blob" -- https://<host>/<o>/<r>/blob/<ref>/<path...> (path points to a file;
        blame/<ref>/<path...> is isomorphic to blob and handled identically);
      "page" -- non-code repo page (pull/issues/releases/commit/raw/archive, etc.,
        see NON_CODE_PAGE_SEGMENTS); includes "page" field and is rejected by classify_source.
    Limitation: ref takes only the first path segment (branch names containing '/' like feature/foo
    are unsupported; acquire_repo explains this on fetch failure)."""
    u = url.strip()
    m = re.match(r"^git@(github\.com|gitee\.com):([^/]+)/([^/#?]+?)(?:\.git)?$", u)
    if m:
        return {"host": m.group(1), "owner": m.group(2), "repo": m.group(3),
                "kind": "repo", "ref": None, "path": None}
    m = re.match(r"^https?://(github\.com|gitee\.com)/([^/]+)/([^/#?]+)", u)
    if not m:
        return None
    host, owner, repo = m.group(1), m.group(2), re.sub(r"\.git$", "", m.group(3))
    rest = u[m.end():].split("#")[0].split("?")[0].strip("/")
    kind, ref, path = "repo", None, None
    page = None
    if rest:
        segs = rest.split("/")
        if segs[0] in NON_CODE_PAGE_SEGMENTS:
            kind, page = "page", segs[0]
        elif len(segs) >= 2 and segs[0] in ("tree", "blob", "blame"):
            kind = "blob" if segs[0] == "blame" else segs[0]
            ref = urllib.parse.unquote(segs[1])
            path = "/".join(urllib.parse.unquote(s) for s in segs[2:]) or None
    out = {"host": host, "owner": owner, "repo": repo, "kind": kind, "ref": ref, "path": path}
    if page:
        out["page"] = page
    return out


def parse_git_url(url):
    """Generic git repository root URL (any host, two-segment root path only):
    https?://<host>/<o>/<r>[.git][/] or git@<host>:<o>/<r>[.git]
    Returns {"host", "owner", "repo", "clone_url"}; returns None if not a repo root URL.
    Multi-level paths (including tree/blob subpaths or group/subgroup/repo) do not match --
    non-GitHub-like hosts only support repo root input."""
    u = url.strip().split("#")[0].split("?")[0]
    m = re.match(r"^git@([A-Za-z0-9.-]+):([^/]+)/([^/]+?)(?:\.git)?$", u)
    if m:
        host, owner, repo = m.group(1), m.group(2), m.group(3)
        return {"host": host, "owner": owner, "repo": re.sub(r"\.git$", "", repo),
                "clone_url": u}
    m = re.match(r"^(https?)://([A-Za-z0-9.-]+)/([^/]+)/([^/]+?)/?$", u)
    if m:
        scheme, host, owner, repo = m.groups()
        repo = re.sub(r"\.git$", "", repo)
        return {"host": host, "owner": owner, "repo": repo,
                "clone_url": f"{scheme}://{host}/{owner}/{repo}.git"}
    return None


def classify_source(source):
    """Classify user input as local dir / GitHub-like target / generic git repo / rejected, returning a dict:

      {"type": "local", "path": <absolute path>}
      {"type": "github", "target": <parse_github_target result>, "normalized": <normalized URL>}
      {"type": "git", "target": <parse_git_url result>, "normalized": <clone_url>}
      {"type": "rejected", "reason": <one-line message for the user>}

    GitHub-like hosts (github.com and structurally isomorphic gitee.com) support repo root / tree /
    blob / blame forms; other git hosts (cnb.cool, atomgit, gitlab, etc.) only support repo root
    input (via git clone), rejecting subpath URLs with a hint.
    Normalization: protocol-less github.com/<o>/<r>, www.github.com, and owner/repo shorthand are
    expanded to https://github.com/<o>/<r>. owner/repo shorthand applies only when input is not an
    existing local directory (isdir checked first), and both segments must start with alphanumeric
    or underscore so relative paths like ./foo or ../foo are not captured.
    Rejections: raw.githubusercontent.com / gist / non-code repo pages each provide actionable
    rewrite hints."""
    s = source.strip()
    if os.path.isdir(s):
        return {"type": "local", "path": os.path.abspath(s)}
    if re.match(r"^(?:www\.)?github\.com/", s):
        s = "https://" + s
    elif re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$", s):
        s = "https://github.com/" + s
    s = re.sub(r"^https?://www\.github\.com/", "https://github.com/", s)

    m = re.match(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^#?]+)", s)
    if m:
        o, r, rest = m.group(1), m.group(2), m.group(3).strip("/")
        return {"type": "rejected", "reason":
                f"raw.githubusercontent.com is a raw file URL, not a repository URL. The equivalent file entry URL is "
                f"https://github.com/{o}/{r}/blob/{rest}; to convert the whole repository use https://github.com/{o}/{r}"}
    if re.match(r"^https?://gist\.github\.com/", s):
        return {"type": "rejected", "reason":
                "A gist is not a repository; plugin conversion only supports repository URLs. "
                "Please organize the gist into a repository or download it locally and pass the local directory"}
    t = parse_github_target(s)
    if t is None:
        g = parse_git_url(s)
        if g is not None:
            return {"type": "git", "target": g, "normalized": g["clone_url"]}
        if re.match(r"^https?://(?:www\.)?github\.com/?$", s) or \
                re.match(r"^https?://(?:www\.)?github\.com/[^/]+/?$", s):
            return {"type": "rejected", "reason":
                    f"Incomplete repository URL (missing repo name): {source}. Expected "
                    "https://github.com/<owner>/<repo>, a tree URL to a plugin directory, or a blob URL to a file"}
        if re.match(r"^https?://", s) or re.match(r"^git@", s):
            return {"type": "rejected", "reason":
                    f"Unrecognized repository URL: {source}. GitHub/Gitee support repo root / tree / blob URLs; "
                    "other git hosts (cnb.cool, atomgit, gitlab, etc.) only support repo root URLs "
                    "(https://<host>/<owner>/<repo>), without multi-level paths or tree/blob subpaths; "
                    "alternatively, git clone locally and pass the local directory"}
        return {"type": "rejected", "reason":
                f"Unrecognized input: {source}. Supports GitHub/Gitee repo root / tree / blob URLs, "
                "github.com/<owner>/<repo> or <owner>/<repo> shorthand, generic git repo root URLs, and local directories"}
    if t["kind"] == "page":
        page = t.get("page", "")
        if page == "commit":
            return {"type": "rejected", "reason":
                    f"/commit/<sha> is a commit detail page and cannot be used directly. To convert at that commit, "
                    f"use https://{t['host']}/{t['owner']}/{t['repo']}/tree/<sha>"}
        if page in ("raw", "archive"):
            return {"type": "rejected", "reason":
                    f"{page} URLs are file download/archive links and cannot be used directly. "
                    f"Use .../blob/<branch>/<file> for a file or .../tree/<branch>/<dir> for a directory"}
        return {"type": "rejected", "reason":
                f"The repository's {page} page is not a code URL and cannot be used directly. Provide the repo root URL, "
                ".../tree/<branch>/<dir> for a plugin directory, or .../blob/<branch>/<file> for a config file"}
    return {"type": "github", "target": t, "normalized": s}


def parse_github_url(url):
    """https://github.com/<owner>/<repo>[...] or git@github.com:<owner>/<repo>.git -> (owner, repo); None if not GitHub."""
    t = parse_github_target(url)
    return (t["owner"], t["repo"]) if t else None


def github_https_reachable():
    """Pre-check github.com:443 connectivity (3s): if unreachable, skip git clone and go straight
    to codeload tarball to avoid waiting on TCP timeout for every repository."""
    try:
        with socket.create_connection(("github.com", 443), timeout=3):
            return True
    except OSError:
        return False


_GITHUB_REACHABLE = None
_GITHUB_REACHABLE_LOCK = threading.Lock()
# Per-dest lock for acquire_repo: when expanding index entries in parallel, multiple entries may
# fetch the same repo simultaneously; same dest is serialized (double-checked inside lock to reuse
# already-fetched repos), while different repos still run in parallel.
_ACQUIRE_LOCKS = {}
_ACQUIRE_LOCKS_GUARD = threading.Lock()


def _acquire_lock_for(dest):
    with _ACQUIRE_LOCKS_GUARD:
        return _ACQUIRE_LOCKS.setdefault(dest, threading.Lock())


def acquire_repo(source, work_dir, notes, ref=None):
    """Turn source (GitHub-like URL / generic git URL / local directory) into a local directory path;
    returns (path, is_temp).

    github.com: prefers git clone --depth 1; falls back to codeload tarball when github.com is
    unreachable (codeload.github.com and github.com often differ in network reachability).
    Other hosts (gitee/cnb.cool/atomgit/gitlab, etc.): git clone only (no codeload fallback, no
    default_branch API probing; empty ref uses the remote default branch).
    When ref is non-empty (specified by tree/blob subpath URL), fetches strictly at that ref:
    failure reports that the ref may not exist, never silently falling back to the default branch --
    the user sees that ref's contents on the page, so switching refs would convert something else.
    codeload's tar.gz/<ref> accepts branch/tag/commit. When ref is empty, probes default_branch ->
    main -> master -> HEAD."""
    if os.path.isdir(source):
        return os.path.abspath(source), False
    t = parse_github_target(source)
    if t:
        host, owner, repo = t["host"], t["owner"], t["repo"]
    else:
        g = parse_git_url(source)
        if not g:
            raise RuntimeError(f"Unrecognized source (neither a local directory nor a git repo URL): {source}")
        host, owner, repo = g["host"], g["owner"], g["repo"]
    clone_url = (f"git@{host}:{owner}/{repo}.git" if source.strip().startswith("git@")
                 else f"https://{host}/{owner}/{repo}.git")
    ref_slug = re.sub(r"[^0-9A-Za-z._-]", "-", ref) if ref else ""
    dest = os.path.join(work_dir, f"{host}--{owner}--{repo}" + (f"--{ref_slug}" if ref_slug else ""))
    if os.path.isdir(dest):
        return dest, True
    global _GITHUB_REACHABLE
    with _GITHUB_REACHABLE_LOCK:
        if _GITHUB_REACHABLE is None:
            _GITHUB_REACHABLE = github_https_reachable()
            if not _GITHUB_REACHABLE:
                notes.append("Direct connection to github.com unreachable; using codeload tarball for this batch")
    # Serialize concurrent fetches of the same repo (parallel index expansion may reference the same repo)
    with _acquire_lock_for(dest):
        if os.path.isdir(dest):  # another thread finished fetching while waiting for lock
            return dest, True
        _fetch_repo_into(dest, clone_url, host, owner, repo, ref, work_dir, notes)
        return dest, True


def _fetch_repo_into(dest, clone_url, host, owner, repo, ref, work_dir, notes):
    """Fetch clone_url into dest (caller already holds dest's acquire lock; dest does not exist yet).
    Falls back to codeload tarball on clone failure per acquire_repo rules; raises RuntimeError on total failure."""

    def ref_error(detail):
        return RuntimeError(
            f"Failed to fetch branch/tag {ref!r} of {host}/{owner}/{repo} ({detail}). The ref may not exist; "
            "if the branch name contains '/' (e.g. feature/foo), tree/blob URLs cannot distinguish the branch "
            "name from the path boundary -- use a commit SHA or a tag URL without '/' instead")

    is_commit_sha = bool(ref and re.fullmatch(r"[0-9a-fA-F]{40}", ref))
    if (host != "github.com" or _GITHUB_REACHABLE) and not is_commit_sha:
        # 40-char hex is a commit SHA: git clone --branch does not accept bare SHAs, so go straight
        # to codeload tar.gz/<sha> (which accepts branch/tag/commit) instead of wasting a doomed clone
        try:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            # Windows defaults to MAX_PATH=260 and git defaults to core.longpaths=false: deep paths
            # fail checkout with "unable to create file"; enabling longpaths is harmless on macOS/Linux
            cmd = ["git", "-c", "core.longpaths=true", "clone", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [clone_url, dest]
            # Output is for failure diagnostics only; Windows default encoding (GBK) decoding git's
            # UTF-8 output raises UnicodeDecodeError and masks the real error, so pin utf-8 with replace
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=60, env=env)
            return
        except Exception as e:  # noqa: BLE001 — fall back to tarball on clone failure (github.com only)
            out = getattr(e, "output", "") or ""
            if ref and re.search(r"Remote branch .* not found|Couldn't find remote ref", out):
                raise ref_error("remote branch/tag not found") from e
            if host != "github.com":
                raise RuntimeError(f"git clone failed for {clone_url}: {out.strip()[:300] or e}") from e
            notes.append(f"git clone {owner}/{repo} failed ({type(e).__name__}), falling back to codeload tarball")
    if host != "github.com":
        # Non-github.com has no codeload fallback: clone is the only option; SHA ref is unsupported via clone
        if is_commit_sha:
            raise RuntimeError(f"{host} does not support fetching by commit SHA (no codeload fallback); "
                               f"use a branch/tag URL, or git clone and checkout {ref} then pass the local directory")
        raise RuntimeError(f"Failed to fetch repository {clone_url}")
    default_branch = None
    try:
        with urllib.request.urlopen(f"https://api.github.com/repos/{owner}/{repo}", timeout=20) as resp:
            default_branch = json.loads(resp.read().decode("utf-8")).get("default_branch")
    except Exception:  # noqa: BLE001 — if unavailable, try candidate branch names
        pass
    if ref:
        candidates = [ref]
    else:
        candidates = ([default_branch] if default_branch else []) + ["main", "master", "HEAD"]
    last_err = None
    ref_slug = re.sub(r"[^0-9A-Za-z._-]", "-", ref) if ref else ""
    # Prefix tarball temp filename with host/ref to avoid collisions during parallel fetches
    tarball = os.path.join(work_dir, f"{host}--{owner}--{repo}"
                         + (f"--{ref_slug}" if ref_slug else "") + ".tar.gz")
    for r in candidates:
        if not r:
            continue
        try:
            if r == "HEAD":
                tarball_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"
            elif ref and r == ref:
                # codeload bare ref form accepts branch/tag/commit sha
                tarball_url = (f"https://codeload.github.com/{owner}/{repo}/tar.gz/"
                               + urllib.parse.quote(r, safe=""))
            else:
                tarball_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{r}"
            # urlretrieve has no timeout parameter and can hang forever; download manually with timeout
            with urllib.request.urlopen(tarball_url, timeout=60) as resp, \
                    open(tarball, "wb") as f:
                shutil.copyfileobj(resp, f)
            os.makedirs(dest, exist_ok=True)
            subprocess.run(["tar", "-xzf", tarball, "-C", dest, "--strip-components=1"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            os.unlink(tarball)
            return
        except Exception as e:  # noqa: BLE001 — try next candidate ref
            last_err = e
    if ref:
        raise ref_error(f"{type(last_err).__name__}: {last_err}") from last_err
    raise RuntimeError(f"Failed to fetch repository {owner}/{repo}: {last_err}")


# ---------- Discover plugin sources ----------
def find_plugin_manifests(root, exclude_prefixes=()):
    """Recursively find all recognized plugin manifests under root (priority per MANIFEST_CANDIDATES),
    returning sorted plugin root directories relative to root. Root plugin and subdirectory copies
    may coexist in the same repo (e.g. deepseek root copy lacks files while plugins/ copy is complete),
    so after finding a manifest, only skip its manifest container dir and continue scanning other subdirs."""
    found = []
    root_fs = pc.winlong(root)  # use long-path prefix for walk/checks; keep clean relative paths in output
    for dirpath, dirnames, filenames in os.walk(root_fs):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        rel = os.path.relpath(dirpath, root_fs)
        if any(rel == p or rel.startswith(p + os.sep) for p in exclude_prefixes):
            dirnames[:] = []
            continue
        _mrel, dialect = detect_manifest(dirpath)
        if dialect:
            found.append(rel)
            # Only skip manifest container dirs (.codex-plugin/.claude-plugin); keep scanning other subdirs
            dirnames[:] = [d for d in dirnames if d not in DIALECT_MANIFEST_DIRS.values()]
    return sorted(found)


# Index file candidates (by priority): root plugins.json (native project convention) -> Codex
# marketplace -> Claude marketplace. Never silent when multiple coexist: prints candidate list and
# conflict warning, selects the first by priority, and logs selected/ignored ones in report notes.
INDEX_CANDIDATES = [
    ("plugins.json", "plugins.json index"),
    (os.path.join(".agents", "plugins", "marketplace.json"), "Codex marketplace"),
    (os.path.join(".claude-plugin", "marketplace.json"), "Claude marketplace"),
    ("registry-snapshot.json", "DeepSeek registry"),
]


def _index_base_dir(root, index_path):
    """Base directory for resolving relative paths in index entries: marketplace convention resolves
    relative to the marketplace root (parent of .claude-plugin/ or .agents/); plugins.json resolves
    relative to its own directory."""
    d = os.path.dirname(index_path)
    if os.path.basename(d) == ".claude-plugin":
        return os.path.dirname(d)
    if d.endswith(os.path.join(".agents", "plugins")):
        return os.path.dirname(os.path.dirname(d))
    return d


def _normalize_marketplace_entry(entry):
    """Normalize a marketplace.json entry into a unified index entry (with url or path); returns None if unrecognized.

    Supports both observed formats: object source {"source": {"source": "local", "path": "./plugins/xxx"}}
    / {"source": {"source": "url", "url": "https://..."}} (awesome-codex-plugins) and string source
    "source": "./connect-apps" / "https://..." (awesome-claude-plugins); entries with direct url/path
    fields remain supported. When source.url is a local relative path like "./", it is treated as path
    (e.g. superpowers' {"source": {"source": "url", "url": "./"}}).
    Official Claude marketplace external sources (claude-plugins-community / knowledge-work-plugins):
    - {"source": "url", "url": <repo>, "sha"/"ref": ...} -> preserve url + sha/ref,
      fetching at the pinned commit (see _resolve_index_entry);
    - {"source": "git-subdir", "url": <repo>, "path": <in-repo subdir>, "ref"/"sha": ...}
      -> preserve url + subdir + sha/ref; path here is an in-repo path, not a local relative path,
      and url must not be dropped (dropping url would look for a nonexistent local src dir)."""
    if not isinstance(entry, dict):
        return None
    out = dict(entry)
    # DeepSeek registry (registry-snapshot.json) uses a multilingual object {en, zh} for description;
    # manifest expects a string: prefer zh, fall back to en
    desc = out.get("description")
    if isinstance(desc, dict):
        flat = desc.get("zh") or desc.get("en") or next(
            (v for v in desc.values() if isinstance(v, str) and v.strip()), None)
        if flat:
            out["description"] = flat
        else:
            out.pop("description", None)
    src_field = entry.get("source")
    entry_url = entry.get("url")
    has_remote_url = isinstance(entry_url, str) and re.match(r"^https?://", entry_url) is not None
    if has_remote_url and isinstance(src_field, str) and src_field in ("url", "git-subdir", "github"):
        # Share parsing between top-level source type declarations and object form, keeping subdir and sha/ref.
        src_field = entry
    if isinstance(src_field, dict):
        src_kind = src_field.get("source")
        src_url = src_field.get("url")
        if isinstance(src_url, str) and re.match(r"^https?://", src_url) and \
                src_kind in ("url", "git-subdir", "github"):
            # External repo source: preserve url and pinned sha/ref; record git-subdir's path as subdir
            out.pop("source", None)
            out["url"] = src_url
            out.pop("path", None)
            if src_kind == "git-subdir" and isinstance(src_field.get("path"), str) \
                    and src_field["path"].strip("./"):
                out["subdir"] = src_field["path"].strip("./")
            for k in ("sha", "ref"):
                if isinstance(src_field.get(k), str) and src_field[k].strip():
                    out[k] = src_field[k].strip()
            return out
    path = url = None
    if isinstance(src_field, dict):
        path = src_field.get("path")
        url = src_field.get("url")
    elif isinstance(src_field, str):
        if re.match(r"^https?://", src_field):
            url = src_field
        elif not has_remote_url or src_field.startswith(".") or "/" in src_field or "\\" in src_field:
            # Bare word in source may be a marketplace tag; do not turn a tag into path when a remote URL exists.
            # Explicit local paths still take priority; keep bare directory names when there is no remote URL.
            path = src_field
    path = path or entry.get("path")
    url = url or entry.get("url")
    if isinstance(url, str) and not re.match(r"^https?://", url):
        path = path or url
        url = None
    if not (path or url):
        return None
    out.pop("source", None)
    if path:
        out["path"] = path
        out.pop("url", None)
    else:
        out["url"] = url
    return out


def _read_index(index_path, kind):
    """Read an index file and normalize its entries; returns entry list or None if invalid.
    Entries are unified dicts with at least url (external GitHub repo) or path (relative dir);
    name/install_url/description/owner/category are optional metadata."""
    try:
        data = pc.load_json(index_path)
    except Exception:  # noqa: BLE001 — invalid JSON is not an index
        return None
    entries = data.get("plugins")
    if not isinstance(entries, list) or not entries:
        return None
    if kind == "plugins.json index":
        if all(isinstance(e, dict) and (e.get("url") or e.get("path")) for e in entries):
            return entries
        return None
    norm = [e for e in (_normalize_marketplace_entry(x) for x in entries) if e]
    return norm or None


def find_index_candidates(root):
    """Valid index candidates under root as [(rel_path, kind)] in INDEX_CANDIDATES priority order."""
    found = []
    for rel, kind in INDEX_CANDIDATES:
        p = os.path.join(root, rel)
        if os.path.isfile(p) and _read_index(p, kind):
            found.append((rel, kind))
    return found


def load_index_file(root, notes=None):
    """Discover root's index file; returns {"entries", "index_path", "base_dir", "kind"} or None.

    Candidates and priority per INDEX_CANDIDATES; when multiple coexist, prints candidate list and
    conflict warning (never silent), picks highest priority, and records ignored ones in notes."""
    found = find_index_candidates(root)
    if not found:
        return None
    rel, kind = found[0]
    index_path = os.path.join(root, rel)
    entries = _read_index(index_path, kind)
    if len(found) > 1:
        listing = ", ".join(f"{r} ({k})" for r, k in found)
        msg = (f"Found {len(found)} index candidates: {listing}; by priority "
               f"{' > '.join(r for r, _k in INDEX_CANDIDATES)}, selecting {rel} "
               f"and ignoring {', '.join(r for r, _k in found[1:])}")
        print(f"[Index conflict warning] {msg}")
        if notes is not None:
            notes.append("Index candidate conflict: " + msg)
    return {"entries": entries, "index_path": index_path,
            "base_dir": _index_base_dir(root, index_path), "kind": kind}


def find_subdir_marketplaces(root):
    """Recursively discover marketplace.json in subdirectories (*/.claude-plugin/marketplace.json
    and */.agents/plugins/marketplace.json; root candidates are handled by load_index_file and
    skipped here) as well as registry-snapshot.json anywhere (DeepSeek registry index, e.g.
    data/registry-snapshot.json); returns sorted [(index_path, base_dir, kind)]."""
    root_rels = {r for r, _k in INDEX_CANDIDATES}
    found = []
    root_fs = pc.winlong(root)  # scan with long-path prefix (avoid missing deep dirs); return clean absolute paths
    for dirpath, dirnames, filenames in os.walk(root_fs):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        rel_dir = os.path.relpath(dirpath, root_fs)
        for fname in ("marketplace.json", "registry-snapshot.json"):
            if fname not in filenames:
                continue
            p = os.path.normpath(os.path.join(root, rel_dir, fname))
            if os.path.relpath(p, root) in root_rels:
                continue
            if fname == "registry-snapshot.json":
                if _read_index(p, "DeepSeek registry"):
                    found.append((p, _index_base_dir(root, p), "DeepSeek registry"))
            elif os.path.basename(dirpath) == ".claude-plugin" or \
                    dirpath.endswith(os.path.join(".agents", "plugins")):
                if _read_index(p, "marketplace"):
                    found.append((p, _index_base_dir(root, p), "marketplace"))
    return sorted(found)


def read_index_market_name(index_path):
    """Read the top-level name field of an index file (Claude marketplace / plugins.json market name);
    returns None if unreadable or if name is missing/non-string."""
    try:
        data = pc.load_json(index_path)
    except Exception:  # noqa: BLE001 — treat unreadable file as unconfigured
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def resolve_market_from_repo(root, notes):
    """Resolve market name from top-level name in repo index configs: root candidates first
    (in INDEX_CANDIDATES order), then subdirectory marketplace.json files; returns None if
    unresolvable or invalid after normalization (caller falls back to personal)."""
    candidates = [os.path.join(root, rel) for rel, _k in INDEX_CANDIDATES]
    candidates += [p for p, _b, _k in find_subdir_marketplaces(root)]
    for index_path in candidates:
        if not os.path.isfile(index_path):
            continue
        raw = read_index_market_name(index_path)
        if raw is None:
            continue
        market = pc.normalize_market_name(raw)
        try:
            pc.validate_market_name(market)
        except ValueError:
            notes.append(f"Market name {raw!r} in index config {os.path.relpath(index_path, root)}"
                         " is invalid after normalization; falling back to personal")
            return None
        notes.append(f"Market name resolved from index config as {market}"
                     f" (name field of {os.path.relpath(index_path, root)})")
        return market
    return None


def _expand_indexes(root, indexes, work_dir, notes, limit=0, jobs=1):
    """Expand index entries into a list of plugin sources (entry-level errors become placeholder
    entries; when multiple entries reference the same plugin dir, convert once and record duplicates
    as aliases on the same source).
    Source resolution (external url entries each fetch from upstream) runs in parallel up to jobs,
    preserving index entry order; when limit > 0, only the first limit entries are resolved so
    --limit remains effective on index repos with many external url entries."""
    tasks = []
    for idx in indexes:
        for entry in idx["entries"]:
            if limit and len(tasks) >= limit:
                break
            tasks.append((entry, idx["base_dir"]))
        if limit and len(tasks) >= limit:
            break

    def resolve(task):
        entry, base_dir = task
        return entry, _resolve_index_entry(root, entry, work_dir, notes, base_dir=base_dir)

    if jobs > 1 and len(tasks) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
            resolved = list(ex.map(resolve, tasks))
    else:
        resolved = [resolve(t) for t in tasks]

    sources = []
    for entry, s in resolved:
        if s.get("error"):
            sources.append({"source_dir": None, "index_entry": entry,
                            "origin": _entry_origin(entry), "error": s["error"]})
            continue
        for existing in sources:
            # Skill-collection entries (with a skills array) are distinct plugins even if source_dir
            # is the same (e.g. anthropics/skills has 5 entries with source "./"); do not merge them
            if existing.get("source_dir") == s["source_dir"] and not entry.get("skills"):
                existing.setdefault("aliases", []).append(entry.get("name"))
                break
        else:
            sources.append({"source_dir": s["source_dir"], "repo_root": s["repo_root"],
                            "index_entry": entry, "origin": _entry_origin(entry)})
    return sources


def choose_manifest(repo_root, entry, notes, strict_name=False):
    """Pick the plugin subdirectory in a repo for an index entry: if unique, return it; for a
    multi-plugin monorepo, match by plugin name first, then by install_url subdirectory, finally
    falling back to the first manifest with a note.
    When strict_name=True, only a plugin-name match is accepted (even for a single manifest),
    returning None otherwise -- used for same-name fallback when an index entry path is missing,
    never guessing on mismatch."""
    repo_root_fs = pc.winlong(repo_root)
    manifests = find_plugin_manifests(repo_root_fs)
    if not manifests:
        return None
    if len(manifests) == 1 and not strict_name:
        return manifests[0]
    # Match by plugin name first (index install_url can point multiple entries to the same manifest and is unreliable)
    want = pc.normalize_plugin_name((entry or {}).get("name", ""))
    if want:
        for rel in manifests:
            mrel, _d = detect_manifest(os.path.join(repo_root_fs, rel))
            try:
                cm = pc.load_json(os.path.join(repo_root_fs, rel, mrel))
            except Exception:  # noqa: BLE001
                continue
            if pc.normalize_plugin_name(str(cm.get("name", ""))) == want or \
               pc.normalize_plugin_name(os.path.basename(rel)) == want:
                return rel
    if strict_name:
        return None
    if len(manifests) == 1:
        return manifests[0]
    install_url = (entry or {}).get("install_url", "")
    m = re.match(r"^https?://raw\.githubusercontent\.com/[^/]+/[^/]+/[^/]+/(.+)/\.codex-plugin/plugin\.json$",
                 install_url)
    if m:
        rel = m.group(1).strip("/")
        if rel in manifests:
            return rel
    notes.append(f"Repository for index entry {(entry or {}).get('name')!r} has {len(manifests)} plugins; "
                 f"could not match by name/install_url, using first: {manifests[0]}")
    return manifests[0]


def _plugin_rel_tokens(value):
    """Recursively collect all relative-path tokens pointing to files inside the plugin/repo from a JSON value.
    Returns [(relpath, scope)]: scope="plugin" means relative to plugin root (./, ${PLUGIN_ROOT},
    $PLUGIN_ROOT), scope="repo" means relative to repo root (__REPO_ROOT__, commonly used by monorepo
    subdirectory plugins to reference shared repo-level sources).
    A bare token (without explicit prefix) is treated as a path only if: (1) it is the sole token in
    the string (standalone array element, e.g. "args": ["scripts/mcp_server.py"]), or the preceding
    token is an interpreter/launcher (node/bash/python3, etc., as in "bash scripts/x.sh");
    (2) it contains neither @ nor : (docker images ghcr.io/org/img@sha256:..., npm specs pkg@1.2.3,
    and URLs are not local paths); (3) the final segment is not a pure version string (header values
    like "codex-plugin/1.7.0" end with a version, not a file; extensionless executables like
    scripts/gm-mcp-launcher remain unaffected).
    Path-like words in other multi-token text (bash comments, prose, HTTP headers) are not
    misclassified as file references."""
    out = []
    if isinstance(value, str):
        tokens = [t for t in re.split(r"\s+", value.strip()) if t]
        single = len(tokens) == 1
        prev = ""
        for tok in tokens:
            tok = tok.strip("\"'`").rstrip(";,.:").strip("\"'`")
            for prefix, scope in (("${PLUGIN_ROOT}/", "plugin"), ("$PLUGIN_ROOT/", "plugin"),
                                  ("__REPO_ROOT__/", "repo")):
                if tok.startswith(prefix):
                    out.append((tok[len(prefix):], scope))
                    break
            else:
                if tok.startswith("./"):
                    out.append((tok[2:], "plugin"))
                elif ("/" in tok and not os.path.isabs(tok)
                      and not tok.startswith(("-", "@", "~", "*"))
                      and "://" not in tok
                      and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok)
                      and not any(c in tok for c in "><()|;&$@:")
                      and not re.fullmatch(r"v?\d+(\.\d+)*", tok.rsplit("/", 1)[-1])
                      and (single or prev in PATH_COMMAND_BASENAMES)):
                    out.append((tok, "plugin"))
            prev = tok
    elif isinstance(value, dict):
        for v in value.values():
            out += _plugin_rel_tokens(v)
    elif isinstance(value, list):
        for v in value:
            out += _plugin_rel_tokens(v)
    return out


def declared_paths_missing(source_dir, repo_root=None):
    """Check whether any component path declared by the manifest is missing from source_dir
    (used to detect incomplete awesome mirrors). Beyond top-level skills/commands/agents/
    mcpServers/hooks, also inspects files referenced by mcpServers/hooks (.mcp.json/hooks.json)
    to verify in-plugin scripts referenced in args/command exist -- mirrors often include only
    manifests/docs while omitting referenced source directories.
    __REPO_ROOT__ references are checked against repo_root (defaulting to source_dir); other
    relative paths are checked against (1) manifest directory (e.g. ../skills/ in
    .codex-plugin/plugin.json) and (2) plugin root (both styles occur in real repos); only
    missing if both fail."""
    source_dir_fs = pc.winlong(source_dir)
    repo_root_fs = pc.winlong(repo_root) if repo_root else source_dir_fs
    mrel, _dialect = detect_manifest(source_dir_fs)
    try:
        m = pc.load_json(os.path.join(source_dir_fs, mrel))
    except Exception:  # noqa: BLE001 — broken manifest is reported during conversion, not a mirror issue
        return False
    manifest_base = os.path.normpath(os.path.join(source_dir_fs, os.path.dirname(mrel)))

    def _declared_path_exists(e):
        rel = e[2:] if e.startswith("./") else e
        return any(os.path.exists(os.path.normpath(os.path.join(base, rel)))
                   for base in dict.fromkeys((manifest_base, source_dir_fs)))

    for field in ("skills", "commands", "agents"):
        raw = m.get(field)
        for e in (raw if isinstance(raw, list) else [raw]):
            if isinstance(e, str) and not _declared_path_exists(e):
                return True
    for field in ("mcpServers", "hooks"):
        raw = m.get(field)
        data = raw
        if isinstance(raw, str):
            rel = raw[2:] if raw.startswith("./") else raw
            ref = next((os.path.normpath(os.path.join(base, rel))
                        for base in dict.fromkeys((manifest_base, source_dir_fs))
                        if os.path.isfile(os.path.normpath(os.path.join(base, rel)))), None)
            if ref is None:
                return True
            try:
                data = pc.load_json(ref)
            except Exception:  # noqa: BLE001 — invalid JSON is reported during conversion
                continue
        for rel, scope in _plugin_rel_tokens(data):
            base = repo_root_fs if scope == "repo" else source_dir_fs
            if not os.path.exists(os.path.normpath(os.path.join(base, rel))):
                return True
    return False


def _entry_origin(entry):
    return entry.get("url") or entry.get("path") or ""


def _manifest_repository_url(source_dir):
    """Read the plugin manifest's repository field and return a normalized git repo URL, or None if
    missing/invalid. Supports string ("https://github.com/o/r") and object ({"url": ...}) forms;
    non-git URLs (homepages/docs) are filtered out via classify_source."""
    mrel, _d = detect_manifest(source_dir)
    if not mrel:
        return None
    try:
        m = pc.load_json(os.path.join(source_dir, mrel))
    except Exception:  # noqa: BLE001 — broken manifest is reported during conversion; treat as no repository
        return None
    repo = m.get("repository")
    url = repo if isinstance(repo, str) else (
        repo.get("url") if isinstance(repo, dict) else None)
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("git+"):
        url = url[4:]  # npm package.json repository convention prefix (git+https://...)
    cls = classify_source(url)
    if cls["type"] in ("github", "git"):
        return cls["normalized"]
    return None


def _refetch_full_source(entry, url, pin, source_dir, repo_root, work_dir, notes):
    """When a mirror/source dir is incomplete, fetch the full repo from upstream and return
    (source_dir, repo_root) (possibly unchanged). On fetch failure, missing manifest, or equally
    incomplete upstream, keep the original dir and log notes without interrupting the batch."""
    try:
        full_root, _ = acquire_repo(url, work_dir, notes, ref=pin)
        full_chosen = choose_manifest(full_root, entry, notes)
        if full_chosen is None:
            notes.append(f"No recognized plugin manifest found in upstream repo for {entry.get('name')!r}; keeping mirror")
        else:
            candidates = [full_chosen] + [r for r in find_plugin_manifests(full_root)
                                          if r != full_chosen]
            picked = None
            for cand in candidates:
                if not declared_paths_missing(
                        os.path.normpath(os.path.join(full_root, cand)), full_root):
                    picked = cand
                    break
            if picked is not None:
                if picked != full_chosen:
                    notes.append(f"Switched {entry.get('name')!r} to complete plugin directory in repo: {picked}")
                return os.path.normpath(os.path.join(full_root, picked)), full_root
            notes.append(f"Upstream repo for {entry.get('name')!r} is also missing declared component paths; keeping mirror")
    except RuntimeError as e:
        notes.append(f"Failed to fetch upstream repo for {entry.get('name')!r} ({e}); keeping mirror")
    return source_dir, repo_root


def _resolve_index_entry(root, entry, work_dir, notes, base_dir=None):
    """Resolve the plugin source for an index entry; returns {source_dir, repo_root} or {error}.

    Resolution rules:
    - Entry has path: resolved relative to index base_dir (default root; marketplace root for
      marketplace.json, own dir for plugins.json; absolute paths kept as-is). Falls back to repo
      root if not found under base_dir (logged in notes). path takes precedence over url. If path
      dir does not exist, falls back to a unique same-name manifest in the repo (e.g. index points
      to ./plugins/<name> while plugin lives at repo root, like codex-seo) with a note, else error.
      If path dir has no recognized manifest: if entry has a skills array (Claude marketplace
      skill-collection entry, e.g. anthropics/skills), pass to convert_one to assemble; else fall
      back to url if valid (logged in notes), else error.
      If path dir is valid but mirror is incomplete (declared component paths missing), fetch full
      source via manifest's repository field (keeping mirror with notes if missing/invalid/failed).
    - Entry has url (GitHub): check local mirror plugins/<owner>/<repo>/ first; if missing or
      incomplete (declared component paths missing), fetch full upstream repo and prefer a complete
      manifest dir. Entries with sha/ref (official Claude marketplace external sources) fetch at
      the pinned commit; entries with subdir (git-subdir) use that in-repo subdirectory."""
    base_dir = base_dir or root
    rel_path = entry.get("path")
    url = entry.get("url", "")
    if rel_path:
        # normpath normalizes forward slashes; isdir uses winlong to avoid Windows MAX_PATH on deep paths
        p = rel_path if os.path.isabs(rel_path) else os.path.normpath(os.path.join(base_dir, rel_path))
        if not os.path.isdir(pc.winlong(p)) and not os.path.isabs(rel_path) \
                and os.path.abspath(base_dir) != os.path.abspath(root):
            alt = os.path.normpath(os.path.join(root, rel_path))
            if os.path.isdir(pc.winlong(alt)):
                notes.append(f"Index entry path {rel_path!r} not found under index base dir; resolved relative to repo root")
                p = alt
        if not os.path.isdir(pc.winlong(p)):
            chosen = choose_manifest(root, entry, notes, strict_name=True)
            if chosen is not None:
                notes.append(f"Index entry path {rel_path!r} does not exist; fell back by name to "
                             f"in-repo plugin directory: {chosen}")
                return {"source_dir": os.path.normpath(os.path.join(root, chosen)),
                        "repo_root": root}
            return {"error": f"Directory referenced by index entry path does not exist: {rel_path}"}
        repo_root = os.path.abspath(p)
        chosen = choose_manifest(repo_root, entry, notes)
        if chosen is not None:
            source_dir = os.path.normpath(os.path.join(repo_root, chosen))
            # When a path-entry mirror is incomplete (declared component paths missing), fetch full
            # source via manifest's repository field -- path entries lack url, so repository is the
            # author-declared upstream; keep mirror if missing/invalid/failed (missing components
            # will be downgraded via dropped_components during conversion)
            if declared_paths_missing(source_dir, repo_root):
                repo_url = _manifest_repository_url(source_dir)
                if repo_url:
                    notes.append(f"Mirror for {entry.get('name')!r} is incomplete (declared component paths missing); "
                                 f"fetching upstream via manifest repository: {repo_url}")
                    source_dir, repo_root = _refetch_full_source(
                        entry, repo_url, None, source_dir, repo_root, work_dir, notes)
                else:
                    notes.append(f"Mirror for {entry.get('name')!r} is incomplete (declared component paths missing), "
                                 "and manifest has no valid repository field to fetch upstream; keeping mirror")
            return {"source_dir": source_dir, "repo_root": repo_root}
        if isinstance(entry.get("skills"), list) and entry["skills"]:
            # Claude marketplace skill-collection entry: no manifest, skills array lists skill dirs,
            # assembled as a skill-only plugin by convert_one (anthropics/skills pattern)
            return {"source_dir": os.path.normpath(p), "repo_root": repo_root}
        if parse_github_url(url):
            notes.append(f"No recognized plugin manifest in path directory for index entry {entry.get('name')!r}; "
                         f"falling back to url source")
        else:
            return {"error": f"No recognized plugin manifest in index entry path directory: {rel_path}"}

    gh = parse_github_url(url)
    if not gh:
        return {"error": f"Index entry has neither a valid path nor a GitHub url: {entry.get('name')!r}"}
    owner, repo = gh
    pin = entry.get("sha") or entry.get("ref")  # pinned commit/branch for external source (codeload accepts bare sha)
    subdir = entry.get("subdir")
    mirror = os.path.join(root, "plugins", owner, repo)
    repo_root = mirror if os.path.isdir(pc.winlong(mirror)) else None
    if repo_root is None:
        try:
            repo_root, _ = acquire_repo(url, work_dir, notes, ref=pin)
        except RuntimeError as e:
            return {"error": str(e)}
    if subdir:
        # git-subdir entry: plugin is in an in-repo subdirectory; skip full-repo manifest scan
        p = os.path.normpath(os.path.join(repo_root, subdir))
        if not os.path.isdir(pc.winlong(p)):
            return {"error": f"Subdirectory referenced by git-subdir entry does not exist in repo: {subdir}"}
        return {"source_dir": p, "repo_root": repo_root}
    chosen = choose_manifest(repo_root, entry, notes)
    if chosen is None:
        # Declarative subset detection for cordis/code-based plugin repos: wrap as skill-only if
        # root has SKILL.md/skills/; pure-code repos (e.g. package.json only) fail explicitly
        if os.path.isfile(pc.winlong(os.path.join(repo_root, "SKILL.md"))) \
                or os.path.isdir(pc.winlong(os.path.join(repo_root, "skills"))):
            notes.append(f"Repository for {entry.get('name')!r} has no recognized manifest, but root has "
                         "SKILL.md/skills/; wrapping as skill-only")
            return {"source_dir": repo_root, "repo_root": repo_root}
        return {"error": "No recognized plugin manifest found in repo (kimi.plugin.json / .kimi-plugin / "
                         ".codex-plugin / .claude-plugin / .codebuddy-plugin / gemini-extension.json / "
                         "server.json / plugin.json), and no root SKILL.md/skills/; "
                         "code-only plugins (e.g. cordis packages) have no declarative content to migrate"}
    source_dir = os.path.normpath(os.path.join(repo_root, chosen))
    if os.path.isdir(pc.winlong(mirror)) and declared_paths_missing(source_dir, repo_root):
        # Mirror only contains partial files (declared components missing) -> fetch full upstream repo
        notes.append(f"Mirror for {entry.get('name')!r} is incomplete (declared component paths missing); fetching upstream repo")
        source_dir, repo_root = _refetch_full_source(
            entry, url, pin, source_dir, repo_root, work_dir, notes)
    return {"source_dir": source_dir, "repo_root": repo_root}


def discover_sources(root, work_dir, notes, limit=0, jobs=1):
    """Return [{source_dir, repo_root, index_entry, origin}]: local source dir + repo root +
    optional index metadata for each plugin to convert.

    Discovery order:
      1. Root index candidates (plugins.json / .agents/plugins/marketplace.json /
         .claude-plugin/marketplace.json; prints candidate list and conflict warning when multiple
         coexist, selecting by INDEX_CANDIDATES priority and logging to report);
      2. Subdirectory marketplace.json files (discovered recursively and expanded in index mode);
      3. Standard repo mode: scan whole repo for recognized plugin manifests;
      4. If none found but root has SKILL.md/skills/, wrap the whole repo as a skill-only plugin.
    Index entries support url (GitHub repo via mirror/upstream) and path (in-repo/local relative dir).
    When limit > 0, index expansion processes only the first limit entries (avoiding cloning
    hundreds of external url repos before truncating during conversion)."""
    idx = load_index_file(root, notes)
    indexes = []
    if idx:
        notes.append(f"Detected index file {os.path.relpath(idx['index_path'], root)}"
                     f" ({idx['kind']}, {len(idx['entries'])} entries)")
        indexes.append(idx)
    else:
        for index_path, base_dir, kind in find_subdir_marketplaces(root):
            entries = _read_index(index_path, kind)
            msg = (f"Discovered subdirectory {kind} index {os.path.relpath(index_path, root)}"
                   f" ({len(entries)} entries)")
            print(f"[Index] {msg}")
            notes.append(msg)
            indexes.append({"entries": entries, "index_path": index_path,
                            "base_dir": base_dir, "kind": kind})
    if indexes:
        return _expand_indexes(root, indexes, work_dir, notes, limit=limit, jobs=jobs)

    sources = []
    manifests = find_plugin_manifests(root)
    if manifests:
        for rel in manifests:
            sources.append({"source_dir": os.path.normpath(os.path.join(root, rel)),
                            "repo_root": root,
                            "index_entry": None, "origin": "repo-scan"})
        return sources
    if os.path.isfile(os.path.join(root, "SKILL.md")) or os.path.isdir(os.path.join(root, "skills")):
        notes.append("No recognized plugin manifest in repo, but root has SKILL.md/skills/; wrapping whole repo as skill-only plugin")
        sources.append({"source_dir": root, "repo_root": root,
                        "index_entry": None, "origin": "skill-only-fallback"})
        return sources
    raise RuntimeError("No convertible plugins found in repo (no kimi.plugin.json / .kimi-plugin / "
                       ".codex-plugin / .claude-plugin / .codebuddy-plugin / gemini-extension.json / "
                       "server.json / plugin.json, and no root SKILL.md/skills/)")


# ---------- Field conversion ----------
def map_category(raw, notes):
    if not raw:
        return pc.DEFAULT_CATEGORY
    upper = str(raw).strip().upper()
    if upper in pc.CATEGORIES:
        return upper
    mapped = CATEGORY_MAP.get(str(raw).strip().lower())
    if mapped is None:
        notes.append(f"No category mapping for {raw!r}; falling back to {pc.DEFAULT_CATEGORY}")
        return pc.DEFAULT_CATEGORY
    if mapped != raw:
        notes.append(f"category {raw!r} -> {mapped}")
    return mapped


def rewrite_plugin_root_refs(value):
    """Normalize plugin-root placeholders in a string to '.' (caller decides how to build ./ relative
    paths); normalize Gemini's ${/} path separator placeholder to '/'."""
    out = str(value)
    for token in PLUGIN_ROOT_TOKENS:
        out = out.replace(token, ".")
    return out.replace("${/}", "/")


def resolve_ref_in_source(source_dir, raw, base_dir=None):
    """Resolve a file path reference in a manifest (value of mcpServers/hooks).
    Candidates tried in order: (1) relative to the manifest's own directory (base_dir, skipped if
    None) -- e.g. .cursor-plugin/plugin.json writing ./hooks/hooks.json means
    .cursor-plugin/hooks/hooks.json; (2) relative to plugin root (both styles exist in real repos);
    (3) same-named file directly under plugin root (Codex repos often place .mcp.json/hooks.json at
    plugin root while the manifest writes an out-of-bounds path). Returns None if not found."""
    raw = raw.replace("\\", "/")
    rel = raw[2:] if raw.startswith("./") else raw
    abs_src = os.path.abspath(source_dir)
    candidates = []
    if base_dir:
        candidates.append(os.path.normpath(os.path.join(base_dir, rel)))
    candidates.append(os.path.normpath(os.path.join(abs_src, rel)))
    candidates.append(os.path.join(abs_src, os.path.basename(rel)))
    for ref in candidates:
        if (ref == abs_src or ref.startswith(abs_src + os.sep)) and os.path.isfile(ref):
            return ref
    return None


def sanitize_mcp_common_fields(cfg, sname, notes):
    """Validate common mcpServers entry keys (enabledTools/disabledTools/startupTimeoutMs/
    toolTimeoutMs/enabled) against the zod schema; returns dict of valid keys, dropping invalid ones with notes."""
    out = {}
    for k in ("enabledTools", "disabledTools"):
        if k in cfg:
            v = cfg[k]
            if isinstance(v, list) and all(isinstance(t, str) for t in v):
                out[k] = v
            else:
                notes.append(f"mcpServers.{sname}.{k} is not a string array; dropped")
    for k in ("startupTimeoutMs", "toolTimeoutMs"):
        if k in cfg:
            v = cfg[k]
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                out[k] = v
            else:
                notes.append(f"mcpServers.{sname}.{k} is not a positive integer; dropped")
    # Map workbuddy-style timeout (ms) to toolTimeoutMs (explicit toolTimeoutMs takes precedence)
    if isinstance(cfg.get("timeout"), (int, float)) and not isinstance(cfg.get("timeout"), bool) \
            and cfg["timeout"] >= 1:
        out.setdefault("toolTimeoutMs", int(cfg["timeout"]))
    if "enabled" in cfg:
        if isinstance(cfg["enabled"], bool):
            out["enabled"] = cfg["enabled"]
        else:
            notes.append(f"mcpServers.{sname}.enabled is not a boolean; dropped")
    return out


def convert_mcp_servers(source_dir, raw, notes, ref_base=None):
    """Convert source-dialect mcpServers (object or path string to .mcp.json) -> Kimi inline object;
    returns (None, reason) if unconvertible. String references resolve relative to manifest dir (ref_base)."""
    data = raw
    if isinstance(raw, str):
        ref = resolve_ref_in_source(source_dir, raw, base_dir=ref_base)
        if ref is None:
            return None, f"mcpServers referenced file does not exist: {raw}"
        try:
            data = pc.load_json(ref)
        except Exception as e:  # noqa: BLE001
            return None, f"mcpServers file is not valid JSON: {raw}: {e}"
        notes.append(f"Inlined mcpServers from {raw}")
    if not isinstance(data, dict):
        return None, f"Unsupported mcpServers shape: {type(data).__name__}"
    servers = data.get("mcpServers") or data.get("mcp_servers") or data
    if not isinstance(servers, dict) or not servers:
        return None, "mcpServers contains no server entries"
    out = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            notes.append(f"mcpServers.{name} is not an object; dropped")
            continue
        cfg = dict(cfg)
        root_ref_used = any(token in json.dumps(cfg) for token in PLUGIN_ROOT_TOKENS)
        entry = {}
        if cfg.get("url"):
            entry["url"] = cfg["url"]
            # workbuddy style: staticHeaders is synonymous with headers (headers wins on conflict)
            merged_headers = {}
            if isinstance(cfg.get("staticHeaders"), dict):
                merged_headers.update({str(k): str(v) for k, v in cfg["staticHeaders"].items()})
            if isinstance(cfg.get("headers"), dict):
                merged_headers.update({str(k): str(v) for k, v in cfg["headers"].items()})
            if merged_headers:
                entry["headers"] = merged_headers
            if cfg.get("auth") == "oauth":
                entry["auth"] = "oauth"
            if isinstance(cfg.get("bearerTokenEnvVar"), str) and cfg["bearerTokenEnvVar"].strip():
                entry["bearerTokenEnvVar"] = cfg["bearerTokenEnvVar"].strip()
            if cfg.get("transport") in ("sse", "http"):
                entry["transport"] = cfg["transport"]
            elif cfg.get("transport") is not None:
                notes.append(f"mcpServers.{name}.transport {cfg.get('transport')!r} is invalid (allowed: sse/http); dropped")
        elif cfg.get("command"):
            command = rewrite_plugin_root_refs(cfg["command"])
            if command.startswith(".") and not command.startswith("./"):
                command = "./" + command.lstrip("./")
            if os.path.isabs(command):
                # Keep all absolute paths by PATH command name (no interpreter allowlist filtering or
                # dropping): check_plugin_deps verifies whether the tool is on PATH / publicly
                # installable; unusable ones go to the hold_deps_unavailable bucket rather than being
                # irreversibly dropped during conversion
                base = os.path.basename(command)
                notes.append(f"mcpServers.{name}.command absolute path {command} -> treated as PATH command {base} "
                             "(dependency check will flag if missing from PATH or uninstallable)")
                command = base
            entry["command"] = command
            if isinstance(cfg.get("args"), list):
                entry["args"] = [rewrite_plugin_root_refs(a) for a in cfg["args"]]
            if isinstance(cfg.get("env"), dict):
                entry["env"] = {k: rewrite_plugin_root_refs(v) for k, v in cfg["env"].items()}
            if cfg.get("executor") in ("local", "kaos"):
                entry["executor"] = cfg["executor"]
            if root_ref_used and not cfg.get("cwd"):
                entry["cwd"] = "./"
                notes.append(f"mcpServers.{name} references plugin-root placeholder; added cwd ./")
            elif cfg.get("cwd"):
                cwd = rewrite_plugin_root_refs(cfg["cwd"])
                if cwd.startswith(".") and not cwd.startswith("./"):
                    cwd = "./" + cwd.lstrip("./")
                entry["cwd"] = cwd
        else:
            notes.append(f"mcpServers.{name} has neither url nor command; dropped")
            continue
        entry.update(sanitize_mcp_common_fields(cfg, name, notes))
        out[str(name)] = entry
    if not out:
        return None, "mcpServers is empty after conversion"
    return out, None


def serverjson_to_simple(data, notes):
    """Convert official MCP registry server.json -> intermediate simple-dialect dict (then built via simple shape).

    Mapping: name uses reverse-DNS tail (io.github.x/playwright-mcp -> playwright-mcp);
    packages[] (registryType npm/pypi, transport stdio) -> npx -y <pkg>@<ver> / uvx <pkg>==<ver>
    stdio server (runtimeArguments before package name, packageArguments after);
    remotes[] -> url server (streamable-http -> transport http);
    environmentVariables[{name}] -> env {NAME: "${NAME}"} (dependency check notes required env vars)."""
    out = {}
    raw_name = str(data.get("name") or "")
    out["name"] = raw_name.rsplit("/", 1)[-1] or raw_name
    if isinstance(data.get("description"), str):
        out["description"] = data["description"]
    if data.get("version") is not None:
        out["version"] = str(data["version"])
    repo = data.get("repository")
    if isinstance(repo, dict) and isinstance(repo.get("url"), str):
        out["homepage"] = repo["url"]

    def arg_values(items):
        vals = []
        for a in items or []:
            if isinstance(a, dict):
                if a.get("type") == "named" and a.get("name"):
                    vals.append(str(a["name"]))
                    if a.get("value") is not None:
                        vals.append(str(a["value"]))
                elif a.get("value") is not None:
                    vals.append(str(a["value"]))
            elif isinstance(a, str):
                vals.append(a)
        return vals

    def env_vars(items):
        env = {}
        for v in items or []:
            if isinstance(v, dict) and v.get("name"):
                env[str(v["name"])] = "${" + str(v["name"]) + "}"
        return env

    ordered = []  # (key suffix, mcpServers entry)
    for pkg in data.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        reg, ident = pkg.get("registryType"), pkg.get("identifier")
        if not ident:
            continue
        ident = str(ident)
        ver = str(pkg.get("version") or "")
        transport = (pkg.get("transport") or {}).get("type", "stdio")
        if transport != "stdio":
            notes.append(f"server.json packages entry has non-stdio transport={transport!r}; skipped")
            continue
        if reg == "npm":
            entry = {"command": "npx",
                     "args": (["-y"] + arg_values(pkg.get("runtimeArguments"))
                              + [f"{ident}@{ver}" if ver else ident]
                              + arg_values(pkg.get("packageArguments")))}
        elif reg == "pypi":
            entry = {"command": "uvx",
                     "args": (arg_values(pkg.get("runtimeArguments"))
                              + [f"{ident}=={ver}" if ver else ident]
                              + arg_values(pkg.get("packageArguments")))}
        else:
            notes.append(f"server.json packages entry has unsupported registryType={reg!r} (supported: npm/pypi); skipped")
            continue
        env = env_vars(pkg.get("environmentVariables"))
        if env:
            entry["env"] = env
        ordered.append((ident.rsplit("/", 1)[-1], entry))
    for remote in data.get("remotes") or []:
        if not isinstance(remote, dict) or not remote.get("url"):
            continue
        entry = {"url": str(remote["url"])}
        rtype = remote.get("type")
        if rtype in ("sse", "streamable-http"):
            entry["transport"] = "sse" if rtype == "sse" else "http"
        headers = {str(h["name"]): str(h.get("value", ""))
                   for h in remote.get("headers") or [] if isinstance(h, dict) and h.get("name")}
        if headers:
            entry["headers"] = headers
        ordered.append(("remote", entry))
    if not ordered:
        notes.append("server.json has no convertible packages/remotes entries")
        return out
    servers = {}
    for i, (tail, entry) in enumerate(ordered):
        key = out["name"] if i == 0 else f"{out['name']}-{tail}"
        servers[key] = entry
    out["mcpServers"] = servers
    return out


def convert_hooks(source_dir, raw, notes, ref_base=None):
    """Convert source-dialect hooks (array or path string to Claude-style hooks.json) -> Kimi [{event, command, timeout?}].
    String references resolve relative to manifest dir (ref_base, defaulting to plugin root)."""
    data = raw
    if isinstance(raw, str):
        ref = resolve_ref_in_source(source_dir, raw, base_dir=ref_base)
        if ref is None:
            return None, f"hooks referenced file does not exist: {raw}"
        try:
            data = pc.load_json(ref)
        except Exception as e:  # noqa: BLE001
            return None, f"hooks file is not valid JSON: {raw}: {e}"
        notes.append(f"Inlined hooks from {raw}")
    flat = []
    if isinstance(data, dict):
        groups = data.get("hooks", data)
        if isinstance(groups, (list, dict)) and not groups:
            return None, "hooks is empty; field omitted"
        if not isinstance(groups, dict):
            return None, "Unsupported hooks.json shape"
        for event, group_list in groups.items():
            if event not in KIMI_HOOK_EVENTS:
                notes.append(f"hook event {event!r} is unsupported by Kimi; dropped")
                continue
            if not isinstance(group_list, list):
                continue
            for group in group_list:
                inner = group.get("hooks") if isinstance(group, dict) else None
                if not isinstance(inner, list):
                    continue
                for h in inner:
                    if not isinstance(h, dict) or not h.get("command"):
                        continue
                    entry = {"event": event,
                             "command": rewrite_plugin_root_refs(h["command"])}
                    if isinstance(group, dict) and isinstance(group.get("matcher"), str) and group["matcher"]:
                        entry["matcher"] = group["matcher"]
                    t = h.get("timeout")
                    if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 600:
                        entry["timeout"] = int(t)
                    flat.append(entry)
    elif isinstance(data, list):
        for h in data:
            if not isinstance(h, dict):
                continue
            event = h.get("event")
            if event not in KIMI_HOOK_EVENTS:
                notes.append(f"hook event {event!r} is unsupported by Kimi; dropped")
                continue
            if not h.get("command"):
                notes.append(f"hook ({event}) missing command; entry dropped")
                continue
            entry = {"event": event, "command": rewrite_plugin_root_refs(h["command"])}
            if isinstance(h.get("matcher"), str) and h["matcher"]:
                entry["matcher"] = h["matcher"]
            t = h.get("timeout")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 600:
                entry["timeout"] = int(t)
            flat.append(entry)
    else:
        return None, f"Unsupported hooks shape: {type(data).__name__}"
    if not flat:
        return None, "hooks is empty after conversion"
    return flat, None


def normalize_path_list_field(raw, field, notes, dest=None, base_rel="", dropped_out=None):
    """skills/commands/agents: string or string array, uniformly prefixed with ./; returns None if empty
    (field omitted). When dest (output dir) is provided, verifies paths actually exist inside the plugin dir.
    Declared paths are resolved in two ways (both occur in real repos, e.g. a-team's ../skills/ vs
    atomlane's ./skills/): (1) relative to the manifest's directory (base_rel, e.g. ".codex-plugin");
    (2) relative to plugin root. Once resolved, rewritten to a plugin-root-relative ./ path (output
    manifest lives at plugin root). If both miss, tries a same-named fallback under plugin root
    (e.g. ../skills/ -> ./skills/; skills/agents require a directory target, and entries pointing to
    files are dropped as file-not-dir since root SKILL.md is relocated by relocate_root_skill_into_skills_dir).
    If fallback also misses, drops the entry with a note and appends field to dropped_out (so caller
    downgrades plugins with declared-but-missing content rather than auto-registering them)."""
    entries = raw if isinstance(raw, list) else [raw]
    out = []
    for e in entries:
        if not isinstance(e, str) or not e.strip():
            notes.append(f"Non-string entry in {field}; dropped: {e!r}")
            continue
        e = e.strip()
        if "\\" in e:
            # Normalize Windows-style backslash path separators to /
            e = e.replace("\\", "/")
            notes.append(f"Normalized backslashes to / in {field} path: {e}")
        if dest is not None:
            abs_dest = os.path.abspath(dest)
            rel = e[2:] if e.startswith("./") else e
            candidates = [os.path.normpath(os.path.join(abs_dest, base_rel, rel))]
            if base_rel:
                candidates.append(os.path.normpath(os.path.join(abs_dest, rel)))
            resolved = None
            for cand in candidates:
                inside = cand == abs_dest or cand.startswith(abs_dest + os.sep)
                if inside and os.path.exists(cand):
                    resolved = cand
                    break
            if resolved is not None and os.path.isfile(resolved) and field in ("skills", "agents"):
                # skills/agents must be directories; drop file entries (commonly ./SKILL.md) --
                # root SKILL.md will be relocated into skills/ by relocate_root_skill_into_skills_dir
                notes.append(f"{field} declares a file rather than a directory: {e!r}; dropped (root SKILL.md will be relocated into skills/)")
                continue
            if resolved is None:
                base = os.path.basename(rel.rstrip("/"))
                fallback = os.path.join(abs_dest, base)
                ok = (os.path.isdir(fallback) if field in ("skills", "agents")
                      else os.path.exists(fallback))
                if base and ok:
                    notes.append(f"{field} path {e!r} is out of bounds or missing; falling back to ./{base}/")
                    e = "./" + base + ("/" if e.endswith("/") else "")
                else:
                    notes.append(f"{field} path {e!r} does not exist in plugin directory; dropped")
                    if dropped_out is not None and field not in dropped_out:
                        dropped_out.append(field)
                    continue
            else:
                root_rel = os.path.relpath(resolved, abs_dest).replace(os.sep, "/")
                if root_rel == ".":
                    new_e = "./"
                else:
                    new_e = "./" + root_rel + ("/" if e.endswith("/") else "")
                if new_e != e:
                    notes.append(f"{field} path {e!r} resolved to {new_e}")
                e = new_e
        if not e.startswith("./"):
            e = "./" + e
            notes.append(f"Added ./ prefix to {field} path: {e}")
        out.append(e)
    if not out:
        return None
    return out if isinstance(raw, list) else out[0]


def ensure_md_frontmatter(md_path, default_name, default_description, notes, display_base=None):
    """Add missing frontmatter (or missing name/description fields) to SKILL.md / agent .md files --
    Kimi will not load skills/agents without frontmatter (validate_plugin.py reports ERROR).
    display_base: base dir for displaying paths in notes (output dir); defaults to basename."""
    try:
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:  # noqa: BLE001 — skip unreadable files; let validate report them
        return
    desc = re.sub(r"\s+", " ", (default_description or "")).strip() or f"{default_name} skill"
    # Display paths in notes relative to output dir (skills/<name>/SKILL.md): md_path and
    # display_base share the same prefix form (both with or without \\?\), avoiding mount errors
    # and ../.. noise relative to cwd
    disp = os.path.relpath(md_path, display_base) if display_base else os.path.basename(md_path)
    fm = re.match(r"^---\s*\n(.*?)\n---", content, re.S)
    if not fm:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\nname: {default_name}\ndescription: {desc}\n---\n\n" + content)
        notes.append(f"{os.path.basename(md_path)} missing frontmatter; added name/description ({disp})")
        return
    body = fm.group(1)
    add = ""
    if not re.search(r"^name:\s*\S+", body, re.M):
        add += f"name: {default_name}\n"
    if not re.search(r"^description:\s*\S+", body, re.M):
        add += f"description: {desc}\n"
    if add:
        new_fm = "---\n" + add + body + "\n---"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(new_fm + content[fm.end():])
        notes.append(f"{os.path.basename(md_path)} frontmatter missing fields; populated ({disp})")


def pick_icon_url(interface, notes):
    """Extract icon field from source-dialect interface (iconURL/icon/logo/logoURL, etc.) -> Kimi iconUrl (direct image URLs only)."""
    for key in ("iconURL", "iconUrl", "icon", "logoURL", "logo", "thumbnail"):
        url = interface.get(key)
        if isinstance(url, str) and re.match(r"https?://.+\.(png|jpg|jpeg|svg|webp|ico)(\?.*)?$", url, re.I):
            if key != "iconUrl":
                notes.append(f"Icon taken from interface.{key}")
            return url
    return None


# ---- Relocate root SKILL.md into standard skills/<name>/ layout ----
# Kimi's standard plugin skill layout is skills/<name>/SKILL.md (with manifest skills field).
# Relative paths inside a skill (references/, scripts/, etc.) resolve relative to SKILL.md's dir,
# so everything except root-level retained items moves alongside SKILL.md. Retained items:
# plugin semantic dirs (agents/commands/hooks referenced by manifest at root), plugin-level files
# (manifest, setup.sh, README/LICENSE, etc.), and paths referenced by manifest component fields.
ROOT_SKILL_KEEP_DIRS = {"agents", "commands", "hooks", "skills", ".kimi-plugin"}
# Personal market icon convention reads first matching root icon.<ext> (same as validate); must stay at root
ROOT_SKILL_KEEP_FILES = {"kimi.plugin.json", "setup.sh", ".mcp.json",
                         "icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp"}
ROOT_SKILL_KEEP_PREFIXES = ("readme", "license", "licence", "changelog", "notice")


def _manifest_ref_top_tokens(kimi):
    """Top-level path segments relatively referenced by manifest component fields
    (mcpServers/hooks/systemPromptPath, with or without ./ prefix). These must stay at plugin root
    when relocating root SKILL.md so component references do not break. Command names (python3),
    URL fragments, flags, env placeholders, etc. may also appear but will not match real root entries."""
    tokens = set()

    def collect(value):
        if isinstance(value, str):
            m = re.match(r"^(?:\./)?([^/]+)", value.strip())
            if m:
                tokens.add(m.group(1))
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    for field in ("mcpServers", "hooks", "systemPromptPath"):
        if field in kimi:
            collect(kimi[field])
    return tokens


def _root_skill_body_root_refs(skill_md_path):
    """Set of top-level relative path tokens referenced in root SKILL.md body: markdown link
    targets [..](..) and ./ prefixed tokens. Used to warn when relocating that references to
    retained root items will break (warning only; user docs are not rewritten)."""
    try:
        with open(skill_md_path, encoding="utf-8") as f:
            body = f.read()
    except Exception:  # noqa: BLE001 — skip warning if unreadable; continue relocation
        return set()
    tokens = set()

    def add(path):
        path = path.strip()
        if not path or path.startswith(("#", "http://", "https://", "mailto:")):
            return
        if path.startswith("./"):
            path = path[2:]
        top = path.split("/", 1)[0].strip()
        if top and top not in (".", ".."):
            tokens.add(top)

    for m in re.finditer(r"\]\(\s*([^)\s]+)", body):
        add(m.group(1))
    for m in re.finditer(r"(?<![\w/.-])\./([^\s)\]\"'<]+)", body):
        add("./" + m.group(1))
    return tokens


def _root_skill_frontmatter_name(skill_md_path):
    """Read name from root SKILL.md frontmatter: handles double-quoted, single-quoted, and bare YAML
    scalars; returns unquoted name or None if unreadable/missing."""
    try:
        with open(skill_md_path, encoding="utf-8") as f:
            head = f.read(65536)
    except Exception:  # noqa: BLE001 — skip unreadable files; let validate report them
        return None
    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
    if not fm:
        return None
    m = re.search(r"^name:\s*(?:\"([^\"]+)\"|'([^']+)'|(.+?))\s*$", fm.group(1), re.M)
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _safe_skill_dir_name(name):
    """Skill directory name: kept identical to SKILL.md frontmatter name unless it contains path
    separators or Windows reserved chars (or is empty / starts with '.'), in which case it is
    normalized with hyphens and the difference is logged by the caller."""
    n = name.strip()
    if n and not n.startswith(".") and not re.search(r'[/\\:*?"<>|]', n):
        return n
    safe = re.sub(r'[/\\:*?"<>|\s]+', "-", n).strip("-.")
    return safe or "skill"


def relocate_root_skill_into_skills_dir(kimi, dest_fs, fallback_name, notes):
    """Relocate a root SKILL.md fallback layout into standard skills/<name>/ layout and add
    skills: "./skills/". Triggered when: manifest has no skills field, root has SKILL.md, no
    skills/ dir exists (root SKILL.md has no effect if skills/ exists), and no sessionStart
    (which validates skill name against dir name). Directory name matches SKILL.md frontmatter
    name, falling back to plugin name (fix_frontmatter_for_manifest will then match it). Everything
    except root-level retained items moves alongside SKILL.md. Returns True if relocated."""
    if "skills" in kimi or "sessionStart" in kimi:
        return False
    root_skill = os.path.join(dest_fs, "SKILL.md")
    if not os.path.isfile(root_skill):
        return False
    skills_path = os.path.join(dest_fs, "skills")
    if os.path.lexists(skills_path):
        # skills/ dir already exists (root SKILL.md has no effect), or "skills" is occupied by a
        # regular file / broken symlink (no safe destination; makedirs would raise) -- keep as-is
        if not os.path.isdir(skills_path):
            notes.append("Non-directory entry 'skills' exists at root; skipped relocation, keeping root SKILL.md as-is")
        return False
    skill_dir_name = fallback_name
    fm_name = _root_skill_frontmatter_name(root_skill)
    if fm_name:
        skill_dir_name = _safe_skill_dir_name(fm_name)
        if skill_dir_name != fm_name:
            notes.append(f"Skill name {fm_name!r} contains path-reserved chars; directory name normalized to {skill_dir_name!r}")
    keep = (set(ROOT_SKILL_KEEP_DIRS) | set(ROOT_SKILL_KEEP_FILES)
            | _manifest_ref_top_tokens(kimi))
    broken = sorted(_root_skill_body_root_refs(root_skill) & keep)
    if broken:
        notes.append(f"Warning: relative references in SKILL.md body to retained root items will break after relocation "
                     f"({', '.join(broken)}; after moving SKILL.md into skills/{skill_dir_name}/ "
                     "these links will not resolve); manually update to ../ or move the target files")
    target = os.path.join(dest_fs, "skills", skill_dir_name)
    os.makedirs(target)
    moved = []
    for entry in sorted(os.listdir(dest_fs)):
        if entry in keep or entry.lower().startswith(ROOT_SKILL_KEEP_PREFIXES):
            continue
        shutil.move(os.path.join(dest_fs, entry), os.path.join(target, entry))
        moved.append(entry)
    kimi["skills"] = "./skills/"
    notes.append(f"Relocated root SKILL.md into standard layout skills/{skill_dir_name}/ "
                 f"(moved {len(moved)} items: {', '.join(moved)}); added skills: ./skills/")
    return True


# ---------- Single-plugin conversion ----------
def _note_guard_skips(guard, notes):
    """Record items skipped by source_guard pre-scan in report notes for transparency."""
    skipped = (guard or {}).get("skipped_sensitive") or []
    if skipped:
        notes.append("Skipped %d suspected credential/key file(s) (not packaged): %s%s"
                     % (len(skipped), ", ".join(skipped[:10]),
                        " ..." if len(skipped) > 10 else ""))
    cred_dirs = (guard or {}).get("excluded_credential_dirs") or []
    if cred_dirs:
        notes.append("Excluded credential director(ies) (not packaged): " + ", ".join(cred_dirs[:10]))


def fix_frontmatter_for_manifest(kimi, dest, name, description, notes):
    """Populate missing frontmatter: Kimi will not load skills/agents without frontmatter (common
    defect in source repos, fixed automatically during conversion)."""
    raw_skills = kimi.get("skills")
    for sd in (raw_skills if isinstance(raw_skills, list) else [raw_skills]):
        if not isinstance(sd, str):
            continue
        # normpath normalizes trailing/forward slashes: dest may have \\?\ prefix (Windows long path),
        # which skips slash normalization and would otherwise misreport paths with "/" as missing
        sd_abs = os.path.normpath(os.path.join(dest, sd[2:] if sd.startswith("./") else sd))
        if os.path.isdir(sd_abs):
            for d in sorted(os.listdir(sd_abs)):
                smd = os.path.join(sd_abs, d, "SKILL.md")
                if os.path.isfile(smd):
                    ensure_md_frontmatter(smd, d, description, notes, display_base=dest)
    if "skills" not in kimi and os.path.isfile(os.path.join(dest, "SKILL.md")):
        ensure_md_frontmatter(os.path.join(dest, "SKILL.md"), name, description, notes,
                              display_base=dest)
    raw_agents = kimi.get("agents")
    for ad in (raw_agents if isinstance(raw_agents, list) else [raw_agents]):
        if not isinstance(ad, str):
            continue
        ad_abs = os.path.normpath(os.path.join(dest, ad[2:] if ad.startswith("./") else ad))
        if os.path.isdir(ad_abs):
            for f_ in sorted(os.listdir(ad_abs)):
                amd = os.path.join(ad_abs, f_)
                if f_.endswith(".md") and os.path.isfile(amd):
                    ensure_md_frontmatter(amd, os.path.splitext(f_)[0], description, notes,
                                          display_base=dest)


def enforce_system_prompt_limits(kimi, dest, notes):
    """Enforce 32 KB limit on systemPrompt / systemPromptPath (runtime ignores oversized prompts; catch early)."""
    sp = kimi.get("systemPrompt")
    if isinstance(sp, str) and len(sp.encode("utf-8")) > SYSTEM_PROMPT_MAX_BYTES:
        notes.append(f"systemPrompt exceeds 32 KB ({len(sp.encode('utf-8'))} bytes); dropped")
        del kimi["systemPrompt"]
    spp = kimi.get("systemPromptPath")
    if isinstance(spp, str) and spp.startswith("./"):
        f = os.path.normpath(os.path.join(dest, spp[2:]))
        if os.path.isfile(f) and os.path.getsize(f) > SYSTEM_PROMPT_MAX_BYTES:
            notes.append(f"File referenced by systemPromptPath exceeds 32 KB ({os.path.getsize(f)} bytes); field dropped")
            del kimi["systemPromptPath"]


def sanitize_kimi_components(kimi, dest, notes, dropped_out=None):
    """Sanitize component fields of a kimi-dialect manifest (already in native project format):
    prefix skills/agents/commands paths with ./ and verify existence; keep only {skill} in
    sessionStart; keep only event/command/matcher/timeout in hooks with valid Kimi events; filter
    mcpServers entries by key allowlist requiring url or command. Invalid entries/keys are dropped
    with notes; fields dropped due to missing content are appended to dropped_out."""
    for field in ("skills", "agents", "commands"):
        if field in kimi:
            value = normalize_path_list_field(kimi[field], field, notes, dest,
                                              dropped_out=dropped_out)
            if value:
                kimi[field] = value
            else:
                notes.append(f"{field} is empty after sanitization; field omitted")
                del kimi[field]
    if "sessionStart" in kimi:
        ss = kimi["sessionStart"]
        if isinstance(ss, dict) and isinstance(ss.get("skill"), str) and ss["skill"].strip():
            kimi["sessionStart"] = {"skill": ss["skill"].strip()}
        else:
            notes.append(f"Invalid sessionStart shape (expected {{'skill': '<name>'}}); dropped: {ss!r}")
            del kimi["sessionStart"]
    if "hooks" in kimi:
        raw = kimi["hooks"]
        if not isinstance(raw, list):
            notes.append(f"hooks must be an array; dropped: {type(raw).__name__}")
            del kimi["hooks"]
        else:
            clean = []
            for h in raw:
                if not isinstance(h, dict):
                    notes.append(f"hooks entry is not an object; dropped: {h!r}")
                    continue
                extra = [k for k in h if k not in ("event", "command", "matcher", "timeout")]
                if extra:
                    notes.append("hooks entry contains non-spec keys; dropped: " + ", ".join(extra))
                event = h.get("event")
                if event not in KIMI_HOOK_EVENTS:
                    notes.append(f"hook event {event!r} is unsupported by Kimi; entry dropped")
                    continue
                command = h.get("command")
                if not (isinstance(command, str) and command.strip()):
                    notes.append(f"hook ({event}) missing/empty command; entry dropped")
                    continue
                entry = {"event": event, "command": command}
                if isinstance(h.get("matcher"), str) and h["matcher"]:
                    entry["matcher"] = h["matcher"]
                t = h.get("timeout")
                if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 600:
                    entry["timeout"] = t
                elif t is not None:
                    notes.append(f"hook ({event}) invalid timeout (must be int 1-600); key dropped")
                clean.append(entry)
            if clean:
                kimi["hooks"] = clean
            else:
                notes.append("hooks is empty after sanitization; field omitted")
                del kimi["hooks"]
    if "mcpServers" in kimi:
        raw = kimi["mcpServers"]
        if not isinstance(raw, dict):
            notes.append(f"mcpServers must be an object; dropped: {type(raw).__name__}")
            del kimi["mcpServers"]
        else:
            clean = {}
            for sname, cfg in raw.items():
                if not isinstance(cfg, dict):
                    notes.append(f"mcpServers.{sname} is not an object; dropped")
                    continue
                if cfg.get("url"):
                    allowed = set(MCP_REMOTE_KEYS) | set(MCP_COMMON_KEYS)
                elif cfg.get("command"):
                    allowed = set(MCP_STDIO_KEYS) | set(MCP_COMMON_KEYS)
                else:
                    notes.append(f"mcpServers.{sname} has neither url nor command; dropped")
                    continue
                extra = [k for k in cfg if k not in allowed]
                if extra:
                    notes.append(f"mcpServers.{sname} contains non-spec keys; dropped: " + ", ".join(extra))
                entry = {k: v for k, v in cfg.items() if k in allowed}
                common = sanitize_mcp_common_fields(cfg, sname, notes)
                for k in set(entry) & set(MCP_COMMON_KEYS):
                    del entry[k]
                entry.update(common)
                clean[str(sname)] = entry
            if clean:
                kimi["mcpServers"] = clean
            else:
                notes.append("mcpServers is empty after sanitization; field omitted")
                del kimi["mcpServers"]


def convert_one(source_dir, name_hint, index_entry, output_dir, used_names, repo_root=None,
                forced_manifest=None, used_names_lock=None):
    """Convert a single plugin. Returns a report entry dict (with success/failure and reason).
    repo_root: root of the repository containing the plugin (__REPO_ROOT__ references in monorepo
    subdirectory plugins resolve against this, and referenced shared sources are copied into output).
    forced_manifest: (rel_path, dialect), used by blob strict entry to skip platform detection.
    When multiple platform manifests coexist, the first dialect in MANIFEST_CANDIDATES order is
    selected automatically and skipped dialects are logged in notes (never silent).
    used_names_lock: lock protecting the check-and-claim critical section on used_names during
    parallel conversion (None when sequential)."""
    notes = []
    entry = {"source_dir": source_dir, "origin": None, "success": False,
             "notes": notes, "kimi_name": None, "output_dir": None, "error": None}
    if index_entry:
        entry["origin"] = _entry_origin(index_entry)
        entry["index_name"] = index_entry.get("name")

    if forced_manifest:
        # blob strict entry: use the specified manifest (with dialect) directly, skipping platform detection
        manifest_rel, dialect = forced_manifest
    else:
        found = detect_all_manifests(source_dir)
        platforms = list(dict.fromkeys(d for _rel, d in found if d in PLATFORM_DIALECTS))
        if len(platforms) > 1:
            # Multiple platform definitions coexist: auto-select first dialect by MANIFEST_CANDIDATES
            # priority and log skipped dialects in report (visible/traceable without interrupting batch)
            manifest_rel, dialect = found[0]
            notes.append(
                f"Multiple platform definitions coexist ({', '.join(platforms)}); auto-converting as {dialect} "
                f"(skipped: {', '.join(platforms[1:])})")
        else:
            manifest_rel, dialect = found[0] if found else (None, None)
    codex = {}
    coll_skills = (index_entry or {}).get("skills") if index_entry else None
    # Output paths can exceed 260 chars (output prefix + market/plugin + deep plugin subdirs),
    # so file operations on Windows always use \\?\ long-path prefix (pc.winlong in plugin_common);
    # keep clean paths in reports and entry dicts
    source_dir_fs = pc.winlong(source_dir)
    # Manifest directory (e.g. ".codex-plugin"): relative paths in source manifest resolve from here;
    # output manifest sits at plugin root, so paths are rewritten to plugin-root-relative ./ paths
    manifest_dir_abs = (os.path.normpath(os.path.join(source_dir_fs, os.path.dirname(manifest_rel)))
                        if manifest_rel else source_dir_fs)
    manifest_dir_rel = os.path.dirname(manifest_rel) if manifest_rel else ""
    dropped_components = []  # component fields declared but missing in source (downgrade basis; see report dropped_components)
    if manifest_rel:
        try:
            codex = pc.load_json(os.path.join(source_dir_fs, manifest_rel))
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{manifest_rel} is not valid JSON: {e}"
            return entry
        if dialect == "kimi":
            shape = "kimi"       # already native project format: light conversion
        elif dialect == "codex":
            shape = "codex"      # Codex dialect: full conversion
        elif dialect in ("claude", "codebuddy", "cursor"):
            shape = "simple"     # Claude / CodeBuddy / Cursor dialect: full conversion (no interface to map)
        elif dialect == "gemini":
            shape = "simple"     # Gemini extension dialect: mcpServers isomorphic (${extensionPath}
            # already rewritten as plugin-root placeholder); contextFileName -> systemPromptPath
            cfn = codex.pop("contextFileName", None)
            if isinstance(cfn, str) and cfn.strip():
                codex["systemPromptPath"] = "./" + rewrite_plugin_root_refs(cfn.strip()).lstrip("./")
                notes.append(f"Mapped contextFileName {cfn!r} to systemPromptPath")
        elif dialect == "serverjson":
            shape = "simple"     # Official MCP registry server.json -> wrap as mcpServers-only plugin
            codex = serverjson_to_simple(codex, notes)
        else:  # generic: treat as Codex dialect if interface dict is present, else simple dialect
            shape = "codex" if isinstance(codex.get("interface"), dict) else "simple"
    elif isinstance(coll_skills, list) and coll_skills:
        # Claude marketplace skill-collection entry (e.g. anthropics/skills: {"source": "./",
        # "skills": ["./skills/x", ...]}): no manifest; assemble skill-only plugin from listed dirs
        # without copying the whole repo
        dialect = "skill-collection"
        shape = "simple"
        notes.append("Index entry is a skill collection (skills array); assembling skill-only plugin from array")
    elif not (os.path.isfile(os.path.join(source_dir_fs, "SKILL.md"))
              or os.path.isdir(os.path.join(source_dir_fs, "skills"))):
        entry["error"] = "No recognized plugin manifest found, and no SKILL.md/skills/ for fallback"
        return entry
    else:
        dialect = "skill-only"
        shape = "simple"
        notes.append("No plugin manifest; wrapping with skill-only fallback")
    entry["dialect"] = dialect

    # Rights restriction scan (pre-packaging/registration; conversion still completes):
    # license-context restriction phrase hit -> severity=high; vendor/platform signal ->
    # severity=notice; both go to registration_plan's hold_rights_review bucket for user review
    # and release via register_converted.py --only. Dialect manifests (e.g. .codex-plugin/plugin.json)
    # are outside top-level filename scanning, so pass their license field as extra license context.
    try:
        extra_license = []
        if isinstance(codex.get("license"), str) and codex.get("license").strip():
            extra_license.append((manifest_rel or "manifest", "manifest-license",
                                  codex["license"]))
        rights = rs.scan_rights(source_dir_fs, extra_license=extra_license)
        entry["rights"] = {"severity": rs.severity_of(rights),
                           "restricted": rights["restricted"],
                           "vendor": rights["vendor"],
                           "warnings": rights["warnings"]}
        if entry["rights"]["severity"]:
            notes.append(f"Rights scan hit (severity={entry['rights']['severity']}): "
                         "registration held; review plugin-builder-report.md and release with --only")
        for w in rights["warnings"]:
            notes.append(f"Rights scan warning (README body hit, advisory only): "
                         f"{w['category']} @ {w['file']}")
    except Exception as e:  # noqa: BLE001 — scanner errors do not block conversion; log and continue
        notes.append(f"Rights scan error (continuing as no hit): {type(e).__name__}: {e}")

    raw_name = str(codex.get("name") or name_hint or (index_entry or {}).get("name")
                   or os.path.basename(source_dir))
    try:
        name = pc.validate_plugin_name(pc.normalize_plugin_name(raw_name))
    except ValueError as e:
        entry["error"] = f"Invalid plugin name: {e}"
        return entry
    if name != raw_name:
        notes.append(f"Normalized name {raw_name!r} to {name!r}")
    def claim_name(n):
        """Check-and-claim must be atomic; protected by used_names_lock during parallel conversion."""
        base, i = n, 2
        while n in used_names:
            n = f"{base}-{i}"
            i += 1
        if n != base:
            notes.append(f"Duplicate name; renamed to {n}")
        used_names.add(n)
        return n

    if used_names_lock is None:
        name = claim_name(name)
    else:
        with used_names_lock:
            name = claim_name(name)
    entry["kimi_name"] = name

    dest = os.path.join(output_dir, name)
    dest_fs = pc.winlong(dest)
    if os.path.exists(dest_fs):
        shutil.rmtree(dest_fs)
    if dialect == "skill-collection":
        # Skill-collection entry: copy only skill dirs listed in skills array to dest/skills/<name>/
        os.makedirs(dest_fs)
        copied = 0
        for sd in coll_skills:
            if not isinstance(sd, str) or not sd.strip():
                continue
            src = os.path.normpath(os.path.join(source_dir_fs, sd))
            if not os.path.isdir(src):
                notes.append(f"Directory in skills array does not exist; skipped: {sd}")
                continue
            try:
                guard = sg.check_source_tree(src)
            except sg.SourceGuardError as e:
                entry["error"] = f"Skill directory {sd} failed security check: {e}"
                return entry
            _note_guard_skips(guard, notes)
            shutil.copytree(src, os.path.join(dest_fs, "skills", os.path.basename(src)),
                            ignore=sg.make_copy_ignorer(COPY_EXCLUDE_DIRS, guard))
            copied += 1
        if not copied:
            entry["error"] = "None of the skill directories in the skills array exist; cannot assemble"
            return entry
        notes.append(f"Assembled {copied} skill directories from index skills array")
    else:
        try:
            guard = sg.check_source_tree(source_dir_fs)
        except sg.SourceGuardError as e:
            entry["error"] = f"Source directory failed security check: {e}"
            return entry
        _note_guard_skips(guard, notes)
        shutil.copytree(source_dir_fs, dest_fs,
                        ignore=sg.make_copy_ignorer(copy_excludes(dialect), guard))
    entry["output_dir"] = dest

    # Monorepo subdirectory plugin: copy shared repo-level sources referenced via __REPO_ROOT__
    # (e.g. root mcp/ dir) into plugin output so referenced scripts exist at runtime
    if repo_root and os.path.abspath(repo_root) != os.path.abspath(source_dir):
        repo_root_fs = pc.winlong(repo_root)
        ref_datas = [codex]
        for field in ("mcpServers", "hooks"):
            raw = codex.get(field)
            if isinstance(raw, str):
                ref = resolve_ref_in_source(source_dir_fs, raw)
                if ref:
                    try:
                        ref_datas.append(pc.load_json(ref))
                    except Exception:  # noqa: BLE001
                        pass
        dot_mcp_src = os.path.join(source_dir_fs, ".mcp.json")
        if "mcpServers" not in codex and os.path.isfile(dot_mcp_src):
            try:
                ref_datas.append(pc.load_json(dot_mcp_src))
            except Exception:  # noqa: BLE001
                pass
        copied_tops = set()
        for data in ref_datas:
            for rel, scope in _plugin_rel_tokens(data):
                if scope != "repo" or not rel or rel.startswith(".."):
                    continue
                top = rel.split("/")[0]
                if top in copied_tops:
                    continue
                src_p = os.path.join(repo_root_fs, top)
                dst_p = os.path.join(dest_fs, top)
                if not os.path.exists(src_p) or os.path.exists(dst_p):
                    continue
                if os.path.isdir(src_p):
                    try:
                        top_guard = sg.check_source_tree(src_p)
                    except sg.SourceGuardError as e:
                        notes.append(f"Shared repo-level source {top} failed security check; not copied: {e}")
                        continue
                    _note_guard_skips(top_guard, notes)
                    shutil.copytree(src_p, dst_p,
                                    ignore=sg.make_copy_ignorer(COPY_EXCLUDE_DIRS, top_guard))
                else:
                    if sg.is_sensitive_file(top):
                        notes.append(f"Shared repo-level source {top} matched sensitive credential/key rule; not copied")
                        continue
                    shutil.copy2(src_p, dst_p)
                copied_tops.add(top)
                notes.append(f"Copied shared repo-level source {top} from repo root (__REPO_ROOT__ reference)")

    if shape == "kimi":
        # Already native project format: light conversion -- filter by spec field allowlist
        # (non-spec fields dropped with notes) and normalize name/version/interface.category
        dropped = [k for k in codex if k not in SPEC_TOP_FIELDS]
        if dropped:
            notes.append("Dropped non-spec top-level fields: " + ", ".join(sorted(dropped)))
        kimi = {k: v for k, v in codex.items() if k in SPEC_TOP_FIELDS}
        if not str(kimi.get("license") or "").strip():
            # Honest license tagging: mark UNKNOWN if undetectable from external source; never default to MIT
            kimi["license"] = rs.detect_license(source_dir_fs)
            notes.append(f"Missing license; populated from LICENSE file detection: {kimi['license']} "
                         "(UNKNOWN if undetectable, never defaulting to MIT)")
        if str(kimi.get("name", "")).strip() != name:
            notes.append(f"Normalized manifest name {kimi.get('name')!r} to {name!r}")
        kimi["name"] = name
        version = str(kimi.get("version") or "").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", version):
            notes.append(f"version {version!r} missing or non-standard semver; falling back to 0.1.0")
            kimi["version"] = "0.1.0"
        itf = kimi.get("interface")
        if not isinstance(itf, dict):
            itf = {}
            notes.append("Added interface field")
        else:
            dropped_itf = [k for k in itf if k not in SPEC_INTERFACE_FIELDS]
            if dropped_itf:
                notes.append("Dropped non-spec interface fields: " + ", ".join(sorted(dropped_itf)))
            itf = {k: v for k, v in itf.items() if k in SPEC_INTERFACE_FIELDS}
        kimi["interface"] = itf
        if itf.get("hostKind") is not None and itf["hostKind"] not in SPEC_HOST_KINDS:
            notes.append(f"interface.hostKind {itf['hostKind']!r} is invalid (allowed: hosted/local); dropped")
            del itf["hostKind"]
        if itf.get("platforms") is not None:
            if (isinstance(itf["platforms"], list)
                    and all(p in SPEC_PLATFORMS for p in itf["platforms"])):
                pass
            else:
                notes.append(f"interface.platforms contains invalid platform (allowed: {'/'.join(SPEC_PLATFORMS)}); dropped")
                del itf["platforms"]
        if not itf.get("category"):
            itf["category"] = map_category((index_entry or {}).get("category"), notes)
            notes.append(f"Added interface.category (required by Kimi market): {itf['category']}")
        elif str(itf["category"]).upper() not in pc.CATEGORIES:
            itf["category"] = map_category(itf["category"], notes)
        sanitize_kimi_components(kimi, dest_fs, notes, dropped_out=dropped_components)
        relocate_root_skill_into_skills_dir(kimi, dest_fs, name, notes)
        enforce_system_prompt_limits(kimi, dest_fs, notes)
        fix_frontmatter_for_manifest(kimi, dest_fs, name,
                                     kimi.get("description") or name, notes)
        pc.write_json(os.path.join(dest_fs, "kimi.plugin.json"), kimi, force=True)
        components = [k for k in ("skills", "mcpServers", "hooks", "commands", "agents",
                                  "sessionStart", "systemPrompt", "systemPromptPath") if k in kimi]
        entry["components"] = components
        entry["license"] = str(kimi.get("license") or "")
        if dropped_components:
            # Manifest declared components missing in source dir -- visible in report, downgraded
            # to unusable during dependency check
            entry["dropped_components"] = sorted(dropped_components)
        hook_details = _extract_hook_details(kimi)
        if hook_details:
            entry["hooks"] = hook_details
        if os.path.isfile(os.path.join(dest_fs, "setup.sh")):
            entry["setup_sh"] = True
        entry["success"] = True
        notes.append("Already in Kimi format; processed with light conversion (required fields normalized only)")
        return entry

    interface = codex.get("interface") if isinstance(codex.get("interface"), dict) else {}
    display = interface.get("displayName") or (index_entry or {}).get("name") \
        or pc.display_name_from_plugin_name(name)
    description = codex.get("description") or (index_entry or {}).get("description") \
        or f"{display} plugin"

    version = str(codex.get("version") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", version):
        if version:
            notes.append(f"version {version!r} is non-standard semver; falling back to 0.1.0")
        version = "0.1.0"

    author = codex.get("author")
    if isinstance(author, dict):
        author = author.get("name") or author.get("email") or "Local developer"
    if not isinstance(author, str) or not author.strip():
        author = (index_entry or {}).get("owner") or "Local developer"

    keywords = codex.get("keywords")
    if not (isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)):
        keywords = []

    src_license = codex.get("license")
    if isinstance(src_license, str) and src_license.strip():
        license_ = src_license.strip()
    else:
        # Honest license tagging: mark UNKNOWN if undetectable from external source; never default to MIT
        license_ = rs.detect_license(source_dir_fs)
        notes.append(f"Manifest did not declare license; tagged from LICENSE file detection: {license_} "
                     "(UNKNOWN if undetectable, never defaulting to MIT)")

    kimi = {
        "$schema": pc.SCHEMA,
        "name": name,
        "version": version,
        "description": description,
        "keywords": keywords,
        "author": author,
        "license": license_,
    }
    homepage = codex.get("homepage") or (index_entry or {}).get("url")
    if isinstance(homepage, str) and homepage.strip():
        kimi["homepage"] = homepage.strip()

    kimi_interface = {
        "displayName": display,
        "shortDescription": interface.get("shortDescription") or description[:120],
        "longDescription": interface.get("longDescription") or description,
        "developerName": interface.get("developerName") or author,
        "category": map_category(interface.get("category") or (index_entry or {}).get("category"), notes),
    }
    website = interface.get("websiteURL") or homepage
    if isinstance(website, str) and website.strip():
        kimi_interface["websiteURL"] = website.strip()
    icon = pick_icon_url(interface, notes)
    if icon:
        kimi_interface["iconUrl"] = icon
    kimi["interface"] = kimi_interface

    # skills: explicit field takes precedence; otherwise add ./skills/ if skills/ dir exists
    if "skills" in codex:
        skills = normalize_path_list_field(codex["skills"], "skills", notes, dest_fs,
                                           base_rel=manifest_dir_rel,
                                           dropped_out=dropped_components)
        if skills:
            kimi["skills"] = skills
    elif os.path.isdir(os.path.join(dest_fs, "skills")):
        kimi["skills"] = "./skills/"
        notes.append("Added skills field ./skills/")
    # Root SKILL.md (without skills declaration) no longer relies on Kimi root fallback;
    # relocated into standard skills/<name>/ layout by relocate_root_skill_into_skills_dir

    def apply_mcp(servers):
        kimi["mcpServers"] = servers
        if any("command" in s for s in servers.values()):
            kimi_interface["hostKind"] = "local"
            kimi_interface["platforms"] = ["macos", "linux", "windows"]

    if "mcpServers" in codex:
        servers, reason = convert_mcp_servers(source_dir_fs, codex["mcpServers"], notes,
                                              ref_base=manifest_dir_abs)
        if servers:
            apply_mcp(servers)
        else:
            notes.append(f"mcpServers not converted: {reason}")
            if reason and "referenced file does not exist" in reason:
                dropped_components.append("mcpServers")
    else:
        # Common convention: manifest omits mcpServers, but a root MCP config file exists -- inline it.
        # Prefer .mcp.json (dot-prefixed, existing convention), then mcp.json (cursor/workbuddy style)
        for mcp_name in (".mcp.json", "mcp.json"):
            mcp_file = os.path.join(source_dir_fs, mcp_name)
            if not os.path.isfile(mcp_file):
                continue
            servers, reason = convert_mcp_servers(source_dir_fs, "./" + mcp_name, notes)
            if servers:
                apply_mcp(servers)
                notes.append(f"Manifest did not declare mcpServers; inlined from root {mcp_name}")
            elif reason:
                notes.append(f"Failed to inline {mcp_name}: {reason}")
            break

    if "hooks" in codex:
        hooks, reason = convert_hooks(source_dir_fs, codex["hooks"], notes,
                                      ref_base=manifest_dir_abs)
        if hooks:
            kimi["hooks"] = hooks
        else:
            notes.append(f"hooks not converted: {reason}")
            if reason and "referenced file does not exist" in reason:
                dropped_components.append("hooks")
    elif shape == "simple" and os.path.isfile(os.path.join(source_dir_fs, "hooks", "hooks.json")):
        # Claude convention: manifest omits hooks, but hooks/hooks.json exists -- inline it
        hooks, reason = convert_hooks(source_dir_fs, "./hooks/hooks.json", notes)
        if hooks:
            kimi["hooks"] = hooks
            notes.append("hooks not declared; inlined from ./hooks/hooks.json per Claude convention")
        else:
            notes.append(f"hooks not converted: {reason}")

    for field in ("commands", "agents"):
        if field in codex:
            value = normalize_path_list_field(codex[field], field, notes, dest_fs,
                                              base_rel=manifest_dir_rel,
                                              dropped_out=dropped_components)
            if value:
                kimi[field] = value
        elif os.path.isdir(os.path.join(dest_fs, field)):
            # Do not add field if dir has no .md files (source-platform configs like agents/openai.yaml;
            # Kimi only reads .md, and adding it would fail validate). agents checks top-level only
            # (matching validate); commands scans recursively.
            d = os.path.join(dest_fs, field)
            if field == "agents":
                has_md = any(f.endswith(".md") and os.path.isfile(os.path.join(d, f))
                             for f in os.listdir(d))
            else:
                has_md = any(f.endswith(".md") for _dp, _dn, fns in os.walk(d) for f in fns)
            if has_md:
                kimi[field] = f"./{field}/"
                notes.append(f"Added {field} field ./{field}/")
            else:
                notes.append(f"{field}/ directory contains no .md files (source-platform config); omitted {field} field")

    if isinstance(codex.get("sessionStart"), dict) and codex["sessionStart"].get("skill"):
        kimi["sessionStart"] = {"skill": str(codex["sessionStart"]["skill"])}
    if isinstance(codex.get("systemPrompt"), str) and codex["systemPrompt"].strip():
        kimi["systemPrompt"] = codex["systemPrompt"]
    if isinstance(codex.get("systemPromptPath"), str) and codex["systemPromptPath"].strip():
        spp = codex["systemPromptPath"].strip()
        kimi["systemPromptPath"] = spp if spp.startswith("./") else "./" + spp

    dropped_top = [f for f in CODEX_ONLY_TOP_FIELDS if f in codex]
    dropped_itf = [f for f in CODEX_ONLY_INTERFACE_FIELDS if f in interface]
    if dropped_top:
        notes.append("Dropped source-dialect-specific top-level fields: " + ", ".join(dropped_top))
    if dropped_itf:
        notes.append("Dropped source-dialect-specific interface fields: " + ", ".join(dropped_itf))

    relocate_root_skill_into_skills_dir(kimi, dest_fs, name, notes)
    enforce_system_prompt_limits(kimi, dest_fs, notes)
    fix_frontmatter_for_manifest(kimi, dest_fs, name, description, notes)

    pc.write_json(os.path.join(dest_fs, "kimi.plugin.json"), kimi, force=True)

    components = [k for k in ("skills", "mcpServers", "hooks", "commands", "agents",
                              "sessionStart", "systemPrompt", "systemPromptPath") if k in kimi]
    entry["components"] = components
    entry["license"] = str(kimi.get("license") or "")
    if dropped_components:
        # Manifest declared components missing in source dir (incomplete mirror/repo) --
        # visible in report and downgraded to unusable during dependency check (not auto-registered)
        entry["dropped_components"] = sorted(dropped_components)
    hook_details = _extract_hook_details(kimi)
    if hook_details:
        entry["hooks"] = hook_details
    if os.path.isfile(os.path.join(dest_fs, "setup.sh")):
        entry["setup_sh"] = True
    entry["success"] = True
    return entry


# ---------- Reporting ----------
def _extract_hook_details(kimi):
    """Extract report details [{event, command}] from output manifest hooks; returns None if absent."""
    hooks = kimi.get("hooks")
    if not isinstance(hooks, list):
        return None
    detail = [{"event": str(h.get("event", "")), "command": str(h.get("command", ""))}
              for h in hooks if isinstance(h, dict) and h.get("command")]
    return detail or None


REPORT_MD_NAME = "plugin-builder-report.md"


def render_plugin_builder_report(summary, registration=None):
    """Render the full text of the single human-readable report plugin-builder-report.md.

    summary: contents of conversion-report.json (conversion data and registration_plan);
    registration: contents of registration-report.json (registration results), or None if step 2
    has not run yet (registration section shows a placeholder). Both convert and register_converted
    share this function to rewrite the full report -- the latter fills in registration results,
    while re-running convert resets it to the placeholder state (invalidating old registration data).
    The two JSON files and marketplace.orig are machine contracts/snapshots noted at the end."""
    source = summary.get("source")
    market = summary.get("market")
    notes = summary.get("notes") or []
    plan = summary.get("registration_plan") or {}
    results = summary.get("results") or []
    total = summary.get("total", len(results))
    passed = [r for r in results if r.get("success") and not r.get("validate_errors")]
    usable = [r for r in passed if (r.get("usability") or {}).get("usable", True)]
    unusable = [r for r in passed if not (r.get("usability") or {}).get("usable", True)]
    invalid = [r for r in results if r.get("success") and r.get("validate_errors")]
    failed = [r for r in results if not r.get("success")]

    def _clip_command(cmd, limit=200):
        # Markdown report is for humans: truncate very long hook commands (can exceed 1700 chars
        # on one line, hurting readability and grep noise); full text stays in conversion-report.json.
        cmd = str(cmd)
        return cmd if len(cmd) <= limit else cmd[:limit] + "... (see conversion-report.json for full command)"

    def _hold_lines(r):
        out = [f"  - hook (auto-executed on session events): {h['event']} -> `{_clip_command(h['command'])}`"
               for h in r.get("hooks") or []]
        if r.get("setup_sh"):
            out.append("  - setup.sh (plugin/dependency install script; neither conversion nor registration executes it; "
                       "please review and run manually)")
        return out

    def _names_lines(items, label):
        names = [h["name"] for h in items]
        out = [f"- {label}: {len(items)}"]
        if names:
            shown = ", ".join(names[:20]) + (f" ... ({len(names)} total)" if len(names) > 20 else "")
            out.append(f"    {shown}")
        return out

    lines = ["# Plugin Builder Report (External Repo -> Kimi Plugin -> Personal Market)", "",
             f"- Input source: {source}",
             f"- Market: {market or 'personal'}",
             f"- Total plugins: {total}",
             f"- Converted and usable (0 validate errors + dependency check passed): {len(usable)}",
             f"- Converted but dependencies unavailable (missing tool/script/service; see below): {len(unusable)}",
             f"- Converted with validation errors: {len(invalid)}",
             f"- Conversion failed: {len(failed)}",
             f"- Registration plan (registration_plan, input to register_converted.py): "
             f"registerable {len(plan.get('registerable') or [])}, "
             f"hold (rights review) {len(plan.get('hold_rights_review') or [])}, "
             f"hold (hooks/setup.sh) {len(plan.get('hold_hooks_or_setup') or [])}, "
             f"hold (deps unavailable) {len(plan.get('hold_deps_unavailable') or [])}, "
             f"hold (validate errors) {len(plan.get('hold_validate_errors') or [])}, "
             f"failed {len(plan.get('failed') or [])}", "",
             "Usability criteria: based on the personal-tab install path -- structural validation "
             "(validate_plugin.py, aligned with kernel parseManifest and daimon registration gate) + "
             "dependency check (check_plugin_deps.py, checking MCP server commands/packages/URLs, "
             "hook scripts, and SKILL.md-referenced MCP servers against the runtime toolset). "
             "Client 'Install from file' (sideload) restrictions on mcpServers are sideload-only "
             "constraints and do not count against usability. "
             "Rights scan (rights_scan.py): plugins hitting license-context restriction phrases "
             "(severity=high) or vendor/platform signals (severity=notice) are placed in the hold "
             "bucket and can be released with register_converted.py --only after reviewing this report; "
             "suspected credential/key files and out-of-bounds symlinks were skipped/rejected by "
             "source_guard.py prior to packaging.", ""]

    lines += ["## Registration Results", ""]
    if registration is None:
        lines += ["Not executed -- this section will be populated automatically after running step 2 "
                  "`register_converted.py <this-dir>`: registration status, failure reasons, registry "
                  "reconciliation, and plugin links.", ""]
    else:
        reg = registration
        registered = reg.get("registered") or []
        failed_reg = reg.get("failed") or []
        held = reg.get("held") or {}
        recon = reg.get("reconciliation") or {}
        lines.append(f"- Registered at: {reg.get('generated_at')}")
        lines.append(f"- Registration targets: {reg.get('targets')}")
        lines.append(f"- Registered: {len(registered)}")
        if failed_reg:
            lines.append(f"- Registration failed: {len(failed_reg)}")
            for f in failed_reg:
                lines.append(f"  - {f['name']}: {f['error']}")
        else:
            lines.append("- Registration failed: 0")
        lines += _names_lines(held.get("hold_rights_review") or [],
                              "Hold: rights review (requires user review before registering)")
        lines += _names_lines(held.get("hold_hooks_or_setup") or [],
                              "Hold: hooks/setup.sh (requires user review before registering)")
        lines += _names_lines(held.get("hold_deps_unavailable") or [], "Hold: dependencies unavailable")
        if held.get("hold_validate_errors"):
            lines += _names_lines(held["hold_validate_errors"], "Hold: validation errors")
        if held.get("failed"):
            lines += _names_lines(held["failed"], "Conversion failed")
        lines.append(f"- Registry reconciliation: {reg.get('registry_dir')} currently has "
                     f"{recon.get('registry_entries', 0)} entries "
                     f"(registered this run: {recon.get('registered_this_run', 0)}, "
                     f"pre-existing: {recon.get('pre_existing', 0)}; marketplace.orig is not an entry)")
        missing = recon.get("missing_entries") or []
        if missing:
            lines.append(f"  Warning: {len(missing)} plugins registered successfully but missing from registry: "
                         f"{', '.join(missing)}")
        links = [r for r in registered if r.get("link")]
        if links:
            lines.append(f"- Plugin links: see registered[].link in registration-report.json for all {len(links)}; "
                         f"examples:")
            for r in links[:3]:
                lines.append(f"  - [{r['name']}]({r['link']})")
        lines.append("")

    if notes:
        lines += ["## Global Notes", ""] + [f"- {n}" for n in notes] + [""]
    rights_hits = [r for r in results if (r.get("rights") or {}).get("severity")]
    if rights_hits:
        lines += ["## Rights Scan Hits (Registration Held; Review and Release with --only)", ""]
        for r in rights_hits:
            rights = r["rights"]
            name = r.get("kimi_name") or r.get("index_name") or r.get("origin") \
                or r.get("source_dir")
            lines.append(f"- **{name}** <- {r.get('origin') or r.get('source_dir')}"
                         f" | severity: {rights['severity']}")
            for m in rights.get("restricted") or []:
                lines.append(f"  - Rights restriction: {m['category']} @ {m['file']} ({m['context']})")
            for s in rights.get("vendor") or []:
                lines.append(f"  - Vendor/platform signal: {s['signal']}={s['value']} @ {s['source']}")
        lines.append("")
    lines += ["## Converted and Usable", ""]
    for r in usable:
        alias = f" (index aliases: {', '.join(r['aliases'])})" if r.get("aliases") else ""
        warns = (r.get("usability") or {}).get("warnings") or []
        warn_note = f" | {len(warns)} warning(s)" if warns else ""
        dialect_note = f" | dialect: {r['dialect']}" if r.get("dialect") else ""
        license_note = f" | license: {r['license']}" if r.get("license") else ""
        lines.append(f"- **{r['kimi_name']}** <- {r.get('origin') or r['source_dir']}{alias}"
                     f" | components: {', '.join(r.get('components') or ['none'])}{dialect_note}{license_note}{warn_note}")
        lines.extend(_hold_lines(r))
        for n in r["notes"]:
            lines.append(f"  - {n}")
        for w in warns:
            lines.append(f"  - Warning: {w}")
    if unusable:
        lines += ["", "## Converted but Dependencies Unavailable", ""]
        for r in unusable:
            lines.append(f"- **{r['kimi_name']}** <- {r.get('origin') or r['source_dir']}")
            lines.extend(_hold_lines(r))
            for b in (r.get("usability") or {}).get("blockers", []):
                lines.append(f"  - Blocker: {b}")
    if invalid:
        lines += ["", "## Converted with Validation Errors", ""]
        for r in invalid:
            lines.append(f"- **{r['kimi_name']}** <- {r.get('origin') or r['source_dir']}")
            lines.extend(_hold_lines(r))
            for e in r["validate_errors"]:
                lines.append(f"  - Validation: {e}")
    if failed:
        lines += ["", "## Conversion Failed", ""]
        for r in failed:
            name = r.get("index_name") or r.get("kimi_name") or r.get("origin") or r.get("source_dir")
            lines.append(f"- **{name}**: {r.get('error')}")
    lines += ["", "---", "",
              "Machine-readable artifacts (in this directory, for tools and agents, not session deliverables): "
              "`conversion-report.json` (conversion data and registration_plan), "
              "`registration-report.json` (registration results and full links), "
              "`marketplace.orig` (static snapshot of source marketplace, immutable after creation).", ""]
    return "\n".join(lines)


def write_reports(results, output_dir, source, notes, market=None):
    total = len(results)
    passed = [r for r in results if r.get("success") and not r.get("validate_errors")]
    usable = [r for r in passed if (r.get("usability") or {}).get("usable", True)]
    unusable = [r for r in passed if not (r.get("usability") or {}).get("usable", True)]
    invalid = [r for r in results if r.get("success") and r.get("validate_errors")]
    failed = [r for r in results if not r.get("success")]
    plan = pc.build_registration_plan(results)
    summary = {"source": source, "market": market, "total": total,
               "converted_and_usable": len(usable),
               "converted_but_unusable": len(unusable),
               "converted_with_validate_errors": len(invalid),
               "failed": len(failed), "notes": notes,
               "registration_plan": plan, "results": results}
    json_path = os.path.join(output_dir, "conversion-report.json")
    pc.write_json(json_path, summary, force=True)

    # Re-running convert invalidates previous registration results: remove registration-report.json
    # (resetting registration section to placeholder) and clean up legacy conversion-report.md.
    stale_registration = os.path.join(output_dir, "registration-report.json")
    if os.path.isfile(stale_registration):
        os.remove(stale_registration)
        print("[Report] Invalidated previous registration-report.json (re-run register_converted.py for fresh results)")
    legacy_md = os.path.join(output_dir, "conversion-report.md")
    if os.path.isfile(legacy_md):
        os.remove(legacy_md)
        print("[Report] Removed legacy conversion-report.md (unified into plugin-builder-report.md)")

    md_path = os.path.join(output_dir, REPORT_MD_NAME)
    with open(pc.winlong(md_path), "w", encoding="utf-8") as f:
        f.write(render_plugin_builder_report(summary, None))
    return md_path, json_path


# ---------- marketplace.orig (full source marketplace manifest, generated once on conversion as static snapshot) ----------
def _orig_entry_from_index_entry(entry):
    """Index entry -> orig entry (including unconverted/failed entries; source taken from url or path)."""
    name = entry.get("name")
    if not name:
        ref = entry.get("url") or entry.get("path") or ""
        name = str(ref).rstrip("/").rsplit("/", 1)[-1]
    if not name:
        return None
    out = {"name": str(name)}
    if entry.get("description"):
        out["description"] = str(entry["description"])
    if entry.get("version"):
        out["version"] = str(entry["version"])
    src = entry.get("url") or entry.get("path")
    if src:
        out["source"] = str(src)
    return out


def _orig_entry_from_plugin_dir(plugin_dir):
    """Plugin directory -> orig entry (reads name/description/version from first recognized manifest;
    uses directory name for manifest-less skill-only directories)."""
    for rel, _d in MANIFEST_CANDIDATES:
        p = os.path.join(plugin_dir, rel)
        if not os.path.isfile(p):
            continue
        try:
            m = pc.load_json(p)
        except Exception:  # noqa: BLE001 — broken manifest is logged in conversion report; skip in orig
            return None
        name = m.get("name")
        if not name:
            return None
        out = {"name": str(name)}
        if m.get("description"):
            out["description"] = str(m["description"])
        if m.get("version"):
            out["version"] = str(m["version"])
        return out
    base = os.path.basename(plugin_dir)
    return {"name": base} if base else None


def collect_marketplace_plugins(root, notes):
    """Collect the full plugin list of the source marketplace (contents of marketplace.orig):

    - Index repo (root plugins.json / marketplace.json, or subdirectory marketplace.json):
      takes all index entries -- including unconverted, failed, and dep-unavailable entries,
      unaffected by --limit or conversion scope;
    - Non-index repo (single plugin / monorepo / tree subpath input): scans whole repo for plugin
      manifests -- even when tree subpath converts only one subdir, orig records all plugins in repo;
    - Skill-only fallback repo: single entry for the repo itself.
    """
    index_entries = []
    found = find_index_candidates(root)
    if found:
        rel, kind = found[0]
        index_entries = _read_index(os.path.join(root, rel), kind) or []
    else:
        for index_path, _base_dir, kind in find_subdir_marketplaces(root):
            index_entries.extend(_read_index(index_path, kind) or [])
    if index_entries:
        plugins = [p for p in (_orig_entry_from_index_entry(e) for e in index_entries) if p]
        if plugins:
            return plugins
    plugins = []
    for rel in find_plugin_manifests(root):
        brief = _orig_entry_from_plugin_dir(os.path.join(root, rel))
        if brief:
            plugins.append(brief)
    if plugins:
        return plugins
    base = os.path.basename(root)
    return [{"name": base}] if base else []


def write_marketplace_orig(market_dir, market, plugins):
    """Write full source marketplace manifest to <market_dir>/marketplace.orig (modeled on Claude
    marketplace format). Generated only on conversion; later register/install/uninstall never modify it."""
    doc = {"name": market,
           "plugins": sorted(plugins, key=lambda p: p["name"])}
    pc.write_json(os.path.join(market_dir, "marketplace.orig"), doc, force=True)
    return os.path.join(market_dir, "marketplace.orig")


def parse_args():
    p = argparse.ArgumentParser(description="Batch-convert a plugin/skill repository (in any source dialect) into Kimi plugins")
    p.add_argument("source", help="GitHub repo URL (https/git@, supports tree/blob subpaths; "
                                  "github.com/<o>/<r> and <o>/<r> shorthand auto-expanded) or local directory")
    p.add_argument("--output-dir", default=str(pc.resolve_plugin_sources_dir()),
                   help="Conversion output root directory (default: plugin-sources/ under daimon-share, same as create_plugin.py); "
                        "outputs are written to <output-dir>/<market>/<name>/ and reports to <output-dir>/<market>/")
    p.add_argument("--market", default=None,
                   help="Target market name (default: resolved from top-level name in index config, "
                        "falling back to personal); explicit value overrides index config")
    p.add_argument("--force", action="store_true",
                   help="Allow overwriting existing market reports and same-named plugin sources (including manual edits); "
                        "confirm old contents can be discarded first")
    p.add_argument("--limit", type=int, default=0,
                   help="Max number of index entries to expand and convert (0 = unlimited, for debugging; "
                        "alias entries pointing to the same plugin also count toward the limit)")
    p.add_argument("--work-dir", default=None,
                   help="Temporary directory for cloning repos (default: system temp dir, auto-cleaned on exit; "
                        "an explicitly specified directory is not cleaned up)")
    p.add_argument("--offline", action="store_true",
                   help="Run dependency checks offline (skip npm/PyPI registry and MCP URL probes)")
    p.add_argument("--jobs", type=int, default=8,
                   help="Number of parallel worker threads for upstream fetching and per-plugin conversion/validation/dep checks "
                        "(default: 8, 1 = sequential)")
    return p.parse_args()


def main():
    # Pin stdio to UTF-8: prevents UnicodeEncodeError under Windows GBK locale + piped capture
    pc.reconfigure_stdio_utf8()
    a = parse_args()
    output_dir = os.path.abspath(a.output_dir)
    os.makedirs(pc.winlong(output_dir), exist_ok=True)
    notes = []
    work_dir = a.work_dir or tempfile.mkdtemp(prefix="codex-plugin-convert-")
    cleanup_work_dir = a.work_dir is None
    os.makedirs(work_dir, exist_ok=True)
    try:
        _convert(a, output_dir, notes, work_dir)
    finally:
        # work_dir is a per-run clone cache (fresh per run, not reused across runs):
        # auto-clean self-created temp dirs on exit (including errors); caller owns explicit --work-dir.
        if cleanup_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def _convert(a, output_dir, notes, work_dir):

    # Explicit --market overrides all config resolution; note if normalized, reject if still invalid.
    market = None
    if a.market:
        try:
            market = pc.validate_market_name(pc.normalize_market_name(a.market))
        except ValueError as e:
            raise SystemExit(f"Error: {e}") from e
        if market != a.market.strip():
            notes.append(f"Normalized market name to {market} (input: {a.market!r})")

    target = None
    cls = classify_source(a.source)
    if cls["type"] == "rejected":
        raise SystemExit(f"Error: {cls['reason']}")
    if cls["type"] == "github":
        target = cls["target"]
    if cls["type"] in ("github", "git") and cls["normalized"] != a.source.strip():
        notes.append(f"Normalized input by convention to {cls['normalized']}")
    fetch_source = cls["normalized"] if cls["type"] in ("github", "git") else cls["path"]
    if cls["type"] == "local":
        # Prevent self-conversion: reject if source dir and output dir are identical or nested
        # (scanning would treat outputs as inputs and rebuilding dest could delete sources).
        src_real = os.path.realpath(cls["path"])
        out_real = os.path.realpath(output_dir)
        if src_real == out_real or out_real.startswith(src_real + os.sep) \
                or src_real.startswith(out_real + os.sep):
            raise SystemExit(
                f"Error: Local source directory and output directory cannot be identical or nested "
                f"(input: {src_real}, output: {out_real}). Use --output-dir to specify a disjoint directory.")
    ref = target["ref"] if target and target["kind"] in ("tree", "blob") else None
    try:
        root, _ = acquire_repo(fetch_source, work_dir, notes, ref=ref)

        forced_manifest = None
        if target and target["kind"] == "tree" and target.get("path"):
            # tree subpath: treat that directory as a single-plugin input and convert only it
            p = os.path.normpath(os.path.join(root, target["path"]))
            if not os.path.isdir(p):
                raise SystemExit(f"Error: Directory referenced by tree URL does not exist in repo (ref {ref}): {target['path']}")
            notes.append(f"tree subpath URL: converting only plugin at {target['path']} (ref {ref})")
            sources = [{"source_dir": p, "repo_root": root, "index_entry": None, "origin": a.source}]
        elif target and target["kind"] == "blob" and target.get("path"):
            # blob file as strict entry point (index file or manifest): relative paths in index
            # entries resolve per _index_base_dir (marketplace root / file dir, falling back to
            # repo root) without scanning other configs in the repo
            f = os.path.normpath(os.path.join(root, target["path"]))
            if not os.path.isfile(f):
                raise SystemExit(f"Error: File referenced by blob URL does not exist in repo (ref {ref}): {target['path']}")
            base_name = os.path.basename(f)
            rel_tail = target["path"].replace("\\", "/")
            if base_name in ("marketplace.json", "plugins.json"):
                kind = "plugins.json index" if base_name == "plugins.json" else "marketplace"
                entries = _read_index(f, kind)
                if not entries:
                    raise SystemExit(f"Error: blob file is not a valid index (no valid entries): {target['path']}")
                if market is None:
                    # blob index entry: resolve market from top-level name of this index file first
                    blob_market_raw = read_index_market_name(f)
                    if blob_market_raw is not None:
                        blob_market = pc.normalize_market_name(blob_market_raw)
                        try:
                            market = pc.validate_market_name(blob_market)
                            notes.append(f"Market name resolved from index config as {market}"
                                         f" (name field of {target['path']})")
                        except ValueError:
                            notes.append(f"Market name {blob_market_raw!r} in index config {target['path']}"
                                         " is invalid after normalization; falling back to personal")
                notes.append(f"blob strict entry: expanding index file {target['path']} ({len(entries)} entries), "
                             "resolving relative paths from marketplace root (falling back to repo root)")
                sources = _expand_indexes(root, [{"entries": entries, "index_path": f,
                                                  "base_dir": _index_base_dir(root, f), "kind": kind}],
                                          work_dir, notes, jobs=a.jobs)
            else:
                # manifest strict entry: plugin dir = plugin root traced back by candidate rel path; dialect matched by candidate
                forced = None
                source_dir = os.path.dirname(f)
                for rel, d in MANIFEST_CANDIDATES:
                    rel_s = rel.replace(os.sep, "/")
                    if rel_tail == rel_s or rel_tail.endswith("/" + rel_s):
                        plugin_root_rel = rel_tail[:-len(rel_s)].rstrip("/")
                        source_dir = os.path.join(root, plugin_root_rel) if plugin_root_rel else root
                        forced = (rel, d)
                        break
                if forced is None and base_name in ("plugin.json", "kimi.plugin.json"):
                    forced = (base_name, "kimi" if base_name == "kimi.plugin.json" else "generic")
                if forced is None:
                    raise SystemExit(f"Error: blob file is neither an index file nor a recognized plugin manifest: {target['path']}")
                forced_manifest = forced
                notes.append(f"blob strict entry: using {target['path']} as manifest ({forced[1]} dialect), "
                             "converting only its plugin without scanning other repo configs")
                sources = [{"source_dir": os.path.normpath(source_dir), "repo_root": root,
                            "index_entry": None, "origin": a.source}]
        else:
            sources = discover_sources(root, work_dir, notes, limit=a.limit, jobs=a.jobs)
    except RuntimeError as e:
        raise SystemExit(f"Error: {e}") from e
    if a.limit > 0:
        sources = sources[:a.limit]

    # Market resolution (unified across input types): explicit --market > top-level name in source
    # index config > personal. Both git URLs and local directories use the same logic -- cloned
    # repos are sources too; single-plugin inputs (tree/blob manifest) have no index and fall back to personal.
    if market is None:
        market = resolve_market_from_repo(root, notes)
    if market is None:
        market = pc.DEFAULT_MARKET
    market_dir = os.path.join(output_dir, market)
    try:
        # Protect reports and sources under the same market dir; atomic creation also prevents concurrent collisions.
        os.makedirs(pc.winlong(market_dir), exist_ok=a.force)
    except FileExistsError:
        raise SystemExit(
            f"Error: Market output directory already exists: {market_dir}. Conversion stopped to protect "
            "existing reports and plugin sources. Use --output-dir for a new output root, or use --force "
            "only after confirming old reports and same-named sources (including manual edits) can be discarded.") from None
    if a.force:
        print(f"[Warning] --force enabled: will overwrite reports and same-named plugin sources under {market_dir} (including manual edits)")

    # marketplace.orig: full source marketplace manifest (including unconverted/failed entries;
    # tree subpaths also scan the whole repo). Independent of sources and --limit -- it is a source snapshot.
    orig_plugins = collect_marketplace_plugins(root, notes)

    used_names = set()
    used_names_lock = threading.Lock()
    validator = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate_plugin.py")

    def process_one(s):
        if s.get("error"):
            return {"source_dir": None, "origin": s.get("origin"),
                    "index_name": (s.get("index_entry") or {}).get("name"),
                    "success": False, "notes": [], "error": s["error"]}
        try:
            r = convert_one(s["source_dir"], None, s.get("index_entry"), market_dir, used_names,
                            repo_root=s.get("repo_root"),
                            forced_manifest=forced_manifest,
                            used_names_lock=used_names_lock)
            if s.get("aliases"):
                r["aliases"] = s["aliases"]
            r["origin"] = r.get("origin") or s.get("origin")
            if r["success"]:
                # Validator subprocess stdout encoding varies by environment (PYTHONIOENCODING/
                # PYTHONUTF8/Windows GBK locale); pin utf-8 on both sides with errors=replace so
                # stray bytes never abort the batch. Pass pc.winlong-prefixed output path so
                # internal join/walk in the subprocess won't hit Windows MAX_PATH.
                proc = subprocess.run([sys.executable, validator, pc.winlong(r["output_dir"])],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace",
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                errors = [ln[8:] for ln in proc.stdout.splitlines() if ln.startswith("[ERROR] ")]
                r["validate_errors"] = errors
                if proc.returncode != 0 and not errors:
                    r["validate_errors"] = ["validate_plugin.py internal error: " + proc.stdout.strip()[:300]]
                if not r["validate_errors"]:
                    # Dependency availability check (against runtime toolset; non-empty blockers = unusable)
                    r["usability"] = cpd.check_plugin(pc.winlong(r["output_dir"]), online=not a.offline)
                    if r.get("dropped_components"):
                        # Declared component content missing in source dir (incomplete mirror/repo):
                        # structurally valid but hollow -- mark unusable and exclude from auto-registration
                        usa = r["usability"] if isinstance(r.get("usability"), dict) else {}
                        blockers = list(usa.get("blockers") or [])
                        blockers.append(
                            "Declared component content missing in source directory; conversion incomplete: "
                            + ", ".join(r["dropped_components"]))
                        usa["name"] = usa.get("name") or r.get("kimi_name")
                        usa["usable"] = False
                        usa["blockers"] = blockers
                        usa.setdefault("warnings", [])
                        r["usability"] = usa
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — record per-plugin exception as failure and continue batch
            r = {"source_dir": s.get("source_dir"), "origin": s.get("origin"),
                 "index_name": (s.get("index_entry") or {}).get("name"),
                 "success": False, "notes": [],
                 "error": f"Uncaught exception during conversion: {type(e).__name__}: {e}"}
        return r

    # Run per-plugin conversion/validation/dep checks in parallel via --jobs; map preserves order
    if a.jobs > 1 and len(sources) > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            results = list(ex.map(process_one, sources))
    else:
        results = [process_one(s) for s in sources]

    md_path, _json_path = write_reports(results, market_dir, a.source, notes, market=market)
    write_marketplace_orig(market_dir, market, orig_plugins)
    passed = [r for r in results if r.get("success") and not r.get("validate_errors")]
    ok = sum(1 for r in passed if (r.get("usability") or {}).get("usable", True))
    unusable = sum(1 for r in passed if not (r.get("usability") or {}).get("usable", True))
    invalid = sum(1 for r in results if r.get("success") and r.get("validate_errors"))
    failed = sum(1 for r in results if not r.get("success"))
    plan = pc.build_registration_plan(results)
    print(f"Conversion complete: {len(results)} total, {ok} usable, {unusable} deps unavailable, "
          f"{invalid} validation errors, {failed} failed")
    print(f"Registration plan: {len(plan['registerable'])} registerable, "
          f"{len(plan['hold_rights_review'])} hold (rights review), "
          f"{len(plan['hold_hooks_or_setup'])} hold (hooks/setup.sh), "
          f"{len(plan['hold_deps_unavailable'])} hold (deps unavailable), "
          f"{len(plan['hold_validate_errors'])} hold (validate errors), "
          f"{len(plan['failed'])} failed")
    print(f"Market: {market}")
    print(f"Plugin directory: {market_dir}")
    print(f"Report (human-readable; conversion details + registration results filled after step 2): {md_path}")
    if plan["registerable"]:
        register_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "register_converted.py")
        print(f"Next step (batch-register {len(plan['registerable'])} registerable plugins, auto-holding others):")
        print(f"  python3 \"{register_script}\" \"{market_dir}\"")


if __name__ == "__main__":
    main()
