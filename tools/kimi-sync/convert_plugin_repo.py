#!/usr/bin/env python3
"""convert_plugin_repo.py — 把一个插件/技能仓库（任意来源方言）批量转换成本项目（Kimi）插件。

输入：GitHub 仓库地址（https://github.com/<owner>/<repo>，也支持 git@ 形式、github.com/<o>/<r>
  缺协议写法与 <owner>/<repo> 简写）、通用 git 托管地址（gitee.com 与 GitHub 结构同构、
  支持全形态；cnb.cool / atomgit.com / gitlab.com 等支持仓库根地址，git clone 拉取，
  无 codeload 回退与 tree/blob 子路径）或本地目录；也支持 tree/blob/blame 子路径 URL：
  - https://github.com/<o>/<r>/tree/<ref>/<path...> → clone 该 repo（checkout 到 ref），
    把 <path> 作为单插件目录输入，只转换这一个插件；
  - https://github.com/<o>/<r>/blob/<ref>/<path...> → 把该文件作为严格入口（索引文件或
    manifest），相对路径以该文件所在目录为基准（找不到时回退仓库根），不再扫描仓库其他配置。
  URL 指定 ref 时严格按该 ref 拉取，失败即报「ref 可能不存在」，不静默回退默认分支；
  分支名含 / 时 URL 无法区分分支名与路径边界，请改用 commit SHA 或不带 / 的标签链接。
  仓库内非代码页面（pull/issues/releases/commit/raw/archive 等）、raw.githubusercontent.com、
  gist 与非 GitHub 系托管的多级路径一律拒绝并给出改写提示，不静默当整仓处理（见 classify_source）。
仓库形态自动识别：
  A. 单插件仓库：仓库根部（或某个子目录）有可识别的插件 manifest。
  B. 多插件 monorepo：多个子目录各有 manifest（如 kreuzberg-dev/plugins）。
  C. 索引仓库：根部有 plugins.json（{"plugins": [...]} 列表），或有 marketplace 索引
     （.agents/plugins/marketplace.json 为 Codex marketplace，.claude-plugin/marketplace.json
     为 Claude marketplace）；条目用 url 指向外部 GitHub 仓库、或用 path/source 指向仓库内
     （或本地）相对路径目录；插件本体可能镜像在本仓库 plugins/<owner>/<repo>/ 下，镜像不全
     时按 url 回源拉取。官方 Claude marketplace 外部源 {"source": "url", "url", "sha"/"ref"}
     按钉住的提交拉取；git-subdir 源取仓库内子目录为插件目录；技能集合条目
     {"source": "./", "skills": [...]} 按数组组装 skill-only 插件（同 source 多条目不合并）；
     条目 path 失效时按条目名在仓库内唯一匹配 manifest 同名回退，对不上不猜。
     DeepSeek registry（registry-snapshot.json，根部或任意子目录如 data/）：条目 url
     回源拉取，description 多语言对象 zh 优先拍平；cordis 判定——有可识别 manifest 或
     根部 SKILL.md/skills/ 的声明式子集可转，纯代码型 cordis 包明确记失败原因。
     多个索引候选并存时不静默：打印候选清单与冲突警告，按优先级
     plugins.json > .agents/plugins/marketplace.json > .claude-plugin/marketplace.json 选用，
     并在报告里记录选了谁、忽略了谁。根部没有索引时递归发现子目录里的 marketplace.json
     与 registry-snapshot.json 并按索引模式展开。
  D. 没有任何 manifest、但根部有 SKILL.md 或 skills/ 的仓库：整仓包成一个 skill-only 插件。

插件 manifest 识别约定（按优先级，Codex 只是支持的来源方言之一）：
  1. kimi.plugin.json                —— 本项目原生格式：轻转换（校正 name/version/category 后原样保留）
  2. .kimi-plugin/plugin.json        —— 本项目原生格式（点目录形式）：轻转换
  3. .codex-plugin/plugin.json       —— Codex 约定：全量方言转换
  4. .claude-plugin/plugin.json      —— Claude 约定：全量方言转换
  5. .codebuddy-plugin/plugin.json   —— CodeBuddy 约定（格式近 Claude）：全量方言转换
  6. .cursor-plugin/plugin.json      —— Cursor 约定（格式近 Claude）：全量方言转换
  7. gemini-extension.json           —— Gemini 扩展约定：mcpServers 同构（${extensionPath} 按插件根
                                        占位符改写），contextFileName 映射为 systemPromptPath
  8. server.json                     —— MCP 官方 registry 约定：packages（npm/pypi，stdio）→
                                        npx/uvx server，remotes → url 型 server，包成仅 mcpServers 插件
  9. plugin.json（插件目录根部）      —— 通用约定：内容含 interface 按 Codex 方言处理，否则按简单方言处理
  同一插件目录存在多个平台定义（kimi/codex/claude/codebuddy/gemini/serverjson）时，
  按上述识别优先级（MANIFEST_CANDIDATES 顺序）自动选第一个方言转换，被跳过的方言
  记进转换报告 notes（可见、可回溯，不打断批量转换）。单一平台直接用。

转换规则（非 Kimi 方言 → kimi.plugin.json）：
  - 清单写到插件根目录 kimi.plugin.json（Kimi 解析器认根目录清单，见 agent-core plugin/manifest.ts）。
  - name 归一化为 kebab-case；version 非法时回退 0.1.0；重名自动加 -2/-3 后缀。
  - interface 只保留 Kimi 认识的字段（displayName/shortDescription/longDescription/
    developerName/websiteURL/iconUrl/category）；来源方言的专有字段（capabilities/defaultPrompt/
    brandColor/screenshots/composerIcon 等）丢弃并记入报告 notes。
  - interface.category 是自由文本，映射到 Kimi 枚举 PRODUCTIVITY/DEVELOPER/FINANCE/
    LIFESTYLE_HEALTH/SYSTEM（见 CATEGORY_MAP）。
  - mcpServers：来源方言常写成指向 .mcp.json 的路径字符串（"./.mcp.json"），Kimi 要求内联
    对象 —— 读文件内联；兼容 mcpServers / mcp_servers / 裸字典三种形态。
    command 里的 ${PLUGIN_ROOT} / $PLUGIN_ROOT / __REPO_ROOT__ 改写为 "./"（Kimi 会按插件
    根目录解析 ./ 开头的 command/cwd）；args 里的同类占位符替换为 "." 并补 cwd "./"；
    绝对路径 command 一律按 PATH 命令名保留（basename，Kimi 不收绝对路径），是否在
    PATH / 有无公开安装途径由依赖检查实际验证，不可用进暂缓桶，转换不丢弃。
  - hooks：来源方言常写成指向 hooks.json 的路径字符串，且文件是 Claude 风格
    {"hooks": {Event: [{matcher, hooks: [{type: command, command, timeout, statusMessage}]}]}}，
    Kimi 要求内联 [{event, command, timeout?}] —— 拍平转换；event 只保留 Kimi 支持的
    16 种（HookDefSchema），不支持的丢弃并记 notes；statusMessage 等非 schema 字段丢弃。
  - commands / agents：路径统一补 "./" 前缀；空数组直接省略字段。
  - 插件本体整目录复制（排除 .git/.github/来源 manifest 目录/node_modules），保证 skills/、
    hooks/、mcp 源码等被引用的文件都在插件目录里；monorepo 子目录插件用 __REPO_ROOT__
    引用的仓库级共享源码也会复制进来。

产出（均按市场分目录；market 解析与输入入口无关：--market 参数 > 源索引配置顶层
  name（根部 plugins.json / marketplace.json，含子目录发现的 marketplace.json；blob 链接
  指向索引文件时按该文件的 name）> personal——单插件输入（tree 子路径 / blob manifest /
  无索引仓库）源里没有索引，回退 personal）：
  <output-dir>/<market>/<name>/           每个转换成功的插件一个目录（符合本项目插件规范）
  <output-dir>/<market>/plugin-builder-report.md   唯一人读报告：转换明细 + 登记结果
                                                   （登记结果由 register_converted.py 填回）
  <output-dir>/<market>/conversion-report.json     机器合同：转换数据 + registration_plan
  <output-dir>/<market>/marketplace.orig           源市场全量清单（静态快照）

市场产物目录已存在时默认拒绝（包括空目录），不覆盖报告或插件源码；
用 --output-dir 指定新的产物根，或经用户确认后用 --force 覆盖旧报告和同名插件源码。

每个插件转换后先跑 validate_plugin.py（结构 0 error），再跑 check_plugin_deps.py
（依赖可用性），两关都过才算「真正可用」。本地全流程，无 token。
work_dir（git 拉取缓存）每次运行新建、运行结束自动清理（含异常退出）；
显式 --work-dir 指定的目录不会被清理。
"""

import argparse, json, os, re, shutil, socket, subprocess, sys, tempfile, threading, urllib.parse, urllib.request
import concurrent.futures
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc
import check_plugin_deps as cpd
import source_guard as sg
import rights_scan as rs

# 插件 manifest 识别约定（按优先级）：(相对插件目录的路径, 来源方言)
# kimi = 本项目原生格式（轻转换）；codex/claude/generic = 各来源方言（全量转换）
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

# 参与「多平台方言检测」的平台方言（generic 根部 plugin.json 不算平台定义：与平台
# manifest 并存时按上面的优先级走，不触发选择确认）
PLATFORM_DIALECTS = ("kimi", "codex", "claude", "codebuddy", "cursor", "gemini", "serverjson")


def detect_manifest(plugin_dir):
    """按优先级探测目录里的插件 manifest，返回 (相对路径, 方言)；没有返回 (None, None)。"""
    plugin_dir = pc.winlong(plugin_dir)
    for rel, dialect in MANIFEST_CANDIDATES:
        if os.path.isfile(os.path.join(plugin_dir, rel)):
            return rel, dialect
    return None, None


def detect_all_manifests(plugin_dir):
    """目录里实际存在的所有候选 manifest [(相对路径, 方言)]（按 MANIFEST_CANDIDATES 优先级序）。"""
    plugin_dir = pc.winlong(plugin_dir)
    return [(rel, d) for rel, d in MANIFEST_CANDIDATES
            if os.path.isfile(os.path.join(plugin_dir, rel))]

# Kimi manifest.ts 支持的 hook 事件（HookDefSchema 枚举，与 validate_plugin.py 保持一致）
KIMI_HOOK_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
                    "PermissionResult", "UserPromptSubmit", "Stop", "StopFailure", "Interrupt",
                    "SessionStart", "SessionEnd", "SubagentStart", "SubagentStop",
                    "PreCompact", "PostCompact", "Notification")

# interface.category 自由文本 → Kimi 枚举（缺省 PRODUCTIVITY）
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

# 常见解释器/启动器集合（用于识别路径 token 前的解释器上下文）
PATH_COMMAND_BASENAMES = {"python3", "python", "node", "npx", "uvx", "uv", "deno", "bun",
                          "bash", "sh", "env", "docker", "ruby", "perl", "php", "go", "java"}

# 复制插件本体时排除的目录/文件（来源方言的 manifest 目录由 copy_excludes 按方言追加，
# 转换后统一由根目录 kimi.plugin.json 取代）
COPY_EXCLUDE_DIRS = {".git", ".github", "node_modules", "__pycache__"}
DIALECT_MANIFEST_DIRS = {"kimi": ".kimi-plugin", "codex": ".codex-plugin",
                         "claude": ".claude-plugin", "codebuddy": ".codebuddy-plugin",
                         "cursor": ".cursor-plugin"}


def copy_excludes(dialect):
    """复制插件本体时要额外排除的来源 manifest 目录/文件：非 kimi 方言统一排除各平台
    manifest 目录与 gemini-extension.json、server.json（产物以根目录 kimi.plugin.json
    为准，其他平台的 manifest 不带走）。"""
    if dialect == "kimi":
        return set(COPY_EXCLUDE_DIRS)
    return set(COPY_EXCLUDE_DIRS) | set(DIALECT_MANIFEST_DIRS.values()) | {"gemini-extension.json", "server.json"}

# 来源方言专有、Kimi 不认识的字段（转换时丢弃，记 notes）
CODEX_ONLY_TOP_FIELDS = ("apps", "userConfig", "scripts", "displayName", "privacyPolicyURL",
                         "termsOfServiceURL", "websiteURL", "composerIcon", "publisher", "type",
                         "commit", "verified", "entry", "id", "bundle", "logo")
CODEX_ONLY_INTERFACE_FIELDS = ("capabilities", "defaultPrompt", "composerIcon", "brandColor",
                               "screenshots", "privacyPolicyURL", "termsOfServiceURL", "logo",
                               "type", "logoDark", "logoURL", "logoLarge", "logoSmall",
                               "logoMedium", "icon", "thumbnail", "links")

PLUGIN_ROOT_TOKENS = ("${PLUGIN_ROOT}", "$PLUGIN_ROOT", "__REPO_ROOT__", "${extensionPath}")

# ---- kimi.plugin.json 字段规范（权威：agent-core plugin/manifest.ts + config/schema.ts
# 的 zod schema + https://catalog.msh.team/misc/kimi.plugin.schema.json + 个人市场登记
# 门禁 daimon plugin-market/service.ts）。产物 manifest 只允许出现这些字段。 ----
SPEC_TOP_FIELDS = ("$schema", "name", "version", "description", "keywords", "author",
                   "homepage", "license", "skills", "agents", "sessionStart", "mcpServers",
                   "hooks", "commands", "interface", "skillInstructions", "systemPrompt",
                   "systemPromptPath")
# interface：runtime 读前 5 个；iconUrl/category/hostKind/platforms/mcpOverrides 为市场/schema 字段
SPEC_INTERFACE_FIELDS = ("displayName", "shortDescription", "longDescription", "developerName",
                         "websiteURL", "iconUrl", "category", "hostKind", "platforms",
                         "mcpOverrides")
SPEC_HOST_KINDS = ("hosted", "local")
SPEC_PLATFORMS = ("macos", "windows", "linux", "ios", "android", "harmonyos")
# mcpServers 条目（zod McpServerConfig）：stdio / remote 各自的合法键
MCP_COMMON_KEYS = ("enabledTools", "disabledTools", "enabled", "startupTimeoutMs", "toolTimeoutMs")
MCP_STDIO_KEYS = ("command", "args", "env", "cwd", "executor")
MCP_REMOTE_KEYS = ("url", "headers", "auth", "bearerTokenEnvVar", "transport")
# systemPrompt / systemPromptPath 上限（runtime 超限直接忽略）
SYSTEM_PROMPT_MAX_BYTES = 32 * 1024


# ---------- 获取仓库 ----------
# 仓库内非代码页面的首段路径：这些 URL 指向 PR/issue/release/commit 等页面而非代码，
# 静默当整仓转换会违背用户意图，统一拒绝并给出指引（见 classify_source）。
NON_CODE_PAGE_SEGMENTS = ("pull", "issues", "issue", "releases", "commit", "commits",
                          "actions", "wiki", "security", "insights", "network", "graphs",
                          "pulse", "projects", "discussions", "settings", "compare",
                          "stargazers", "watchers", "forks", "packages", "raw", "archive")


# 与 GitHub URL 结构同构的托管站（/<o>/<r>/tree|blob|blame/<ref>/<path> 同样有效），
# tree/blob 子路径解析与 GitHub 一致；拉取层差异见 acquire_repo（codeload 仅 github.com）。
GITHUB_LIKE_HOSTS = ("github.com", "gitee.com")


def parse_github_target(url):
    """解析 GitHub 系 URL，返回 {"host", "owner", "repo", "kind", "ref", "path"}；
    非 GitHub 系（github.com / gitee.com）返回 None。

    kind:
      "repo" —— 仓库根（或无 trailing 路径的形态，保持原有仓库根行为）；
      "tree" —— https://<host>/<o>/<r>/tree/<ref>/<path...>（path 可为空）；
      "blob" —— https://<host>/<o>/<r>/blob/<ref>/<path...>（path 指向文件；
        blame/<ref>/<path...> 与 blob 同构，一并按 blob 处理）；
      "page" —— 仓库内非代码页面（pull/issues/releases/commit/raw/archive 等，
        见 NON_CODE_PAGE_SEGMENTS），此时带 "page" 字段，由 classify_source 拒绝。
    限制：ref 只取第一个路径段（feature/foo 这类带斜杠的分支名不支持，拉取失败时
    acquire_repo 会在报错里说明）。"""
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
    """通用 git 仓库根 URL（任意 host，仅仓库根两段路径）：
    https?://<host>/<o>/<r>[.git][/] 或 git@<host>:<o>/<r>[.git]
    返回 {"host", "owner", "repo", "clone_url"}；不像仓库根地址返回 None。
    多级路径（含 tree/blob 等子路径或 group/subgroup/repo 多级组）不匹配——
    非 GitHub 系托管只承诺仓库根输入。"""
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
    """把用户输入归类为本地目录 / GitHub 系目标 / 通用 git 仓库 / 拒绝，返回 dict：

      {"type": "local", "path": <绝对路径>}
      {"type": "github", "target": <parse_github_target 结果>, "normalized": <规范化 URL>}
      {"type": "git", "target": <parse_git_url 结果>, "normalized": <clone_url>}
      {"type": "rejected", "reason": <给用户看的一句话>}

    GitHub 系（github.com 与结构同构的 gitee.com）支持仓库根 / tree / blob / blame 全形态；
    其他 git 托管（cnb.cool、atomgit、gitlab 等）只承诺仓库根输入（git clone 拉取），
    带子路径的 URL 拒绝并提示。
    规范化：缺协议的 github.com/<o>/<r>、www.github.com 与 owner/repo 简写自动补全为
    https://github.com/<o>/<r>。owner/repo 简写只在输入不是本地目录时生效（isdir 先判），
    两段均以字母/数字/下划线开头，不吞 ./foo、../foo 这类相对路径。
    拒绝：raw.githubusercontent.com / gist / 仓库内非代码页面，各自给出可操作的改写提示。"""
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
                f"raw.githubusercontent.com 是文件内容地址，不是仓库地址。等价的文件入口链接是 "
                f"https://github.com/{o}/{r}/blob/{rest}；要转换整个仓库请用 https://github.com/{o}/{r}"}
    if re.match(r"^https?://gist\.github\.com/", s):
        return {"type": "rejected", "reason":
                "gist 不是仓库，插件转换只支持仓库地址；"
                "请先把 gist 内容整理成仓库，或下载到本地后用本地目录作为输入"}
    t = parse_github_target(s)
    if t is None:
        g = parse_git_url(s)
        if g is not None:
            return {"type": "git", "target": g, "normalized": g["clone_url"]}
        if re.match(r"^https?://(?:www\.)?github\.com/?$", s) or \
                re.match(r"^https?://(?:www\.)?github\.com/[^/]+/?$", s):
            return {"type": "rejected", "reason":
                    f"仓库地址不完整（缺少仓库名）: {source}。正确写法是 "
                    "https://github.com/<owner>/<repo>，或指向插件目录的 tree 链接、指向文件的 blob 链接"}
        if re.match(r"^https?://", s) or re.match(r"^git@", s):
            return {"type": "rejected", "reason":
                    f"无法识别的仓库地址: {source}。GitHub/Gitee 支持仓库根 / tree / blob 链接；"
                    "其他 git 托管（cnb.cool、atomgit、gitlab 等）只支持仓库根地址"
                    "（https://<host>/<owner>/<repo>），多级路径与 tree/blob 子路径暂不支持；"
                    "也可以 git clone 到本地后用本地目录作为输入"}
        return {"type": "rejected", "reason":
                f"无法识别的输入: {source}。支持 GitHub/Gitee 仓库根 / tree / blob 链接，"
                "github.com/<owner>/<repo> 或 <owner>/<repo> 简写，通用 git 仓库根地址，以及本地目录"}
    if t["kind"] == "page":
        page = t.get("page", "")
        if page == "commit":
            return {"type": "rejected", "reason":
                    f"/commit/<sha> 是提交详情页，不能直接作为转换输入。如需按该提交转换，"
                    f"请改用 https://{t['host']}/{t['owner']}/{t['repo']}/tree/<sha>"}
        if page in ("raw", "archive"):
            return {"type": "rejected", "reason":
                    f"{page} 链接是文件内容/打包下载地址，不能直接作为转换输入。"
                    f"指向文件请改用 .../blob/<分支>/<文件>，指向目录请改用 .../tree/<分支>/<目录>"}
        return {"type": "rejected", "reason":
                f"仓库的 {page} 页面不是代码地址，不能作为转换输入。请提供仓库根链接，"
                "或指向插件目录的 .../tree/<分支>/<目录>、指向配置文件的 .../blob/<分支>/<文件> 链接"}
    return {"type": "github", "target": t, "normalized": s}


def parse_github_url(url):
    """https://github.com/<owner>/<repo>[...] 或 git@github.com:<owner>/<repo>.git → (owner, repo)；非 GitHub 返回 None。"""
    t = parse_github_target(url)
    return (t["owner"], t["repo"]) if t else None


def github_https_reachable():
    """github.com:443 连通性预检（3s）：不通时跳过 git clone 直接走 codeload tarball，
    避免每个仓库都白等一次 TCP 连接超时。"""
    try:
        with socket.create_connection(("github.com", 443), timeout=3):
            return True
    except OSError:
        return False


_GITHUB_REACHABLE = None
_GITHUB_REACHABLE_LOCK = threading.Lock()
# acquire_repo 的 per-dest 锁：并行展开索引条目时多个条目可能同时拉同一仓库，
# 同一 dest 串行（锁内 double-check 已拉完的直接复用），不同仓库之间仍并行。
_ACQUIRE_LOCKS = {}
_ACQUIRE_LOCKS_GUARD = threading.Lock()


def _acquire_lock_for(dest):
    with _ACQUIRE_LOCKS_GUARD:
        return _ACQUIRE_LOCKS.setdefault(dest, threading.Lock())


def acquire_repo(source, work_dir, notes, ref=None):
    """把 source（GitHub 系 URL / 通用 git URL / 本地目录）变成本地目录路径，返回 (path, 是否临时)。

    github.com：优先 git clone --depth 1；github.com 不通时回退 codeload tarball
    （codeload.github.com 与 github.com 网络可达性经常不同）。
    其他托管（gitee/cnb.cool/atomgit/gitlab 等）：只 git clone（无 codeload 回退、
    无 default_branch API 探测，ref 为空时按远端默认分支）。
    ref 非空（tree/blob 子路径 URL 指定了分支/标签/SHA）时严格按该 ref 拉取：
    失败即报「ref 可能不存在」，绝不静默回退默认分支——用户在页面上看到的是该 ref
    的内容，悄悄换 ref 会转出与页面不一致的东西。codeload 的 tar.gz/<ref> 接受
    分支/标签/commit。ref 为空时按 default_branch → main → master → HEAD 探测。"""
    if os.path.isdir(source):
        return os.path.abspath(source), False
    t = parse_github_target(source)
    if t:
        host, owner, repo = t["host"], t["owner"], t["repo"]
    else:
        g = parse_git_url(source)
        if not g:
            raise RuntimeError(f"无法识别的来源（既不是本地目录也不是 git 仓库 URL）: {source}")
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
                notes.append("github.com 直连不通，本批次全部走 codeload tarball")
    # 同一仓库的并发拉取串行化（并行展开索引条目时多个条目可能指向同一仓库）
    with _acquire_lock_for(dest):
        if os.path.isdir(dest):  # 等锁期间另一线程已拉完
            return dest, True
        _fetch_repo_into(dest, clone_url, host, owner, repo, ref, work_dir, notes)
        return dest, True


def _fetch_repo_into(dest, clone_url, host, owner, repo, ref, work_dir, notes):
    """把 clone_url 拉进 dest（调用方已持有 dest 的 acquire 锁，dest 尚不存在）。
    clone 失败按 acquire_repo 的回退规则走 codeload tarball；彻底失败抛 RuntimeError。"""

    def ref_error(detail):
        return RuntimeError(
            f"拉取仓库 {host}/{owner}/{repo} 的分支/标签 {ref!r} 失败（{detail}）。该 ref 可能不存在；"
            "若分支名含 /（如 feature/foo），tree/blob 链接无法区分分支名与路径边界，"
            "请改用 commit SHA 或不带 / 的标签链接")

    is_commit_sha = bool(ref and re.fullmatch(r"[0-9a-fA-F]{40}", ref))
    if (host != "github.com" or _GITHUB_REACHABLE) and not is_commit_sha:
        # 40 位 hex 是 commit SHA：git clone --branch 不接受裸 SHA，直接走 codeload
        # tar.gz/<sha>（codeload 接受分支/标签/commit），不浪费一次必失败的 clone
        try:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            # Windows 默认 MAX_PATH=260 且 git 默认 core.longpaths=false：深路径仓库
            # checkout 直接报 unable to create file；打开 longpaths，macOS/Linux 无感
            cmd = ["git", "-c", "core.longpaths=true", "clone", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [clone_url, dest]
            # 输出仅作失败诊断；Windows 默认编码（GBK）解 git 的 UTF-8 输出会抛
            # UnicodeDecodeError 掩盖真实错误，显式钉死 utf-8 并容错
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=60, env=env)
            return
        except Exception as e:  # noqa: BLE001 — clone 失败统一走 tarball 回退（仅 github.com 有）
            out = getattr(e, "output", "") or ""
            if ref and re.search(r"Remote branch .* not found|Couldn't find remote ref", out):
                raise ref_error("远端不存在该分支/标签") from e
            if host != "github.com":
                raise RuntimeError(f"git clone 失败 {clone_url}: {out.strip()[:300] or e}") from e
            notes.append(f"git clone {owner}/{repo} 失败（{type(e).__name__}），回退 codeload tarball")
    if host != "github.com":
        # 非 github.com 无 codeload 回退：clone 是唯一途径；SHA ref 在 clone 不可用
        if is_commit_sha:
            raise RuntimeError(f"{host} 不支持按 commit SHA 拉取（无 codeload 回退），"
                               f"请改用分支/标签链接，或 git clone 后 checkout {ref} 再以本地目录输入")
        raise RuntimeError(f"拉取仓库失败 {clone_url}")
    default_branch = None
    try:
        with urllib.request.urlopen(f"https://api.github.com/repos/{owner}/{repo}", timeout=20) as resp:
            default_branch = json.loads(resp.read().decode("utf-8")).get("default_branch")
    except Exception:  # noqa: BLE001 — 拿不到就靠候选分支名试
        pass
    if ref:
        candidates = [ref]
    else:
        candidates = ([default_branch] if default_branch else []) + ["main", "master", "HEAD"]
    last_err = None
    ref_slug = re.sub(r"[^0-9A-Za-z._-]", "-", ref) if ref else ""
    # tarball 临时文件名带 host/ref 前缀：并行拉取时避免不同来源的临时文件互相覆盖
    tarball = os.path.join(work_dir, f"{host}--{owner}--{repo}"
                         + (f"--{ref_slug}" if ref_slug else "") + ".tar.gz")
    for r in candidates:
        if not r:
            continue
        try:
            if r == "HEAD":
                tarball_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/HEAD"
            elif ref and r == ref:
                # codeload 的裸 ref 形式接受分支/标签/commit sha
                tarball_url = (f"https://codeload.github.com/{owner}/{repo}/tar.gz/"
                               + urllib.parse.quote(r, safe=""))
            else:
                tarball_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{r}"
            # urlretrieve 无超时参数，连接挂起会永久卡住；改用手动超时下载
            with urllib.request.urlopen(tarball_url, timeout=60) as resp, \
                    open(tarball, "wb") as f:
                shutil.copyfileobj(resp, f)
            os.makedirs(dest, exist_ok=True)
            subprocess.run(["tar", "-xzf", tarball, "-C", dest, "--strip-components=1"],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            os.unlink(tarball)
            return
        except Exception as e:  # noqa: BLE001 — 换下一个 ref 再试
            last_err = e
    if ref:
        raise ref_error(f"{type(last_err).__name__}: {last_err}") from last_err
    raise RuntimeError(f"拉取仓库失败 {owner}/{repo}: {last_err}")


# ---------- 发现插件来源 ----------
def find_plugin_manifests(root, exclude_prefixes=()):
    """递归找 root 下所有可识别的插件 manifest（优先级见 MANIFEST_CANDIDATES），
    返回插件根目录列表（相对 root，排序）。同一仓库里根部插件与子目录插件副本
    可以并存（如 deepseek 根副本缺文件、plugins/ 副本完整），所以找到 manifest 后
    只跳过对应的 manifest 容器目录本身，其余子目录继续扫描。"""
    found = []
    root_fs = pc.winlong(root)  # walk/判断走前缀路径，返回值保持干净的相对路径
    for dirpath, dirnames, filenames in os.walk(root_fs):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        rel = os.path.relpath(dirpath, root_fs)
        if any(rel == p or rel.startswith(p + os.sep) for p in exclude_prefixes):
            dirnames[:] = []
            continue
        _mrel, dialect = detect_manifest(dirpath)
        if dialect:
            found.append(rel)
            # 只跳过 manifest 容器目录（.codex-plugin/.claude-plugin），其余子目录继续扫
            dirnames[:] = [d for d in dirnames if d not in DIALECT_MANIFEST_DIRS.values()]
    return sorted(found)


# 索引文件候选（按优先级）：根部 plugins.json（本项目原有约定）→ Codex marketplace →
# Claude marketplace。多个并存时不静默：打印候选清单与冲突警告，按此优先级选第一个，
# 并在报告 notes 里记录选了谁、忽略了谁。
INDEX_CANDIDATES = [
    ("plugins.json", "plugins.json 索引"),
    (os.path.join(".agents", "plugins", "marketplace.json"), "Codex marketplace"),
    (os.path.join(".claude-plugin", "marketplace.json"), "Claude marketplace"),
    ("registry-snapshot.json", "DeepSeek registry"),
]


def _index_base_dir(root, index_path):
    """索引条目相对路径的解析基准：marketplace 约定相对路径以「marketplace 根」
    （.claude-plugin/ 或 .agents/ 的上一级目录）为基准；plugins.json 以自身目录为基准。"""
    d = os.path.dirname(index_path)
    if os.path.basename(d) == ".claude-plugin":
        return os.path.dirname(d)
    if d.endswith(os.path.join(".agents", "plugins")):
        return os.path.dirname(os.path.dirname(d))
    return d


def _normalize_marketplace_entry(entry):
    """marketplace.json 条目 → 统一索引条目（带 url 或 path）；无法识别来源返回 None。

    兼容两种实测格式：source 对象形式 {"source": {"source": "local", "path": "./plugins/xxx"}}
    / {"source": {"source": "url", "url": "https://..."}}（awesome-codex-plugins）和 source
    字符串形式 "source": "./connect-apps" / "https://..."（awesome-claude-plugins）；
    条目直接带 url/path 字段的继续支持。source 里的 url 是 "./" 这类本地相对路径时按
    path 处理（如 superpowers 的 {"source": {"source": "url", "url": "./"}}）。
    官方 Claude marketplace 外部源（claude-plugins-community / knowledge-work-plugins）：
    - {"source": "url", "url": <repo>, "sha"/"ref": ...} → 保留 url + sha/ref，
      拉取时按钉住的提交取内容（见 _resolve_index_entry）；
    - {"source": "git-subdir", "url": <repo>, "path": <仓库内子目录>, "ref"/"sha": ...}
      → 保留 url + subdir + sha/ref；这里的 path 是仓库内路径，不按本地相对路径解析，
      也不能丢 url（丢了 url 会在本地找一个不存在的 src 目录）。"""
    if not isinstance(entry, dict):
        return None
    out = dict(entry)
    # DeepSeek registry（registry-snapshot.json）的 description 是多语言对象 {en, zh}，
    # manifest 需要字符串：zh 优先，en 兜底
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
        # 顶层 source 类型声明与对象形式共用解析，保留 subdir 和 sha/ref。
        src_field = entry
    if isinstance(src_field, dict):
        src_kind = src_field.get("source")
        src_url = src_field.get("url")
        if isinstance(src_url, str) and re.match(r"^https?://", src_url) and \
                src_kind in ("url", "git-subdir", "github"):
            # 外部仓库源：保留 url 与钉住的 sha/ref；git-subdir 的 path 记为 subdir
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
            # source 裸词可能是市场标签；有明确远端 URL 时不能把标签变成 path。
            # 明确的本地路径仍优先；没有远端 URL 时保留裸目录名的原有用法。
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
    """读取索引文件并规范化条目，返回条目列表；不是合法索引返回 None。
    条目统一为带 url（外部 GitHub 仓库）或 path（相对路径目录）的 dict，至少其一；
    name/install_url/description/owner/category 为可选元数据。"""
    try:
        data = pc.load_json(index_path)
    except Exception:  # noqa: BLE001 — 不是合法 JSON 就不是索引
        return None
    entries = data.get("plugins")
    if not isinstance(entries, list) or not entries:
        return None
    if kind == "plugins.json 索引":
        if all(isinstance(e, dict) and (e.get("url") or e.get("path")) for e in entries):
            return entries
        return None
    norm = [e for e in (_normalize_marketplace_entry(x) for x in entries) if e]
    return norm or None


def find_index_candidates(root):
    """root 下存在的有效索引候选 [(相对路径, kind)]（按 INDEX_CANDIDATES 优先级序）。"""
    found = []
    for rel, kind in INDEX_CANDIDATES:
        p = os.path.join(root, rel)
        if os.path.isfile(p) and _read_index(p, kind):
            found.append((rel, kind))
    return found


def load_index_file(root, notes=None):
    """发现 root 的索引文件，返回 {"entries", "index_path", "base_dir", "kind"} 或 None。

    候选与优先级见 INDEX_CANDIDATES；多个候选并存时打印候选清单与冲突警告（不静默），
    取优先级最高者，被忽略的候选记入 notes（进转换报告）。"""
    found = find_index_candidates(root)
    if not found:
        return None
    rel, kind = found[0]
    index_path = os.path.join(root, rel)
    entries = _read_index(index_path, kind)
    if len(found) > 1:
        listing = "、".join(f"{r}（{k}）" for r, k in found)
        msg = (f"发现 {len(found)} 个索引候选: {listing}；按优先级 "
               f"{' > '.join(r for r, _k in INDEX_CANDIDATES)} 选用 {rel}，"
               f"忽略 {', '.join(r for r, _k in found[1:])}")
        print(f"[索引冲突警告] {msg}")
        if notes is not None:
            notes.append("索引候选冲突: " + msg)
    return {"entries": entries, "index_path": index_path,
            "base_dir": _index_base_dir(root, index_path), "kind": kind}


def find_subdir_marketplaces(root):
    """递归发现子目录里的 marketplace.json（*/.claude-plugin/marketplace.json 与
    */.agents/plugins/marketplace.json；根部候选由 load_index_file 处理，这里跳过）
    与任意位置的 registry-snapshot.json（DeepSeek registry 索引，如
    data/registry-snapshot.json），返回 [(index_path, base_dir, kind)]（按路径排序）。"""
    root_rels = {r for r, _k in INDEX_CANDIDATES}
    found = []
    root_fs = pc.winlong(root)  # 扫描走前缀路径（深目录不漏），返回干净的绝对路径
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
    """读索引文件顶层 name 字段（Claude marketplace / plugins.json 约定的市场名）；
    文件不可读或 name 缺失/非字符串时返回 None。"""
    try:
        data = pc.load_json(index_path)
    except Exception:  # noqa: BLE001 — 读不出就按无配置处理
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def resolve_market_from_repo(root, notes):
    """从仓库索引配置的顶层 name 解析 market 名：根部候选优先（INDEX_CANDIDATES 序），
    其次子目录里发现的 marketplace.json；解析不到或归一化后不合法返回 None（调用方
    回退 personal）。仅本地目录转换时由 main 调用。"""
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
            notes.append(f"索引配置 {os.path.relpath(index_path, root)} 的市场名 {raw!r}"
                         " 归一化后不合法，回退 personal")
            return None
        notes.append(f"市场名按索引配置解析为 {market}"
                     f"（{os.path.relpath(index_path, root)} 的 name 字段）")
        return market
    return None


def _expand_indexes(root, indexes, work_dir, notes, limit=0, jobs=1):
    """按索引模式展开索引条目为待转换来源列表（条目级 error 记占位条目；同一插件目录
    被多个条目引用时只转一次，重复条目作为别名记进同一个来源）。
    条目的来源解析（外部 url 条目每个都要回源拉取）按 jobs 并行，结果顺序保持与
    索引条目顺序一致；limit>0 时只解析前 limit 个条目，达到即停——外部 url 条目
    每个都要回源拉取，不限住会让 --limit 在索引仓库上形同虚设。"""
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
            # 技能集合条目（带 skills 数组）即使 source_dir 相同也是不同插件
            # （anthropics/skills 的 5 个条目 source 都是 "./"），不参与合并
            if existing.get("source_dir") == s["source_dir"] and not entry.get("skills"):
                existing.setdefault("aliases", []).append(entry.get("name"))
                break
        else:
            sources.append({"source_dir": s["source_dir"], "repo_root": s["repo_root"],
                            "index_entry": entry, "origin": _entry_origin(entry)})
    return sources


def choose_manifest(repo_root, entry, notes, strict_name=False):
    """在一个仓库里为该索引条目挑插件子目录：唯一则取之；多插件 monorepo 先按
    install_url 指向的子目录匹配，再按插件名匹配，最后取第一个并记 notes。
    strict_name=True 时只接受按插件名匹配成功（单 manifest 也要过名字校验），
    不匹配返回 None——用于「索引条目 path 失效」的同名回退，名字对不上绝不猜。"""
    repo_root_fs = pc.winlong(repo_root)
    manifests = find_plugin_manifests(repo_root_fs)
    if not manifests:
        return None
    if len(manifests) == 1 and not strict_name:
        return manifests[0]
    # 先按插件名匹配（索引的 install_url 存在多条目重复指向同一 manifest 的情况，不可信）
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
    notes.append(f"索引条目 {(entry or {}).get('name')!r} 的仓库有 {len(manifests)} 个插件，"
                 f"未能按名字/install_url 匹配，取第一个: {manifests[0]}")
    return manifests[0]


def _plugin_rel_tokens(value):
    """递归收集 JSON 值里所有指向插件/仓库内文件的相对路径 token。
    返回 [(relpath, scope)]：scope="plugin" 表示相对插件根（./、${PLUGIN_ROOT}、
    $PLUGIN_ROOT），scope="repo" 表示相对仓库根（__REPO_ROOT__，monorepo 里插件
    在子目录时常用它引用仓库级共享源码）。
    裸 token（不带显式前缀）当路径需同时满足：① 整个字符串就这一个 token
    （独立数组元素，如 "args": ["scripts/mcp_server.py"]），或前一个 token 是
    解释器/启动器（node/bash/python3 等，形如 "bash scripts/x.sh" 的命令）；
    ② 不含 @ 和 :（docker 镜像 ghcr.io/org/img@sha256:...、npm 规格 pkg@1.2.3、
    URL 都不是本地路径）；③ 末段不是纯版本号串（"codex-plugin/1.7.0" 这类
    header 值的末段是版本号，不是文件；无扩展名的可执行脚本如
    scripts/gm-mcp-launcher 不受影响）。
    其余多 token 文本（bash 注释、说明文字、HTTP header 值）里的类路径词
    不误判为文件引用——散文 token 常带尾标点或仓库名前缀，按路径检查必落空。"""
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
    """manifest 声明的组件路径在目录里是否存在缺失（判断 awesome 镜像是否不完整）。
    除了 manifest 顶层的 skills/commands/agents/mcpServers/hooks 字段，还深入
    mcpServers/hooks 引用文件（.mcp.json/hooks.json）的内容，检查 args/command 里
    引用的插件内脚本是否也在——镜像常只收清单和文档、漏掉被引用的源码目录。
    __REPO_ROOT__ 开头的引用相对仓库根（repo_root，缺省等于 source_dir）判定；
    其余相对路径按两种口径依次判定——① 相对 manifest 自身所在目录（如
    .codex-plugin/plugin.json 里的 ../skills/ 指向插件根的 skills/）、② 相对插件根
    （真实仓库里两种写法都存在）；两种口径都落空才算缺失。"""
    source_dir_fs = pc.winlong(source_dir)
    repo_root_fs = pc.winlong(repo_root) if repo_root else source_dir_fs
    mrel, _dialect = detect_manifest(source_dir_fs)
    try:
        m = pc.load_json(os.path.join(source_dir_fs, mrel))
    except Exception:  # noqa: BLE001 — 清单本身坏了由转换阶段报错，不算镜像问题
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
            except Exception:  # noqa: BLE001 — 坏 JSON 由转换阶段报
                continue
        for rel, scope in _plugin_rel_tokens(data):
            base = repo_root_fs if scope == "repo" else source_dir_fs
            if not os.path.exists(os.path.normpath(os.path.join(base, rel))):
                return True
    return False


def _entry_origin(entry):
    return entry.get("url") or entry.get("path") or ""


def _manifest_repository_url(source_dir):
    """读插件 manifest 的 repository 字段，返回规范化 git 仓库 URL；缺失/非法返回 None。
    支持字符串（"https://github.com/o/r"）与对象（{"url": ...}）形式；主页/文档站等
    非 git 仓库地址经 classify_source 校验后剔除。"""
    mrel, _d = detect_manifest(source_dir)
    if not mrel:
        return None
    try:
        m = pc.load_json(os.path.join(source_dir, mrel))
    except Exception:  # noqa: BLE001 — 清单本身坏了由转换阶段报，这里按无 repository 处理
        return None
    repo = m.get("repository")
    url = repo if isinstance(repo, str) else (
        repo.get("url") if isinstance(repo, dict) else None)
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    if url.startswith("git+"):
        url = url[4:]  # npm package.json 的 repository 惯例前缀（git+https://...）
    cls = classify_source(url)
    if cls["type"] in ("github", "git"):
        return cls["normalized"]
    return None


def _refetch_full_source(entry, url, pin, source_dir, repo_root, work_dir, notes):
    """镜像/源目录不完整时回源仓库拉全量，返回 (source_dir, repo_root)（可能不变）。
    回源失败、源仓库无 manifest 或同样缺组件时保留原目录并记 notes，绝不中断批次。"""
    try:
        full_root, _ = acquire_repo(url, work_dir, notes, ref=pin)
        full_chosen = choose_manifest(full_root, entry, notes)
        if full_chosen is None:
            notes.append(f"{entry.get('name')!r} 源仓库里找不到可识别的插件 manifest，仍用镜像转换")
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
                    notes.append(f"{entry.get('name')!r} 改选仓库内组件完整的插件目录: {picked}")
                return os.path.normpath(os.path.join(full_root, picked)), full_root
            notes.append(f"{entry.get('name')!r} 源仓库同样缺声明的组件路径，仍用镜像转换")
    except RuntimeError as e:
        notes.append(f"{entry.get('name')!r} 回源仓库拉取失败（{e}），仍用镜像转换")
    return source_dir, repo_root


def _resolve_index_entry(root, entry, work_dir, notes, base_dir=None):
    """解析一个索引条目的插件来源，返回 {source_dir, repo_root} 或 {error}。

    解析规则：
    - 条目带 path：相对索引基准目录（base_dir，缺省 root；marketplace 以「marketplace
      根」、plugins.json 以自身所在目录为基准）解析（绝对路径按原样）；基准目录下不存在
      时回退仓库根 root 再试（记 notes）；path 优先于 url；path 目录不存在时，若仓库内
      能按条目名匹配到唯一插件 manifest（典型：索引指向 ./plugins/<name> 而插件实际在
      仓库根部，如 codex-seo）则同名回退（记 notes），否则 error；
      path 目录里找不到可识别的 manifest 时：条目带 skills 数组（Claude marketplace
      技能集合条目，如 anthropics/skills）按技能集合交 convert_one 组装；否则若 url
      有效则回退 url 来源（记 notes），否则 error；
      path 目录可用但镜像不完整（manifest 声明的组件路径缺失）时，按 manifest
      repository 字段回源仓库拉全量（字段缺失/非法或回源失败时维持镜像并记 notes）。
    - 条目带 url（GitHub）：先查本仓 plugins/<owner>/<repo>/ 镜像，镜像不存在或
      不完整（声明的组件路径缺失）时回源仓库拉全量，并在多个 manifest 中优先选
      组件完整的目录。条目带 sha/ref（官方 Claude marketplace 外部源）时回源按
      钉住的提交拉取；带 subdir（git-subdir 条目）时取仓库内该子目录为插件目录。"""
    base_dir = base_dir or root
    rel_path = entry.get("path")
    url = entry.get("url", "")
    if rel_path:
        # normpath 归一条目里的正斜杠；isdir 判断走 winlong（仓库内深路径不撞 MAX_PATH）
        p = rel_path if os.path.isabs(rel_path) else os.path.normpath(os.path.join(base_dir, rel_path))
        if not os.path.isdir(pc.winlong(p)) and not os.path.isabs(rel_path) \
                and os.path.abspath(base_dir) != os.path.abspath(root):
            alt = os.path.normpath(os.path.join(root, rel_path))
            if os.path.isdir(pc.winlong(alt)):
                notes.append(f"索引条目 path {rel_path!r} 在索引基准目录下不存在，按仓库根解析")
                p = alt
        if not os.path.isdir(pc.winlong(p)):
            chosen = choose_manifest(root, entry, notes, strict_name=True)
            if chosen is not None:
                notes.append(f"索引条目 path {rel_path!r} 不存在，按名称回退仓库内的"
                             f"插件目录: {chosen}")
                return {"source_dir": os.path.normpath(os.path.join(root, chosen)),
                        "repo_root": root}
            return {"error": f"索引条目 path 指向的目录不存在: {rel_path}"}
        repo_root = os.path.abspath(p)
        chosen = choose_manifest(repo_root, entry, notes)
        if chosen is not None:
            source_dir = os.path.normpath(os.path.join(repo_root, chosen))
            # path 条目镜像不完整（声明的组件路径缺失）时，按 manifest repository
            # 字段回源拉全量——path 条目自身不带 url，repository 是作者声明的源仓库；
            # 字段缺失/非法或回源失败时维持镜像（缺失组件由转换阶段记 dropped 降级）
            if declared_paths_missing(source_dir, repo_root):
                repo_url = _manifest_repository_url(source_dir)
                if repo_url:
                    notes.append(f"{entry.get('name')!r} 的镜像不完整（声明的组件路径缺失），"
                                 f"按 manifest repository 回源拉取: {repo_url}")
                    source_dir, repo_root = _refetch_full_source(
                        entry, repo_url, None, source_dir, repo_root, work_dir, notes)
                else:
                    notes.append(f"{entry.get('name')!r} 的镜像不完整（声明的组件路径缺失），"
                                 "但 manifest 无有效 repository 字段，无法回源，仍用镜像转换")
            return {"source_dir": source_dir, "repo_root": repo_root}
        if isinstance(entry.get("skills"), list) and entry["skills"]:
            # Claude marketplace 技能集合条目：无 manifest，skills 数组列出技能目录，
            # 由 convert_one 按数组组装 skill-only 插件（anthropics/skills 形态）
            return {"source_dir": os.path.normpath(p), "repo_root": repo_root}
        if parse_github_url(url):
            notes.append(f"索引条目 {entry.get('name')!r} 的 path 目录里找不到可识别的"
                         f"插件 manifest，回退 url 来源")
        else:
            return {"error": f"索引条目 path 目录里找不到可识别的插件 manifest: {rel_path}"}

    gh = parse_github_url(url)
    if not gh:
        return {"error": f"索引条目既没有有效 path 也没有 GitHub url: {entry.get('name')!r}"}
    owner, repo = gh
    pin = entry.get("sha") or entry.get("ref")  # 外部源钉住的提交/分支（codeload 接受裸 sha）
    subdir = entry.get("subdir")
    mirror = os.path.join(root, "plugins", owner, repo)
    repo_root = mirror if os.path.isdir(pc.winlong(mirror)) else None
    if repo_root is None:
        try:
            repo_root, _ = acquire_repo(url, work_dir, notes, ref=pin)
        except RuntimeError as e:
            return {"error": str(e)}
    if subdir:
        # git-subdir 条目：插件在仓库内子目录，不再做全仓 manifest 扫描
        p = os.path.normpath(os.path.join(repo_root, subdir))
        if not os.path.isdir(pc.winlong(p)):
            return {"error": f"git-subdir 条目指向的子目录在仓库里不存在: {subdir}"}
        return {"source_dir": p, "repo_root": repo_root}
    chosen = choose_manifest(repo_root, entry, notes)
    if chosen is None:
        # cordis/代码型插件仓库的声明式子集判定：无 manifest 但根部有 SKILL.md/skills/
        # 的 skills 类插件按 skill-only 包装；纯代码仓库（仅 package.json 等）明确失败
        if os.path.isfile(pc.winlong(os.path.join(repo_root, "SKILL.md"))) \
                or os.path.isdir(pc.winlong(os.path.join(repo_root, "skills"))):
            notes.append(f"{entry.get('name')!r} 的仓库无可识别 manifest，但根部有"
                         " SKILL.md/skills/，按 skill-only 包装")
            return {"source_dir": repo_root, "repo_root": repo_root}
        return {"error": "仓库里找不到可识别的插件 manifest（kimi.plugin.json / .kimi-plugin / "
                         ".codex-plugin / .claude-plugin / .codebuddy-plugin / gemini-extension.json / "
                         "server.json / plugin.json），根部也无 SKILL.md/skills/；"
                         "代码型插件（如 cordis 代码包）无声明式内容可迁移"}
    source_dir = os.path.normpath(os.path.join(repo_root, chosen))
    if os.path.isdir(pc.winlong(mirror)) and declared_paths_missing(source_dir, repo_root):
        # 镜像只收了部分文件（manifest 声明的组件不在镜像里）→ 回源仓库拉全量
        notes.append(f"{entry.get('name')!r} 的镜像不完整（声明的组件路径缺失），回源仓库拉取")
        source_dir, repo_root = _refetch_full_source(
            entry, url, pin, source_dir, repo_root, work_dir, notes)
    return {"source_dir": source_dir, "repo_root": repo_root}


def discover_sources(root, work_dir, notes, limit=0, jobs=1):
    """返回 [{source_dir, repo_root, index_entry, origin}]：待转换插件的本地源目录 +
    所在仓库根 + 可选索引元数据。

    发现顺序：
      1. 根部索引候选（plugins.json / .agents/plugins/marketplace.json /
         .claude-plugin/marketplace.json；并存时打印候选清单与冲突警告，按
         INDEX_CANDIDATES 优先级选用并记报告）；
      2. 子目录里的 marketplace.json（递归发现，按索引模式展开其中条目）；
      3. 普通仓库模式：全仓扫可识别的插件 manifest；
      4. 一个都没有但有 SKILL.md/skills/ 时整仓当一个 skill-only 插件（走兜底脚手架）。
    索引条目支持 url（GitHub 仓库，走镜像/回源）与 path（仓库内/本地相对路径目录）。
    limit>0 时索引展开只处理前 limit 个条目（外部 url 条目每个都要回源拉取，
    若只在转换阶段截断，--limit 2 也会先 clone 几百个仓库，调试完全失控）。"""
    idx = load_index_file(root, notes)
    indexes = []
    if idx:
        notes.append(f"识别到索引文件 {os.path.relpath(idx['index_path'], root)}"
                     f"（{idx['kind']}，{len(idx['entries'])} 条目）")
        indexes.append(idx)
    else:
        for index_path, base_dir, kind in find_subdir_marketplaces(root):
            entries = _read_index(index_path, kind)
            msg = (f"子目录发现 {kind} 索引 {os.path.relpath(index_path, root)}"
                   f"（{len(entries)} 条目）")
            print(f"[索引] {msg}")
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
        notes.append("仓库没有可识别的插件 manifest，但根部有 SKILL.md/skills/，按 skill-only 插件整仓包装")
        sources.append({"source_dir": root, "repo_root": root,
                        "index_entry": None, "origin": "skill-only-fallback"})
        return sources
    raise RuntimeError("仓库里找不到任何可转换的插件（无 kimi.plugin.json / .kimi-plugin / "
                       ".codex-plugin / .claude-plugin / .codebuddy-plugin / gemini-extension.json / "
                       "server.json / plugin.json，根部也无 SKILL.md/skills/）")


# ---------- 字段转换 ----------
def map_category(raw, notes):
    if not raw:
        return pc.DEFAULT_CATEGORY
    upper = str(raw).strip().upper()
    if upper in pc.CATEGORIES:
        return upper
    mapped = CATEGORY_MAP.get(str(raw).strip().lower())
    if mapped is None:
        notes.append(f"category {raw!r} 无映射，回退 {pc.DEFAULT_CATEGORY}")
        return pc.DEFAULT_CATEGORY
    if mapped != raw:
        notes.append(f"category {raw!r} → {mapped}")
    return mapped


def rewrite_plugin_root_refs(value):
    """字符串里的插件根占位符统一成 '.'（调用方决定怎么拼成 ./ 相对路径）；
    Gemini 的 ${/} 路径分隔符占位符统一成 '/'。"""
    out = str(value)
    for token in PLUGIN_ROOT_TOKENS:
        out = out.replace(token, ".")
    return out.replace("${/}", "/")


def resolve_ref_in_source(source_dir, raw, base_dir=None):
    """解析清单里指向文件的路径引用（mcpServers/hooks 的值）。
    候选按序命中：① 相对 **manifest 自身所在目录**（base_dir，缺省跳过）——如
    .cursor-plugin/plugin.json 写 ./hooks/hooks.json 指 .cursor-plugin/hooks/hooks.json；
    ② 相对插件根（真实仓库里两种写法都存在）；③ 插件根下的同名文件（Codex 仓库常把
    .mcp.json/hooks.json 放在插件根而 manifest 写了越界路径）。找不到返回 None。"""
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
    """mcpServers 条目公共键（enabledTools/disabledTools/startupTimeoutMs/toolTimeoutMs/enabled）
    按 zod schema 类型校验，返回合法键组成的 dict；非法键丢弃记 notes。"""
    out = {}
    for k in ("enabledTools", "disabledTools"):
        if k in cfg:
            v = cfg[k]
            if isinstance(v, list) and all(isinstance(t, str) for t in v):
                out[k] = v
            else:
                notes.append(f"mcpServers.{sname}.{k} 不是字符串数组，丢弃")
    for k in ("startupTimeoutMs", "toolTimeoutMs"):
        if k in cfg:
            v = cfg[k]
            if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                out[k] = v
            else:
                notes.append(f"mcpServers.{sname}.{k} 不是正整数，丢弃")
    # workbuddy 形态的 timeout（毫秒）映射为 toolTimeoutMs（显式 toolTimeoutMs 优先）
    if isinstance(cfg.get("timeout"), (int, float)) and not isinstance(cfg.get("timeout"), bool) \
            and cfg["timeout"] >= 1:
        out.setdefault("toolTimeoutMs", int(cfg["timeout"]))
    if "enabled" in cfg:
        if isinstance(cfg["enabled"], bool):
            out["enabled"] = cfg["enabled"]
        else:
            notes.append(f"mcpServers.{sname}.enabled 不是布尔值，丢弃")
    return out


def convert_mcp_servers(source_dir, raw, notes, ref_base=None):
    """来源方言的 mcpServers（对象或指向 .mcp.json 的路径字符串）→ Kimi 内联对象；无法转换返回 (None, 原因)。
    字符串引用按 manifest 所在目录（ref_base，缺省插件根）解析。"""
    data = raw
    if isinstance(raw, str):
        ref = resolve_ref_in_source(source_dir, raw, base_dir=ref_base)
        if ref is None:
            return None, f"mcpServers 指向的文件不存在: {raw}"
        try:
            data = pc.load_json(ref)
        except Exception as e:  # noqa: BLE001
            return None, f"mcpServers 文件不是合法 JSON: {raw}: {e}"
        notes.append(f"mcpServers 从 {raw} 内联")
    if not isinstance(data, dict):
        return None, f"mcpServers 形态不支持: {type(data).__name__}"
    servers = data.get("mcpServers") or data.get("mcp_servers") or data
    if not isinstance(servers, dict) or not servers:
        return None, "mcpServers 里没有服务器条目"
    out = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            notes.append(f"mcpServers.{name} 不是对象，丢弃")
            continue
        cfg = dict(cfg)
        root_ref_used = any(token in json.dumps(cfg) for token in PLUGIN_ROOT_TOKENS)
        entry = {}
        if cfg.get("url"):
            entry["url"] = cfg["url"]
            # workbuddy 形态：staticHeaders 与 headers 同义，映射为 headers（冲突时 headers 优先）
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
                notes.append(f"mcpServers.{name}.transport {cfg.get('transport')!r} 非法（限 sse/http），丢弃")
        elif cfg.get("command"):
            command = rewrite_plugin_root_refs(cfg["command"])
            if command.startswith(".") and not command.startswith("./"):
                command = "./" + command.lstrip("./")
            if os.path.isabs(command):
                # 绝对路径一律按 PATH 命令名保留（不按解释器白名单分流、不丢弃）：
                # 工具是否在 PATH / 有无公开安装途径由 check_plugin_deps 实际验证，
                # 不可用进「暂缓·依赖不可用」桶，转换阶段不做不可逆丢弃
                base = os.path.basename(command)
                notes.append(f"mcpServers.{name}.command 绝对路径 {command} → 按 PATH 命令 {base} 处理"
                             "（不在 PATH 或无安装途径时由依赖检查标出）")
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
                notes.append(f"mcpServers.{name} 引用了插件根占位符，补 cwd ./")
            elif cfg.get("cwd"):
                cwd = rewrite_plugin_root_refs(cfg["cwd"])
                if cwd.startswith(".") and not cwd.startswith("./"):
                    cwd = "./" + cwd.lstrip("./")
                entry["cwd"] = cwd
        else:
            notes.append(f"mcpServers.{name} 既无 url 也无 command，丢弃")
            continue
        entry.update(sanitize_mcp_common_fields(cfg, name, notes))
        out[str(name)] = entry
    if not out:
        return None, "mcpServers 转换后为空"
    return out, None


def serverjson_to_simple(data, notes):
    """MCP 官方 registry 的 server.json → simple 方言中间形态（随后走 simple shape 统一构造）。

    映射：name 取 reverse-DNS 末段（io.github.x/playwright-mcp → playwright-mcp）；
    packages[]（registryType npm/pypi、transport stdio）→ npx -y <pkg>@<ver> / uvx <pkg>==<ver>
    的 stdio server（runtimeArguments 插在包名前，packageArguments 插在后）；
    remotes[] → url 型 server（streamable-http → transport http）；
    environmentVariables[{name}] → env {NAME: "${NAME}"}（依赖检查会记「需配置环境变量」）。"""
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

    ordered = []  # (key 后缀, mcpServers 条目)
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
            notes.append(f"server.json packages 条目 transport={transport!r} 非 stdio，跳过")
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
            notes.append(f"server.json packages 条目 registryType={reg!r} 暂不支持（支持 npm/pypi），跳过")
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
        notes.append("server.json 无可转换的 packages/remotes 条目")
        return out
    servers = {}
    for i, (tail, entry) in enumerate(ordered):
        key = out["name"] if i == 0 else f"{out['name']}-{tail}"
        servers[key] = entry
    out["mcpServers"] = servers
    return out


def convert_hooks(source_dir, raw, notes, ref_base=None):
    """来源方言的 hooks（数组、或指向 Claude 风格 hooks.json 的路径字符串）→ Kimi [{event, command, timeout?}]。
    字符串引用按 manifest 所在目录（ref_base，缺省插件根）解析。"""
    data = raw
    if isinstance(raw, str):
        ref = resolve_ref_in_source(source_dir, raw, base_dir=ref_base)
        if ref is None:
            return None, f"hooks 指向的文件不存在: {raw}"
        try:
            data = pc.load_json(ref)
        except Exception as e:  # noqa: BLE001
            return None, f"hooks 文件不是合法 JSON: {raw}: {e}"
        notes.append(f"hooks 从 {raw} 内联")
    flat = []
    if isinstance(data, dict):
        groups = data.get("hooks", data)
        if isinstance(groups, (list, dict)) and not groups:
            return None, "hooks 为空，忽略该字段"
        if not isinstance(groups, dict):
            return None, "hooks.json 形态不支持"
        for event, group_list in groups.items():
            if event not in KIMI_HOOK_EVENTS:
                notes.append(f"hook event {event!r} Kimi 不支持，丢弃")
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
                notes.append(f"hook event {event!r} Kimi 不支持，丢弃")
                continue
            if not h.get("command"):
                notes.append(f"hook（{event}）缺 command，丢弃该条目")
                continue
            entry = {"event": event, "command": rewrite_plugin_root_refs(h["command"])}
            if isinstance(h.get("matcher"), str) and h["matcher"]:
                entry["matcher"] = h["matcher"]
            t = h.get("timeout")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and 1 <= t <= 600:
                entry["timeout"] = int(t)
            flat.append(entry)
    else:
        return None, f"hooks 形态不支持: {type(data).__name__}"
    if not flat:
        return None, "hooks 转换后为空"
    return flat, None


def normalize_path_list_field(raw, field, notes, dest=None, base_rel="", dropped_out=None):
    """skills/commands/agents：字符串或字符串数组，统一补 ./ 前缀；空数组返回 None（省略字段）。
    给了 dest（转换产物目录）时校验路径真实存在于插件目录内。声明路径按两种口径依次解析
    （真实仓库里两种写法都存在，见 a-team 的 ../skills/ 与 atomlane 的 ./skills/）：
    ① 相对 **manifest 所在目录**（base_rel，如 ".codex-plugin"）；② 相对插件根。
    命中后重写为相对插件根的 ./ 形式（产物 manifest 在插件根）。两种口径都落空时先试
    同名目录兜底（如 ../skills/ → ./skills/；skills/agents 要求兜底目标是目录，声明成
    文件的按「文件而非目录」丢弃，根 SKILL.md 由 relocate_root_skill_into_skills_dir
    收编进 skills/）；兜底也没有
    就丢弃并记 notes，同时把字段名追加进 dropped_out（调用方据此把「声明了但内容缺失」
    的插件降级，不自动登记）。"""
    entries = raw if isinstance(raw, list) else [raw]
    out = []
    for e in entries:
        if not isinstance(e, str) or not e.strip():
            notes.append(f"{field} 里有非字符串条目，丢弃: {e!r}")
            continue
        e = e.strip()
        if "\\" in e:
            # Windows 风格反斜杠路径分隔符统一归一为 /
            e = e.replace("\\", "/")
            notes.append(f"{field} 路径反斜杠归一为 /: {e}")
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
                # skills/agents 必须是目录；声明成文件（常见为 ./SKILL.md）时丢弃该条目——
                # 根 SKILL.md 由 relocate_root_skill_into_skills_dir 收编进 skills/
                notes.append(f"{field} 声明的是文件而非目录: {e!r}，丢弃（根 SKILL.md 后续收编进 skills/）")
                continue
            if resolved is None:
                base = os.path.basename(rel.rstrip("/"))
                fallback = os.path.join(abs_dest, base)
                ok = (os.path.isdir(fallback) if field in ("skills", "agents")
                      else os.path.exists(fallback))
                if base and ok:
                    notes.append(f"{field} 路径 {e!r} 越界/不存在，用 ./{base}/ 兜底")
                    e = "./" + base + ("/" if e.endswith("/") else "")
                else:
                    notes.append(f"{field} 路径 {e!r} 在插件目录里不存在，丢弃")
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
                    notes.append(f"{field} 路径 {e!r} 解析为 {new_e}")
                e = new_e
        if not e.startswith("./"):
            e = "./" + e
            notes.append(f"{field} 路径补 ./ 前缀: {e}")
        out.append(e)
    if not out:
        return None
    return out if isinstance(raw, list) else out[0]


def ensure_md_frontmatter(md_path, default_name, default_description, notes, display_base=None):
    """SKILL.md / agents .md 缺 frontmatter（或 frontmatter 缺 name/description）时补上——
    没有 frontmatter 的技能/agent 不会被 Kimi 加载（validate_plugin.py 报 ERROR）。
    display_base：notes 路径展示的基准目录（产物目录），缺省只显示文件名。"""
    try:
        with open(md_path, encoding="utf-8") as f:
            content = f.read()
    except Exception:  # noqa: BLE001 — 读不了的文件跳过，交给 validate 报
        return
    desc = re.sub(r"\s+", " ", (default_description or "")).strip() or f"{default_name} 技能"
    # notes 展示相对产物目录（skills/<名>/SKILL.md）：md_path 与 display_base 同侧
    # （都带或不带 \\?\ 前缀），relpath 不会撞 mount；相对 cwd 会产出 ../.. 噪音
    disp = os.path.relpath(md_path, display_base) if display_base else os.path.basename(md_path)
    fm = re.match(r"^---\s*\n(.*?)\n---", content, re.S)
    if not fm:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"---\nname: {default_name}\ndescription: {desc}\n---\n\n" + content)
        notes.append(f"{os.path.basename(md_path)} 缺 frontmatter，已补 name/description（{disp}）")
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
        notes.append(f"{os.path.basename(md_path)} frontmatter 缺字段，已补（{disp}）")


def pick_icon_url(interface, notes):
    """来源方言 interface 里的图标字段（iconURL/icon/logo/logoURL 等）→ Kimi iconUrl（只收直链图片）。"""
    for key in ("iconURL", "iconUrl", "icon", "logoURL", "logo", "thumbnail"):
        url = interface.get(key)
        if isinstance(url, str) and re.match(r"https?://.+\.(png|jpg|jpeg|svg|webp|ico)(\?.*)?$", url, re.I):
            if key != "iconUrl":
                notes.append(f"图标取自 interface.{key}")
            return url
    return None


# ---- 根 SKILL.md 收编为 skills/<名>/ 标准布局 ----
# Kimi 插件的标准技能布局是 skills/<名>/SKILL.md（manifest 显式声明 skills 字段）。
# 技能内的相对路径（references/、scripts/ 等）以 SKILL.md 所在目录为基准解析，
# 所以收编时除「插件根级保留项」外的全部内容跟进 SKILL.md；保留项：
# 插件语义目录（agents/commands/hooks 由 manifest 字段引用，按根布局工作）、
# 插件级文件（manifest、setup.sh、README/LICENSE 等）、manifest 组件字段引用的路径。
ROOT_SKILL_KEEP_DIRS = {"agents", "commands", "hooks", "skills", ".kimi-plugin"}
# 个人市场图标约定按插件根 icon.<ext> 首个匹配读取（validate 同口径），收编时必须留根
ROOT_SKILL_KEEP_FILES = {"kimi.plugin.json", "setup.sh", ".mcp.json",
                         "icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp"}
ROOT_SKILL_KEEP_PREFIXES = ("readme", "license", "licence", "changelog", "notice")


def _manifest_ref_top_tokens(kimi):
    """manifest 组件字段（mcpServers/hooks/systemPromptPath）里相对引用的 top-level
    路径段（./ 前缀或裸写都收）。收编根 SKILL.md 时这些路径必须留在插件根，否则
    组件引用断。命令名（python3）、URL 片段、flag、env 占位等字符串也会产生 token，
    但不会匹配真实根条目，无害。"""
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
    """根 SKILL.md 正文里指向插件根顶层条目的相对引用 top token 集合：
    markdown 链接 [..](..) 目标与 ./ 前缀 token。用于收编时提示
    「指向保留项的引用会断裂」（只告警，不改写用户文档）。"""
    try:
        with open(skill_md_path, encoding="utf-8") as f:
            body = f.read()
    except Exception:  # noqa: BLE001 — 读不了就不告警，收编流程继续
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
    """根 SKILL.md frontmatter 的 name：识别双引号/单引号/裸写三种 YAML 写法，
    返回裸名（引号标量去引号）；读不到/没有返回 None。"""
    try:
        with open(skill_md_path, encoding="utf-8") as f:
            head = f.read(65536)
    except Exception:  # noqa: BLE001 — 读不了的文件跳过，交给 validate 报
        return None
    fm = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
    if not fm:
        return None
    m = re.search(r"^name:\s*(?:\"([^\"]+)\"|'([^']+)'|(.+?))\s*$", fm.group(1), re.M)
    if not m:
        return None
    return next((g for g in m.groups() if g), None)


def _safe_skill_dir_name(name):
    """技能目录名：与 SKILL.md frontmatter name 原样保持一致；仅当含路径分隔或
    Windows 保留字符（或为空、. 开头）时归一化为连字符形式，差异由调用方记 note。"""
    n = name.strip()
    if n and not n.startswith(".") and not re.search(r'[/\\:*?"<>|]', n):
        return n
    safe = re.sub(r'[/\\:*?"<>|\s]+', "-", n).strip("-.")
    return safe or "skill"


def relocate_root_skill_into_skills_dir(kimi, dest_fs, fallback_name, notes):
    """根 SKILL.md 兜底形态收编为 skills/<名>/ 标准布局，并补 skills 字段（"./skills/"）。
    触发条件：manifest 无 skills 字段、根有 SKILL.md、无 skills/ 目录（有 skills/ 目录时
    根 SKILL.md 本就不生效，保持原样）、无 sessionStart（skill 名按目录名校验，收编会
    换目录名）。技能目录名与 SKILL.md frontmatter 的 name 保持一致；frontmatter 缺 name
    时回退插件名（后续 fix_frontmatter_for_manifest 按目录名补 frontmatter，两侧自然
    一致）。技能内相对路径以 SKILL.md 所在目录为基准，故除根级保留项（语义目录/插件级
    文件/manifest 引用路径）外全部跟进。返回 True 表示发生了收编。"""
    if "skills" in kimi or "sessionStart" in kimi:
        return False
    root_skill = os.path.join(dest_fs, "SKILL.md")
    if not os.path.isfile(root_skill):
        return False
    skills_path = os.path.join(dest_fs, "skills")
    if os.path.lexists(skills_path):
        # skills/ 目录已存在（根 SKILL.md 本就不生效），或 skills 名字被普通文件/失效
        # 符号链接占用（收编没有安全落点，makedirs 会抛异常）——都保持根 SKILL.md 原状
        if not os.path.isdir(skills_path):
            notes.append("根部存在非目录条目 skills，收编跳过，保持根 SKILL.md 原状")
        return False
    skill_dir_name = fallback_name
    fm_name = _root_skill_frontmatter_name(root_skill)
    if fm_name:
        skill_dir_name = _safe_skill_dir_name(fm_name)
        if skill_dir_name != fm_name:
            notes.append(f"技能名 {fm_name!r} 含路径保留字符，目录名归一化为 {skill_dir_name!r}")
    keep = (set(ROOT_SKILL_KEEP_DIRS) | set(ROOT_SKILL_KEEP_FILES)
            | _manifest_ref_top_tokens(kimi))
    broken = sorted(_root_skill_body_root_refs(root_skill) & keep)
    if broken:
        notes.append(f"注意：SKILL.md 正文里指向插件根保留项的相对引用收编后会断裂"
                     f"（{'、'.join(broken)}；SKILL.md 收进 skills/{skill_dir_name}/ 后"
                     "这些引用解析不到目标），请人工改为 ../ 形式或移动目标文件")
    target = os.path.join(dest_fs, "skills", skill_dir_name)
    os.makedirs(target)
    moved = []
    for entry in sorted(os.listdir(dest_fs)):
        if entry in keep or entry.lower().startswith(ROOT_SKILL_KEEP_PREFIXES):
            continue
        shutil.move(os.path.join(dest_fs, entry), os.path.join(target, entry))
        moved.append(entry)
    kimi["skills"] = "./skills/"
    notes.append(f"根 SKILL.md 收编为标准布局 skills/{skill_dir_name}/"
                 f"（跟进 {len(moved)} 项: {'、'.join(moved)}），补 skills 字段 ./skills/")
    return True


# ---------- 单插件转换 ----------
def _note_guard_skips(guard, notes):
    """把 source_guard 预扫描的跳过项写进报告 notes（透明可查）。"""
    skipped = (guard or {}).get("skipped_sensitive") or []
    if skipped:
        notes.append("已跳过 %d 个疑似凭证/密钥文件（不打包）: %s%s"
                     % (len(skipped), ", ".join(skipped[:10]),
                        " ..." if len(skipped) > 10 else ""))
    cred_dirs = (guard or {}).get("excluded_credential_dirs") or []
    if cred_dirs:
        notes.append("已排除凭证目录（不打包）: " + ", ".join(cred_dirs[:10]))


def fix_frontmatter_for_manifest(kimi, dest, name, description, notes):
    """补 frontmatter：技能/agent 没有 frontmatter 不会被 Kimi 加载（源仓库的常见缺陷，转换时顺手修）。"""
    raw_skills = kimi.get("skills")
    for sd in (raw_skills if isinstance(raw_skills, list) else [raw_skills]):
        if not isinstance(sd, str):
            continue
        # normpath 归一尾斜杠/正斜杠：dest 可能是 \\?\ 前缀路径（Windows 长路径），
        # 前缀路径不做斜杠转换，直接 join 带 "/" 的字段值会误判不存在
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
    """systemPrompt / systemPromptPath 上限 32 KB（runtime 超限直接忽略，转换时提前拦）。"""
    sp = kimi.get("systemPrompt")
    if isinstance(sp, str) and len(sp.encode("utf-8")) > SYSTEM_PROMPT_MAX_BYTES:
        notes.append(f"systemPrompt 超过 32 KB（{len(sp.encode('utf-8'))} bytes），丢弃")
        del kimi["systemPrompt"]
    spp = kimi.get("systemPromptPath")
    if isinstance(spp, str) and spp.startswith("./"):
        f = os.path.normpath(os.path.join(dest, spp[2:]))
        if os.path.isfile(f) and os.path.getsize(f) > SYSTEM_PROMPT_MAX_BYTES:
            notes.append(f"systemPromptPath 指向的文件超过 32 KB（{os.path.getsize(f)} bytes），丢弃该字段")
            del kimi["systemPromptPath"]


def sanitize_kimi_components(kimi, dest, notes, dropped_out=None):
    """kimi 方言（已是本项目格式）的组件字段按 §4 规范清洗：
    路径字段（skills/agents/commands）补 ./ 前缀并校验存在性；sessionStart 只留 {skill}；
    hooks 条目按 strict schema 只留 event/command/matcher/timeout 且 event 限枚举；
    mcpServers 条目按键白名单过滤并要求 url/command 其一。非法条目/键丢弃记报告；
    声明了但内容缺失被丢弃的字段名追加进 dropped_out（调用方据此降级，不自动登记）。"""
    for field in ("skills", "agents", "commands"):
        if field in kimi:
            value = normalize_path_list_field(kimi[field], field, notes, dest,
                                              dropped_out=dropped_out)
            if value:
                kimi[field] = value
            else:
                notes.append(f"{field} 清洗后为空，省略该字段")
                del kimi[field]
    if "sessionStart" in kimi:
        ss = kimi["sessionStart"]
        if isinstance(ss, dict) and isinstance(ss.get("skill"), str) and ss["skill"].strip():
            kimi["sessionStart"] = {"skill": ss["skill"].strip()}
        else:
            notes.append(f"sessionStart 形态非法（需 {{'skill': '<名>'}}），丢弃: {ss!r}")
            del kimi["sessionStart"]
    if "hooks" in kimi:
        raw = kimi["hooks"]
        if not isinstance(raw, list):
            notes.append(f"hooks 必须是数组，丢弃: {type(raw).__name__}")
            del kimi["hooks"]
        else:
            clean = []
            for h in raw:
                if not isinstance(h, dict):
                    notes.append(f"hooks 条目不是对象，丢弃: {h!r}")
                    continue
                extra = [k for k in h if k not in ("event", "command", "matcher", "timeout")]
                if extra:
                    notes.append("hooks 条目含规范外键，丢弃这些键: " + ", ".join(extra))
                event = h.get("event")
                if event not in KIMI_HOOK_EVENTS:
                    notes.append(f"hook event {event!r} Kimi 不支持，丢弃该条目")
                    continue
                command = h.get("command")
                if not (isinstance(command, str) and command.strip()):
                    notes.append(f"hook（{event}）command 缺失/为空，丢弃该条目")
                    continue
                entry = {"event": event, "command": command}
                if isinstance(h.get("matcher"), str) and h["matcher"]:
                    entry["matcher"] = h["matcher"]
                t = h.get("timeout")
                if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= 600:
                    entry["timeout"] = t
                elif t is not None:
                    notes.append(f"hook（{event}）timeout 非法（限 1-600 整数），丢弃该键")
                clean.append(entry)
            if clean:
                kimi["hooks"] = clean
            else:
                notes.append("hooks 清洗后为空，省略该字段")
                del kimi["hooks"]
    if "mcpServers" in kimi:
        raw = kimi["mcpServers"]
        if not isinstance(raw, dict):
            notes.append(f"mcpServers 必须是对象，丢弃: {type(raw).__name__}")
            del kimi["mcpServers"]
        else:
            clean = {}
            for sname, cfg in raw.items():
                if not isinstance(cfg, dict):
                    notes.append(f"mcpServers.{sname} 不是对象，丢弃")
                    continue
                if cfg.get("url"):
                    allowed = set(MCP_REMOTE_KEYS) | set(MCP_COMMON_KEYS)
                elif cfg.get("command"):
                    allowed = set(MCP_STDIO_KEYS) | set(MCP_COMMON_KEYS)
                else:
                    notes.append(f"mcpServers.{sname} 既无 url 也无 command，丢弃")
                    continue
                extra = [k for k in cfg if k not in allowed]
                if extra:
                    notes.append(f"mcpServers.{sname} 含规范外键，丢弃这些键: " + ", ".join(extra))
                entry = {k: v for k, v in cfg.items() if k in allowed}
                common = sanitize_mcp_common_fields(cfg, sname, notes)
                for k in set(entry) & set(MCP_COMMON_KEYS):
                    del entry[k]
                entry.update(common)
                clean[str(sname)] = entry
            if clean:
                kimi["mcpServers"] = clean
            else:
                notes.append("mcpServers 清洗后为空，省略该字段")
                del kimi["mcpServers"]


def convert_one(source_dir, name_hint, index_entry, output_dir, used_names, repo_root=None,
                forced_manifest=None, used_names_lock=None):
    """转换一个插件。返回报告条目 dict（含 success/failure 与原因）。
    repo_root：插件所在仓库的根（monorepo 子目录插件的 __REPO_ROOT__ 引用按它解析，
    引用到的仓库级共享源码会复制进插件产物目录）。
    forced_manifest：(相对路径, 方言)，blob 严格入口用，跳过平台检测。
    多平台 manifest 并存时按 MANIFEST_CANDIDATES 顺序自动选第一个方言，
    并把被跳过的方言记进报告 notes（不静默选定）。
    used_names_lock：并行转换时保护 used_names 查重-占位临界区的锁（串行为 None）。"""
    notes = []
    entry = {"source_dir": source_dir, "origin": None, "success": False,
             "notes": notes, "kimi_name": None, "output_dir": None, "error": None}
    if index_entry:
        entry["origin"] = _entry_origin(index_entry)
        entry["index_name"] = index_entry.get("name")

    if forced_manifest:
        # blob 严格入口：直接用指定的 manifest（含方言），不再做平台检测
        manifest_rel, dialect = forced_manifest
    else:
        found = detect_all_manifests(source_dir)
        platforms = list(dict.fromkeys(d for _rel, d in found if d in PLATFORM_DIALECTS))
        if len(platforms) > 1:
            # 多平台定义并存：按 MANIFEST_CANDIDATES 顺序自动取第一个方言，
            # 被跳过的方言记进报告（可见、可回溯，不打断批量转换）
            manifest_rel, dialect = found[0]
            notes.append(
                f"多平台定义并存（{'、'.join(platforms)}），自动按 {dialect} 转换"
                f"（{'、'.join(platforms[1:])} 未采用）")
        else:
            manifest_rel, dialect = found[0] if found else (None, None)
    codex = {}
    coll_skills = (index_entry or {}).get("skills") if index_entry else None
    # 产物落点可能叠出 >260 深路径（output 前缀 + market/插件名 + 插件自带深目录），
    # Windows 上文件操作一律走 \\?\ 前缀路径（pc.winlong，见 plugin_common）；
    # 报告与 entry 里保留干净路径
    source_dir_fs = pc.winlong(source_dir)
    # manifest 所在目录（如 ".codex-plugin"）：来源 manifest 里的相对路径按它解析，
    # 产物 manifest 在插件根，转换时统一重写为插件根相对的 ./ 路径
    manifest_dir_abs = (os.path.normpath(os.path.join(source_dir_fs, os.path.dirname(manifest_rel)))
                        if manifest_rel else source_dir_fs)
    manifest_dir_rel = os.path.dirname(manifest_rel) if manifest_rel else ""
    dropped_components = []  # 声明了但内容缺失被丢弃的组件字段（降级依据，见报告 dropped_components）
    if manifest_rel:
        try:
            codex = pc.load_json(os.path.join(source_dir_fs, manifest_rel))
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{manifest_rel} 不是合法 JSON: {e}"
            return entry
        if dialect == "kimi":
            shape = "kimi"       # 已是本项目格式：轻转换
        elif dialect == "codex":
            shape = "codex"      # Codex 方言：全量转换
        elif dialect in ("claude", "codebuddy", "cursor"):
            shape = "simple"     # Claude / CodeBuddy / Cursor 方言：全量转换（无 interface 可映射）
        elif dialect == "gemini":
            shape = "simple"     # Gemini 扩展方言：mcpServers 同构（${extensionPath} 已按
            # 插件根占位符改写），contextFileName 是每个会话注入的上下文 → systemPromptPath
            cfn = codex.pop("contextFileName", None)
            if isinstance(cfn, str) and cfn.strip():
                codex["systemPromptPath"] = "./" + rewrite_plugin_root_refs(cfn.strip()).lstrip("./")
                notes.append(f"contextFileName {cfn!r} 映射为 systemPromptPath")
        elif dialect == "serverjson":
            shape = "simple"     # MCP 官方 registry server.json → 包成仅 mcpServers 的插件
            codex = serverjson_to_simple(codex, notes)
        else:  # generic：内容含 interface 按 Codex 方言处理，否则按简单方言
            shape = "codex" if isinstance(codex.get("interface"), dict) else "simple"
    elif isinstance(coll_skills, list) and coll_skills:
        # Claude marketplace 技能集合条目（如 anthropics/skills：{"source": "./",
        # "skills": ["./skills/x", ...]}）：无 manifest，按数组列出的技能目录组装
        # skill-only 插件，不整仓复制
        dialect = "skill-collection"
        shape = "simple"
        notes.append("索引条目为技能集合（skills 数组），按数组组装 skill-only 插件")
    elif not (os.path.isfile(os.path.join(source_dir_fs, "SKILL.md"))
              or os.path.isdir(os.path.join(source_dir_fs, "skills"))):
        entry["error"] = "找不到可识别的插件 manifest，也无 SKILL.md/skills/ 可兜底"
        return entry
    else:
        dialect = "skill-only"
        shape = "simple"
        notes.append("无插件 manifest，按 skill-only 兜底包装")
    entry["dialect"] = dialect

    # 权利限制扫描（打包/登记前置，转换照常完成）：许可语境短语命中 → severity=high；
    # 厂商/平台信号 → severity=notice；两类都进 registration_plan 的
    # hold_rights_review 暂缓桶，用户审阅报告后用 register_converted.py --only 放行。
    # 方言 manifest（如 .codex-plugin/plugin.json）的 license 字段不在顶层文件名
    # 扫描范围内，作为额外许可语境传入。
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
            notes.append(f"权利扫描命中（severity={entry['rights']['severity']}）："
                         "登记暂缓，用户审阅 plugin-builder-report.md 后 --only 放行")
        for w in rights["warnings"]:
            notes.append(f"权利扫描警告（README 正文命中，仅提示不拒绝）: "
                         f"{w['category']} @ {w['file']}")
    except Exception as e:  # noqa: BLE001 — 扫描自身异常不阻断转换，如实记录后继续
        notes.append(f"权利扫描异常（按未命中继续）: {type(e).__name__}: {e}")

    raw_name = str(codex.get("name") or name_hint or (index_entry or {}).get("name")
                   or os.path.basename(source_dir))
    try:
        name = pc.validate_plugin_name(pc.normalize_plugin_name(raw_name))
    except ValueError as e:
        entry["error"] = f"插件名不可用: {e}"
        return entry
    if name != raw_name:
        notes.append(f"name {raw_name!r} 归一化为 {name!r}")
    def claim_name(n):
        """查重-占位必须原子：并行转换时由 used_names_lock 保护。"""
        base, i = n, 2
        while n in used_names:
            n = f"{base}-{i}"
            i += 1
        if n != base:
            notes.append(f"重名，改为 {n}")
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
        # 技能集合条目：只收 skills 数组列出的技能目录到 dest/skills/<名>/，不整仓复制
        os.makedirs(dest_fs)
        copied = 0
        for sd in coll_skills:
            if not isinstance(sd, str) or not sd.strip():
                continue
            src = os.path.normpath(os.path.join(source_dir_fs, sd))
            if not os.path.isdir(src):
                notes.append(f"skills 数组中的目录不存在，跳过: {sd}")
                continue
            try:
                guard = sg.check_source_tree(src)
            except sg.SourceGuardError as e:
                entry["error"] = f"技能目录 {sd} 安检未通过: {e}"
                return entry
            _note_guard_skips(guard, notes)
            shutil.copytree(src, os.path.join(dest_fs, "skills", os.path.basename(src)),
                            ignore=sg.make_copy_ignorer(COPY_EXCLUDE_DIRS, guard))
            copied += 1
        if not copied:
            entry["error"] = "skills 数组列出的技能目录都不存在，无法组装"
            return entry
        notes.append(f"按索引 skills 数组组装 {copied} 个技能目录")
    else:
        try:
            guard = sg.check_source_tree(source_dir_fs)
        except sg.SourceGuardError as e:
            entry["error"] = f"源目录安检未通过: {e}"
            return entry
        _note_guard_skips(guard, notes)
        shutil.copytree(source_dir_fs, dest_fs,
                        ignore=sg.make_copy_ignorer(copy_excludes(dialect), guard))
    entry["output_dir"] = dest

    # monorepo 子目录插件：__REPO_ROOT__ 引用的仓库级共享源码（如仓库根 mcp/ 目录）
    # 复制进插件产物目录，否则转换后的插件引用的脚本不存在、跑不起来
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
                        notes.append(f"仓库级共享源码 {top} 安检未通过，未复制: {e}")
                        continue
                    _note_guard_skips(top_guard, notes)
                    shutil.copytree(src_p, dst_p,
                                    ignore=sg.make_copy_ignorer(COPY_EXCLUDE_DIRS, top_guard))
                else:
                    if sg.is_sensitive_file(top):
                        notes.append(f"仓库级共享源码 {top} 命中疑似凭证/密钥文件规则，未复制")
                        continue
                    shutil.copy2(src_p, dst_p)
                copied_tops.add(top)
                notes.append(f"仓库级共享源码 {top} 从仓库根复制进插件（__REPO_ROOT__ 引用）")

    if shape == "kimi":
        # 已是本项目格式：轻转换——按字段规范白名单过滤（规范外字段丢弃记报告），
        # 并校正 name/version/interface.category
        dropped = [k for k in codex if k not in SPEC_TOP_FIELDS]
        if dropped:
            notes.append("丢弃规范外顶层字段: " + ", ".join(sorted(dropped)))
        kimi = {k: v for k, v in codex.items() if k in SPEC_TOP_FIELDS}
        if not str(kimi.get("license") or "").strip():
            # license 诚实标注：外部来源提取不到标 UNKNOWN，不默认 MIT
            kimi["license"] = rs.detect_license(source_dir_fs)
            notes.append(f"license 缺失，按 LICENSE 文件检测补齐: {kimi['license']}"
                         "（提取不到标 UNKNOWN，不默认 MIT）")
        if str(kimi.get("name", "")).strip() != name:
            notes.append(f"manifest name {kimi.get('name')!r} 归一化为 {name!r}")
        kimi["name"] = name
        version = str(kimi.get("version") or "").strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", version):
            notes.append(f"version {version!r} 缺失/非标准 semver，回退 0.1.0")
            kimi["version"] = "0.1.0"
        itf = kimi.get("interface")
        if not isinstance(itf, dict):
            itf = {}
            notes.append("补 interface 字段")
        else:
            dropped_itf = [k for k in itf if k not in SPEC_INTERFACE_FIELDS]
            if dropped_itf:
                notes.append("丢弃规范外 interface 字段: " + ", ".join(sorted(dropped_itf)))
            itf = {k: v for k, v in itf.items() if k in SPEC_INTERFACE_FIELDS}
        kimi["interface"] = itf
        if itf.get("hostKind") is not None and itf["hostKind"] not in SPEC_HOST_KINDS:
            notes.append(f"interface.hostKind {itf['hostKind']!r} 非法（限 hosted/local），丢弃")
            del itf["hostKind"]
        if itf.get("platforms") is not None:
            if (isinstance(itf["platforms"], list)
                    and all(p in SPEC_PLATFORMS for p in itf["platforms"])):
                pass
            else:
                notes.append(f"interface.platforms 含非法平台（限 {'/'.join(SPEC_PLATFORMS)}），丢弃")
                del itf["platforms"]
        if not itf.get("category"):
            itf["category"] = map_category((index_entry or {}).get("category"), notes)
            notes.append(f"补 interface.category（Kimi 市场必需）: {itf['category']}")
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
            # manifest 声明了组件但内容在源目录缺失——报告可见，依赖检查阶段降级为「不可用」
            entry["dropped_components"] = sorted(dropped_components)
        hook_details = _extract_hook_details(kimi)
        if hook_details:
            entry["hooks"] = hook_details
        if os.path.isfile(os.path.join(dest_fs, "setup.sh")):
            entry["setup_sh"] = True
        entry["success"] = True
        notes.append("已是 Kimi 格式，按轻转换处理（仅校正必需字段）")
        return entry

    interface = codex.get("interface") if isinstance(codex.get("interface"), dict) else {}
    display = interface.get("displayName") or (index_entry or {}).get("name") \
        or pc.display_name_from_plugin_name(name)
    description = codex.get("description") or (index_entry or {}).get("description") \
        or f"{display} 插件"

    version = str(codex.get("version") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?", version):
        if version:
            notes.append(f"version {version!r} 非标准 semver，回退 0.1.0")
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
        # license 诚实标注：外部来源提取不到标 UNKNOWN，不默认 MIT
        license_ = rs.detect_license(source_dir_fs)
        notes.append(f"manifest 未声明 license，按 LICENSE 文件检测结果标注: {license_}"
                     "（提取不到标 UNKNOWN，不默认 MIT）")

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

    # skills：显式字段优先；缺省时 skills/ 目录存在则补上（与 Kimi manifest 解析的目录语义一致）
    if "skills" in codex:
        skills = normalize_path_list_field(codex["skills"], "skills", notes, dest_fs,
                                           base_rel=manifest_dir_rel,
                                           dropped_out=dropped_components)
        if skills:
            kimi["skills"] = skills
    elif os.path.isdir(os.path.join(dest_fs, "skills")):
        kimi["skills"] = "./skills/"
        notes.append("补 skills 字段 ./skills/")
    # 根 SKILL.md（无 skills 声明）不再依赖 Kimi root fallback，
    # 由 relocate_root_skill_into_skills_dir 统一收编为 skills/<名>/ 标准布局

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
            notes.append(f"mcpServers 未转换: {reason}")
            if reason and "指向的文件不存在" in reason:
                dropped_components.append("mcpServers")
    else:
        # 常见约定：清单不写 mcpServers 但根部有 mcp 配置文件 —— 内联它。
        # 优先 .mcp.json（带点号，既有约定），其次 mcp.json（无点号，
        # cursor 插件与 workbuddy 连接器的形态）
        for mcp_name in (".mcp.json", "mcp.json"):
            mcp_file = os.path.join(source_dir_fs, mcp_name)
            if not os.path.isfile(mcp_file):
                continue
            servers, reason = convert_mcp_servers(source_dir_fs, "./" + mcp_name, notes)
            if servers:
                apply_mcp(servers)
                notes.append(f"清单未声明 mcpServers，从根部 {mcp_name} 内联")
            elif reason:
                notes.append(f"{mcp_name} 内联失败: {reason}")
            break

    if "hooks" in codex:
        hooks, reason = convert_hooks(source_dir_fs, codex["hooks"], notes,
                                      ref_base=manifest_dir_abs)
        if hooks:
            kimi["hooks"] = hooks
        else:
            notes.append(f"hooks 未转换: {reason}")
            if reason and "指向的文件不存在" in reason:
                dropped_components.append("hooks")
    elif shape == "simple" and os.path.isfile(os.path.join(source_dir_fs, "hooks", "hooks.json")):
        # Claude 约定：manifest 不写 hooks，但 hooks/hooks.json 存在 —— 内联它
        hooks, reason = convert_hooks(source_dir_fs, "./hooks/hooks.json", notes)
        if hooks:
            kimi["hooks"] = hooks
            notes.append("hooks 未声明，按 Claude 约定从 ./hooks/hooks.json 内联")
        else:
            notes.append(f"hooks 未转换: {reason}")

    for field in ("commands", "agents"):
        if field in codex:
            value = normalize_path_list_field(codex[field], field, notes, dest_fs,
                                              base_rel=manifest_dir_rel,
                                              dropped_out=dropped_components)
            if value:
                kimi[field] = value
        elif os.path.isdir(os.path.join(dest_fs, field)):
            # 目录存在但没任何 .md 时不补字段（来源平台的配置文件如 agents/openai.yaml，
            # Kimi 只认 .md；补了 validate 会报「agents/ 下没找到任何 .md 文件」）。
            # agents 只看顶层（与 validate 一致），commands 递归找。
            d = os.path.join(dest_fs, field)
            if field == "agents":
                has_md = any(f.endswith(".md") and os.path.isfile(os.path.join(d, f))
                             for f in os.listdir(d))
            else:
                has_md = any(f.endswith(".md") for _dp, _dn, fns in os.walk(d) for f in fns)
            if has_md:
                kimi[field] = f"./{field}/"
                notes.append(f"补 {field} 字段 ./{field}/")
            else:
                notes.append(f"{field}/ 目录无任何 .md 文件（来源平台专有配置），不补 {field} 字段")

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
        notes.append("丢弃来源方言专有顶层字段: " + ", ".join(dropped_top))
    if dropped_itf:
        notes.append("丢弃来源方言专有 interface 字段: " + ", ".join(dropped_itf))

    relocate_root_skill_into_skills_dir(kimi, dest_fs, name, notes)
    enforce_system_prompt_limits(kimi, dest_fs, notes)
    fix_frontmatter_for_manifest(kimi, dest_fs, name, description, notes)

    pc.write_json(os.path.join(dest_fs, "kimi.plugin.json"), kimi, force=True)

    components = [k for k in ("skills", "mcpServers", "hooks", "commands", "agents",
                              "sessionStart", "systemPrompt", "systemPromptPath") if k in kimi]
    entry["components"] = components
    entry["license"] = str(kimi.get("license") or "")
    if dropped_components:
        # manifest 声明了组件但内容在源目录缺失（镜像/源仓库不完整）——
        # 报告可见，并在依赖检查阶段降级为「不可用」，不随流程自动登记
        entry["dropped_components"] = sorted(dropped_components)
    hook_details = _extract_hook_details(kimi)
    if hook_details:
        entry["hooks"] = hook_details
    if os.path.isfile(os.path.join(dest_fs, "setup.sh")):
        entry["setup_sh"] = True
    entry["success"] = True
    return entry


# ---------- 报告 ----------
def _extract_hook_details(kimi):
    """产物 manifest 的 hooks → 报告明细 [{event, command}]；无 hooks 返回 None。"""
    hooks = kimi.get("hooks")
    if not isinstance(hooks, list):
        return None
    detail = [{"event": str(h.get("event", "")), "command": str(h.get("command", ""))}
              for h in hooks if isinstance(h, dict) and h.get("command")]
    return detail or None


REPORT_MD_NAME = "plugin-builder-report.md"


def render_plugin_builder_report(summary, registration=None):
    """渲染唯一人读报告 plugin-builder-report.md 的完整文本。

    summary：conversion-report.json 的内容（转换数据与 registration_plan）；
    registration：registration-report.json 的内容（登记结果），None 表示第②步
    未执行（登记段落写占位说明）。convert 与 register_converted 共用本函数整篇
    重写——后跑者把登记段落填实，重跑 convert 时回到占位态（旧登记结果随之作废）。
    两个 json 与 marketplace.orig 是机器合同/快照，只在文末注记，不作为交付物。"""
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
        # md 报告给人看：超长 hook 命令截断（单行可达 1700+ 字符，既难读也让
        # grep 命中整行噪声）；全文保留在 conversion-report.json 的 hooks 字段。
        cmd = str(cmd)
        return cmd if len(cmd) <= limit else cmd[:limit] + "…（全长见 conversion-report.json）"

    def _hold_lines(r):
        out = [f"  - hook（会话事件自动执行）: {h['event']} → `{_clip_command(h['command'])}`"
               for h in r.get("hooks") or []]
        if r.get("setup_sh"):
            out.append("  - setup.sh（本体/依赖安装脚本，转换与登记脚本均不执行；"
                       "请用户审阅后自行执行）")
        return out

    def _names_lines(items, label):
        names = [h["name"] for h in items]
        out = [f"- {label}: {len(items)}"]
        if names:
            shown = ", ".join(names[:20]) + (f" …等 {len(names)} 个" if len(names) > 20 else "")
            out.append(f"    {shown}")
        return out

    lines = ["# 插件构建报告（外部仓库 → Kimi 插件 → 个人市场）", "",
             f"- 输入来源: {source}",
             f"- 市场: {market or 'personal'}",
             f"- 插件总数: {total}",
             f"- 转换成功且真正可用（validate 0 error + 依赖检查通过）: {len(usable)}",
             f"- 转换成功但依赖不可用（缺 tool/脚本/服务，原因见下）: {len(unusable)}",
             f"- 转换成功但校验有 error: {len(invalid)}",
             f"- 转换失败: {len(failed)}",
             f"- 登记计划（registration_plan，register_converted.py 的输入）: "
             f"可登记 {len(plan.get('registerable') or [])}，"
             f"暂缓·权利待审 {len(plan.get('hold_rights_review') or [])}，"
             f"暂缓·hooks/setup.sh {len(plan.get('hold_hooks_or_setup') or [])}，"
             f"暂缓·依赖不可用 {len(plan.get('hold_deps_unavailable') or [])}，"
             f"暂缓·校验 error {len(plan.get('hold_validate_errors') or [])}，"
             f"失败 {len(plan.get('failed') or [])}", "",
             "可用性口径：以个人页签安装路径为准——结构校验（validate_plugin.py，对齐 "
             "kernel parseManifest 与 daimon 登记门禁）+ 依赖检查（check_plugin_deps.py，"
             "对照本项目运行时内置工具集，核实 MCP server 命令/包/URL、hooks 引用脚本、"
             "SKILL.md 引用的 MCP server 是否真实存在可装）。客户端「从文件安装」（侧载）"
             "对 mcpServers 的限制仅是侧载自身约束，不作为可用性减分项。"
             "权利扫描（rights_scan.py）：许可语境权利限制短语（severity=high）与"
             "厂商/平台信号（severity=notice）命中的插件进暂缓桶，审阅本报告后"
             "用 register_converted.py --only 放行；疑似凭证/密钥文件与越界 symlink "
             "在打包前已被 source_guard.py 跳过/拒绝。", ""]

    lines += ["## 登记结果", ""]
    if registration is None:
        lines += ["未执行——第②步运行 `register_converted.py <本目录>` 后本节自动填充："
                  "登记成败、失败原因、注册表对账与插件链接。", ""]
    else:
        reg = registration
        registered = reg.get("registered") or []
        failed_reg = reg.get("failed") or []
        held = reg.get("held") or {}
        recon = reg.get("reconciliation") or {}
        lines.append(f"- 登记时间: {reg.get('generated_at')}")
        lines.append(f"- 登记目标: {reg.get('targets')} 个")
        lines.append(f"- 登记成功: {len(registered)}")
        if failed_reg:
            lines.append(f"- 登记失败: {len(failed_reg)}")
            for f in failed_reg:
                lines.append(f"  - {f['name']}: {f['error']}")
        else:
            lines.append("- 登记失败: 0")
        lines += _names_lines(held.get("hold_rights_review") or [],
                              "暂缓·权利待审（需用户审阅后才登记）")
        lines += _names_lines(held.get("hold_hooks_or_setup") or [],
                              "暂缓·hooks/setup.sh（需用户审阅后才登记）")
        lines += _names_lines(held.get("hold_deps_unavailable") or [], "暂缓·依赖不可用")
        if held.get("hold_validate_errors"):
            lines += _names_lines(held["hold_validate_errors"], "暂缓·校验 error")
        if held.get("failed"):
            lines += _names_lines(held["failed"], "转换失败")
        lines.append(f"- 注册表对账: {reg.get('registry_dir')} 现有 "
                     f"{recon.get('registry_entries', 0)} 个条目"
                     f"（本次登记 {recon.get('registered_this_run', 0)}，"
                     f"此前已有 {recon.get('pre_existing', 0)}；marketplace.orig 不是条目）")
        missing = recon.get("missing_entries") or []
        if missing:
            lines.append(f"  警告：{len(missing)} 个插件登记成功但注册表条目缺失: "
                         f"{', '.join(missing)}")
        links = [r for r in registered if r.get("link")]
        if links:
            lines.append(f"- 插件链接: 完整 {len(links)} 个见同目录 registration-report.json "
                         f"的 registered[].link；示例:")
            for r in links[:3]:
                lines.append(f"  - [{r['name']}]({r['link']})")
        lines.append("")

    if notes:
        lines += ["## 全局说明", ""] + [f"- {n}" for n in notes] + [""]
    rights_hits = [r for r in results if (r.get("rights") or {}).get("severity")]
    if rights_hits:
        lines += ["## 权利扫描命中（登记暂缓，用户审阅后 --only 放行）", ""]
        for r in rights_hits:
            rights = r["rights"]
            name = r.get("kimi_name") or r.get("index_name") or r.get("origin") \
                or r.get("source_dir")
            lines.append(f"- **{name}** ← {r.get('origin') or r.get('source_dir')}"
                         f"｜severity: {rights['severity']}")
            for m in rights.get("restricted") or []:
                lines.append(f"  - 权利限制: {m['category']} @ {m['file']}（{m['context']}）")
            for s in rights.get("vendor") or []:
                lines.append(f"  - 厂商/平台信号: {s['signal']}={s['value']} @ {s['source']}")
        lines.append("")
    lines += ["## 转换成功且可用", ""]
    for r in usable:
        alias = f"（索引别名: {', '.join(r['aliases'])}）" if r.get("aliases") else ""
        warns = (r.get("usability") or {}).get("warnings") or []
        warn_note = f"｜注意 {len(warns)} 项" if warns else ""
        dialect_note = f"｜来源方言: {r['dialect']}" if r.get("dialect") else ""
        license_note = f"｜license: {r['license']}" if r.get("license") else ""
        lines.append(f"- **{r['kimi_name']}** ← {r.get('origin') or r['source_dir']}{alias}"
                     f"｜组件: {', '.join(r.get('components') or ['无'])}{dialect_note}{license_note}{warn_note}")
        lines.extend(_hold_lines(r))
        for n in r["notes"]:
            lines.append(f"  - {n}")
        for w in warns:
            lines.append(f"  - 注意: {w}")
    if unusable:
        lines += ["", "## 转换成功但依赖不可用", ""]
        for r in unusable:
            lines.append(f"- **{r['kimi_name']}** ← {r.get('origin') or r['source_dir']}")
            lines.extend(_hold_lines(r))
            for b in (r.get("usability") or {}).get("blockers", []):
                lines.append(f"  - 不可用原因: {b}")
    if invalid:
        lines += ["", "## 转换成功但校验有 error", ""]
        for r in invalid:
            lines.append(f"- **{r['kimi_name']}** ← {r.get('origin') or r['source_dir']}")
            lines.extend(_hold_lines(r))
            for e in r["validate_errors"]:
                lines.append(f"  - 校验: {e}")
    if failed:
        lines += ["", "## 转换失败", ""]
        for r in failed:
            name = r.get("index_name") or r.get("kimi_name") or r.get("origin") or r.get("source_dir")
            lines.append(f"- **{name}**：{r.get('error')}")
    lines += ["", "---", "",
              "机器可读明细（同目录，供工具与 agent 读取，不作为会话交付物）："
              "`conversion-report.json`（转换数据与 registration_plan）、"
              "`registration-report.json`（登记结果与完整链接）、"
              "`marketplace.orig`（源市场静态快照，生成后不再变）。", ""]
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

    # 重新 convert 会使上一轮登记结果过期：作废 registration-report.json（登记段落
    # 回到占位态），并清理旧版报告名 conversion-report.md——它们都是本工具的产物。
    stale_registration = os.path.join(output_dir, "registration-report.json")
    if os.path.isfile(stale_registration):
        os.remove(stale_registration)
        print("[报告] 旧 registration-report.json 已随本次转换作废（登记结果以重跑 register_converted.py 后为准）")
    legacy_md = os.path.join(output_dir, "conversion-report.md")
    if os.path.isfile(legacy_md):
        os.remove(legacy_md)
        print("[报告] 旧版 conversion-report.md 已清理（统一为 plugin-builder-report.md）")

    md_path = os.path.join(output_dir, REPORT_MD_NAME)
    with open(pc.winlong(md_path), "w", encoding="utf-8") as f:
        f.write(render_plugin_builder_report(summary, None))
    return md_path, json_path


# ---------- marketplace.orig（源市场全量清单，转换时生成一次，之后为静态快照） ----------
def _orig_entry_from_index_entry(entry):
    """索引条目 → orig 条目（含未转换/失败的条目；source 取 url 或 path）。"""
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
    """插件目录 → orig 条目（读首个可识别 manifest 的 name/description/version；
    无 manifest 的 skill-only 目录用目录名）。"""
    for rel, _d in MANIFEST_CANDIDATES:
        p = os.path.join(plugin_dir, rel)
        if not os.path.isfile(p):
            continue
        try:
            m = pc.load_json(p)
        except Exception:  # noqa: BLE001 — 坏 manifest 在转换报告里记，orig 跳过
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
    """收集源市场的**全量**插件清单（marketplace.orig 的内容）：

    - 索引仓库（根部 plugins.json / marketplace.json，或子目录发现的 marketplace.json）：
      取**全部索引条目**——包括未转换、转换失败、依赖不可用的条目，不受 --limit 与
      转换范围影响；
    - 无索引仓库（单插件 / monorepo / tree 子路径输入）：**全仓扫描**插件 manifest——
      即使 tree 子路径只转换其中一个子目录，orig 也记录全仓识别到的所有插件；
    - skill-only 兜底仓库：清单为仓库自身一条。
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
    """把源市场全量清单写到 <market_dir>/marketplace.orig（仿 Claude marketplace
    格式）。只在转换时生成；之后的登记/安装/卸载都不改变它的内容。"""
    doc = {"name": market,
           "plugins": sorted(plugins, key=lambda p: p["name"])}
    pc.write_json(os.path.join(market_dir, "marketplace.orig"), doc, force=True)
    return os.path.join(market_dir, "marketplace.orig")


def parse_args():
    p = argparse.ArgumentParser(description="把插件/技能仓库（任意来源方言）批量转换为 Kimi 插件")
    p.add_argument("source", help="GitHub 仓库 URL（https/git@，支持 tree/blob 子路径；"
                                  "github.com/<o>/<r> 与 <o>/<r> 简写自动补全）或本地目录")
    p.add_argument("--output-dir", default=str(pc.resolve_plugin_sources_dir()),
                   help="转换产物根目录（默认 daimon-share 下的 plugin-sources/，与 create_plugin.py 一致）；"
                        "产物实际落在 <output-dir>/<market>/<name>/，报告在 <output-dir>/<market>/")
    p.add_argument("--market", default=None,
                   help="目标市场名（缺省：本地目录转换时从索引配置的顶层 name 解析，"
                        "解析不到回退 personal）；显式指定时优先于索引配置")
    p.add_argument("--force", action="store_true",
                   help="允许覆盖已有市场报告和同名插件源码（包括手工修改）；须先确认可丢弃旧内容")
    p.add_argument("--limit", type=int, default=0,
                   help="最多展开并转换前多少个索引条目（0 = 不限，调试用；"
                        "指向同一插件的别名条目也占名额，与转换出的插件数不是同一口径）")
    p.add_argument("--work-dir", default=None,
                   help="拉取仓库的临时目录（默认系统临时目录，运行结束自动清理；"
                        "显式指定时该目录不会被清理，由调用方负责）")
    p.add_argument("--offline", action="store_true",
                   help="依赖检查不联网（跳过 npm/PyPI registry 与 MCP URL 探测）")
    p.add_argument("--jobs", type=int, default=8,
                   help="并行线程数：索引条目回源拉取与逐插件转换/校验/依赖检查都按它并行"
                        "（默认 8，1 = 串行）")
    return p.parse_args()


def main():
    # 中文输出钉死 UTF-8：Windows GBK locale + 管道捕获下 print 会 UnicodeEncodeError
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
        # work_dir 只是本次运行的 clone 缓存（每次运行新建，跨运行不复用）：
        # 自建临时目录运行结束即清理（含异常退出）；显式 --work-dir 由调用方负责。
        if cleanup_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def _convert(a, output_dir, notes, work_dir):

    # 显式 --market 优先于一切配置解析；归一化后与输入不一致会提示，归一化后仍非法直接拒绝。
    market = None
    if a.market:
        try:
            market = pc.validate_market_name(pc.normalize_market_name(a.market))
        except ValueError as e:
            raise SystemExit(f"错误: {e}") from e
        if market != a.market.strip():
            notes.append(f"market 名归一化为 {market}（输入 {a.market!r}）")

    target = None
    cls = classify_source(a.source)
    if cls["type"] == "rejected":
        raise SystemExit(f"错误: {cls['reason']}")
    if cls["type"] == "github":
        target = cls["target"]
    if cls["type"] in ("github", "git") and cls["normalized"] != a.source.strip():
        notes.append(f"输入已按约定规范化为 {cls['normalized']}")
    fetch_source = cls["normalized"] if cls["type"] in ("github", "git") else cls["path"]
    if cls["type"] == "local":
        # 防自己转自己：源目录与实际产物落点（默认 share/plugin-sources，可用 --output-dir 覆盖）
        # 相同或互相包含时拒绝——扫描会把产物当输入，重建 dest 还会误删。
        src_real = os.path.realpath(cls["path"])
        out_real = os.path.realpath(output_dir)
        if src_real == out_real or out_real.startswith(src_real + os.sep) \
                or src_real.startswith(out_real + os.sep):
            raise SystemExit(
                f"错误: 本地源目录与产物目录不能相同或互相包含（输入 {src_real}，"
                f"产物落点 {out_real}）。请用 --output-dir 指定与源目录互不包含的目录。")
    ref = target["ref"] if target and target["kind"] in ("tree", "blob") else None
    try:
        root, _ = acquire_repo(fetch_source, work_dir, notes, ref=ref)

        forced_manifest = None
        if target and target["kind"] == "tree" and target.get("path"):
            # tree 子路径：把该目录作为单插件目录输入，只转换这一个插件
            p = os.path.normpath(os.path.join(root, target["path"]))
            if not os.path.isdir(p):
                raise SystemExit(f"错误: tree URL 指向的目录在仓库（ref {ref}）里不存在: {target['path']}")
            notes.append(f"tree 子路径 URL：只转换 {target['path']}（ref {ref}）这一个插件")
            sources = [{"source_dir": p, "repo_root": root, "index_entry": None, "origin": a.source}]
        elif target and target["kind"] == "blob" and target.get("path"):
            # blob 文件作为严格入口（索引文件或 manifest）：索引条目的相对路径按
            # _index_base_dir 的同一口径解析（marketplace 根 / 文件所在目录，找不到时
            # 回退仓库根），不再扫描仓库其他配置
            f = os.path.normpath(os.path.join(root, target["path"]))
            if not os.path.isfile(f):
                raise SystemExit(f"错误: blob URL 指向的文件在仓库（ref {ref}）里不存在: {target['path']}")
            base_name = os.path.basename(f)
            rel_tail = target["path"].replace("\\", "/")
            if base_name in ("marketplace.json", "plugins.json"):
                kind = "plugins.json 索引" if base_name == "plugins.json" else "marketplace"
                entries = _read_index(f, kind)
                if not entries:
                    raise SystemExit(f"错误: blob 文件不是合法索引（无有效条目）: {target['path']}")
                if market is None:
                    # blob 索引入口：优先按该索引文件的顶层 name 解析市场
                    blob_market_raw = read_index_market_name(f)
                    if blob_market_raw is not None:
                        blob_market = pc.normalize_market_name(blob_market_raw)
                        try:
                            market = pc.validate_market_name(blob_market)
                            notes.append(f"市场名按索引配置解析为 {market}"
                                         f"（{target['path']} 的 name 字段）")
                        except ValueError:
                            notes.append(f"索引配置 {target['path']} 的市场名"
                                         f" {blob_market_raw!r} 归一化后不合法，回退 personal")
                notes.append(f"blob 严格入口：按索引文件 {target['path']} 展开（{len(entries)} 条目），"
                             "相对路径以 marketplace 根为基准（不存在时回退仓库根）")
                sources = _expand_indexes(root, [{"entries": entries, "index_path": f,
                                                  "base_dir": _index_base_dir(root, f), "kind": kind}],
                                          work_dir, notes, jobs=a.jobs)
            else:
                # manifest 严格入口：插件目录 = 文件按其候选相对路径回溯的插件根，方言按候选匹配
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
                    raise SystemExit(f"错误: blob 文件既不是索引文件也不是可识别的插件 manifest: {target['path']}")
                forced_manifest = forced
                notes.append(f"blob 严格入口：以 {target['path']} 为 manifest（{forced[1]} 方言），"
                             "只转换所在插件，不再扫描仓库其他配置")
                sources = [{"source_dir": os.path.normpath(source_dir), "repo_root": root,
                            "index_entry": None, "origin": a.source}]
        else:
            sources = discover_sources(root, work_dir, notes, limit=a.limit, jobs=a.jobs)
    except RuntimeError as e:
        raise SystemExit(f"错误: {e}") from e
    if a.limit > 0:
        sources = sources[:a.limit]

    # 市场归属（与输入入口无关的统一规则）：显式 --market > 源索引配置顶层
    # name > personal。git URL 与本地目录走同一解析——clone 下来的仓库同样是
    # 源；tree/blob manifest 等单插件输入源里没有索引，自然回退 personal。
    if market is None:
        market = resolve_market_from_repo(root, notes)
    if market is None:
        market = pc.DEFAULT_MARKET
    market_dir = os.path.join(output_dir, market)
    try:
        # 报告与源码按同一市场目录保护；原子创建也避免并发转换占用同一默认目录。
        os.makedirs(pc.winlong(market_dir), exist_ok=a.force)
    except FileExistsError:
        raise SystemExit(
            f"错误: 市场产物目录已存在: {market_dir}。为保护既有报告和插件源码，转换已停止。"
            "请用 --output-dir 指定新的产物根目录；只有确认可丢弃旧报告和同名源码"
            "（包括手工修改）后，才使用 --force。") from None
    if a.force:
        print(f"[警告] --force 已启用：将覆盖 {market_dir} 下的报告和同名插件源码（包括手工修改）")

    # marketplace.orig：源市场全量清单（含未转换/失败的条目；tree 子路径也按全仓
    # 识别）。收集独立于 sources 与 --limit——它是源的快照，不是转换范围。
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
                # 校验子进程的 stdout 编码随运行环境变（PYTHONIOENCODING/PYTHONUTF8/
                # Windows GBK locale），父子两侧都钉死 utf-8：env 强制子进程按 utf-8
                # 输出，父进程按 utf-8 解码；errors=replace 兜底意外字节不中断整批
                # 产物路径前缀化（pc.winlong）随 argv 传入，孙进程内部 join/walk 一致
                # 带前缀，深路径产物扫描不会撞 Windows MAX_PATH
                proc = subprocess.run([sys.executable, validator, pc.winlong(r["output_dir"])],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace",
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                errors = [ln[8:] for ln in proc.stdout.splitlines() if ln.startswith("[ERROR] ")]
                r["validate_errors"] = errors
                if proc.returncode != 0 and not errors:
                    r["validate_errors"] = ["validate_plugin.py 自身异常: " + proc.stdout.strip()[:300]]
                if not r["validate_errors"]:
                    # 依赖可用性检查（对照本项目运行时工具集；blocker 非空即「不可用」）
                    r["usability"] = cpd.check_plugin(pc.winlong(r["output_dir"]), online=not a.offline)
                    if r.get("dropped_components"):
                        # manifest 声明的组件内容在源目录缺失（镜像/源仓库不完整）：
                        # 结构再合法也是空心插件，判不可用、不随流程自动登记
                        usa = r["usability"] if isinstance(r.get("usability"), dict) else {}
                        blockers = list(usa.get("blockers") or [])
                        blockers.append(
                            "manifest 声明的组件内容在源目录缺失，转换不完整: "
                            + ", ".join(r["dropped_components"]))
                        usa["name"] = usa.get("name") or r.get("kimi_name")
                        usa["usable"] = False
                        usa["blockers"] = blockers
                        usa.setdefault("warnings", [])
                        r["usability"] = usa
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — 单插件异常记为失败条目继续，绝不中断整批
            r = {"source_dir": s.get("source_dir"), "origin": s.get("origin"),
                 "index_name": (s.get("index_entry") or {}).get("name"),
                 "success": False, "notes": [],
                 "error": f"转换过程未捕获异常: {type(e).__name__}: {e}"}
        return r

    # 逐插件转换/校验/依赖检查按 --jobs 并行；map 保序，报告顺序与串行一致
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
    print(f"转换完成: 共 {len(results)}，成功且可用 {ok}，成功但依赖不可用 {unusable}，"
          f"成功但校验有 error {invalid}，失败 {failed}")
    print(f"登记计划: 可登记 {len(plan['registerable'])}，"
          f"暂缓·权利待审 {len(plan['hold_rights_review'])}，"
          f"暂缓·hooks/setup.sh {len(plan['hold_hooks_or_setup'])}，"
          f"暂缓·依赖不可用 {len(plan['hold_deps_unavailable'])}，"
          f"暂缓·校验 error {len(plan['hold_validate_errors'])}，"
          f"失败 {len(plan['failed'])}")
    print(f"市场: {market}")
    print(f"插件目录: {market_dir}")
    print(f"报告（人读，转换明细 + 登记结果将在第②步后填入）: {md_path}")
    if plan["registerable"]:
        register_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "register_converted.py")
        print(f"下一步（批量登记 {len(plan['registerable'])} 个可登记插件，自动分流暂缓项）:")
        print(f"  python3 \"{register_script}\" \"{market_dir}\"")


if __name__ == "__main__":
    main()
