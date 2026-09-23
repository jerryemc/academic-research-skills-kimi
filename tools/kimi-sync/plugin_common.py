#!/usr/bin/env python3
"""plugin_common.py — Kimi 插件脚手架的公共 helper。
登记目标不是 ~/.kimi：个人市场注册表在 daimon-share 下
（daimon/plugin-market/<market>/<id>.json，缺省市场 personal），登记动作统一委托给
客户端官方 CLI kimi-daimon kimi-plugin register-personal（只登记不安装；安装由用户在
插件页「个人」页签点 ＋ 完成，daemon 侧热更，无需重启）。
被 create_plugin.py / register_personal.py / cachebuster.py / validate_plugin.py 复用。"""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

# ---- 常量 ----
MAX_PLUGIN_NAME_LENGTH = 64
DEFAULT_CATEGORY = "PRODUCTIVITY"
CATEGORIES = {"PRODUCTIVITY", "DEVELOPER", "FINANCE", "LIFESTYLE_HEALTH", "SYSTEM"}
SCHEMA = "https://catalog.msh.team/misc/kimi.plugin.schema.json"


def reconfigure_stdio_utf8():
    """把本进程 stdout/stderr 钉成 UTF-8（容错）。

    脚手架输出含中文，Windows 默认 locale（如 GBK）且 stdout 被管道捕获
    （Agent 调用的标准形态）时 print 直接 UnicodeEncodeError；调用方统一按
    UTF-8 读。PYTHONIOENCODING 能起同样作用但不能要求调用方先设置，脚本
    入口自己钉死才可靠。仅在支持 reconfigure 的 Python（3.7+）上生效。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# 个人市场注册表（不是 ~/.kimi/plugins，也不要直接读写）：
#   <share>/daimon/plugin-market/<market>/<id>.json — 每个插件一个条目文件
# 条目由客户端官方 CLI `kimi-daimon kimi-plugin register-personal` 校验后写入；
# 安装事实仍以 Kimi Code 运行时的 installed.json 为准（由客户端安装动作维护）。
MACOS_DEFAULT_SHARE_DIR = Path.home() / "Library" / "Application Support" / "kimi-desktop" / "daimon-share"


def resolve_daimon_bin(env=None):
    """kimi-daimon 二进制路径 = DAIMON_RUNTIME_BINARY_PATH 环境变量
    （客户端启动 daemon 时注入的运行中真实路径，跨平台，Windows 指向
    kimi-daimon.cmd）。该变量在 runtime 环境中必然存在，不设回退；
    缺失说明脚本运行在 daemon 会话之外，直接报错。"""
    env = os.environ if env is None else env
    value = (env.get("DAIMON_RUNTIME_BINARY_PATH") or "").strip()
    if not value:
        raise RuntimeError(
            "DAIMON_RUNTIME_BINARY_PATH 未设置（应由客户端启动 daemon 时注入）；"
            "请在 Kimi 会话环境内运行，或用 --daimon-bin 显式指定。")
    return value

# 个人市场图标约定：插件根目录下首个匹配文件（<= 256 KiB），缺省时客户端用默认图标兜底。
ICON_FILE_CANDIDATES = ("icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp")
MAX_ICON_BYTES = 256 * 1024

# ---- 名字 ----
def normalize_plugin_name(name: str) -> str:
    n = re.sub(r"[^0-9a-zA-Z]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", n)

def validate_plugin_name(name: str) -> str:
    if not name:
        raise ValueError("plugin name 归一化后为空")
    if len(name) > MAX_PLUGIN_NAME_LENGTH:
        raise ValueError(f"plugin name 超过 {MAX_PLUGIN_NAME_LENGTH} 字符: {name}")
    return name

# ---- 市场名（plugins/<market>/ 目录与登记 --market 共用同一套规则） ----
# 与 daimon 侧 PLUGIN_MARKET_NAME_PATTERN 保持一致（小写目录安全段）。
DEFAULT_MARKET = "personal"
MARKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

def normalize_market_name(name: str) -> str:
    """市场名归一化：小写、非小写字母数字转连字符、去重并去首尾连字符。"""
    n = re.sub(r"[^0-9a-z]+", "-", str(name).strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", n)

def validate_market_name(name: str) -> str:
    if not MARKET_NAME_RE.match(name or ""):
        raise ValueError(
            f"market 名不合法（需匹配 {MARKET_NAME_RE.pattern}）: {name}")
    return name

def infer_market_from_plugin_path(plugin_dir) -> str | None:
    """从插件源目录的产物路径推断市场名：`.../plugin-sources/<market>/<name>/`
    或旧的 `.../plugins/<market>/<name>/` 形态时
    取 `<market>`（create/convert 的产物约定），否则返回 None。
    用于登记时市场名的缺省来源：显式 --market > 本推断 > personal。"""
    parts = Path(plugin_dir).resolve().parts
    if len(parts) >= 3 and parts[-3] in ("plugin-sources", "plugins") and MARKET_NAME_RE.match(parts[-2]):
        return parts[-2]
    return None

def resolve_register_market(plugin_dir, market=None) -> str:
    """登记市场的完整解析：显式 market 参数（归一化+校验）> 产物路径推断 > personal。"""
    if market:
        return validate_market_name(normalize_market_name(str(market)))
    inferred = infer_market_from_plugin_path(plugin_dir)
    return inferred if inferred else DEFAULT_MARKET

def display_name_from_plugin_name(name: str) -> str:
    return " ".join(p.capitalize() for p in re.split(r"[-_]+", name) if p) or name

def plugin_link(plugin_name: str) -> str | None:
    """插件详情链接（kimi-work://plugin?id=<id>，id = manifest name 小写化，
    非 personal 市场为 <id>@<market>）。
    格式权威是 daimon-shared 的 personal-plugin-link.ts，此处保持一致；
    登记 CLI 的 JSON 输出也带同格式 link 字段，优先从那里取。
    id 不满足插件 id 规范时返回 None（已登记插件不会出现，属防御）。"""
    pid = str(plugin_name).strip().lower()
    if not re.match(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*(?:@[a-z0-9][a-z0-9_-]{0,63})?$", pid):
        return None
    return f"kimi-work://plugin?id={pid}"

# ---- share 目录解析：KIMI_SHARE_DIR > macOS 客户端默认 > ~/.kimi ----
def resolve_share_dir(env=None, platform=None):
    """解析 daimon-share 目录：环境变量 KIMI_SHARE_DIR 优先；
    macOS 默认用客户端 daimon-share；其余平台回退 daemon 默认 ~/.kimi。"""
    env = os.environ if env is None else env
    override = (env.get("KIMI_SHARE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if (platform or sys.platform) == "darwin":
        return MACOS_DEFAULT_SHARE_DIR
    return Path.home() / ".kimi"

def resolve_plugin_sources_dir(share_dir=None):
    """Builder 默认源码根；显式 share 与登记使用同一位置，不依赖会话 cwd。"""
    share = Path(share_dir).expanduser() if share_dir else resolve_share_dir()
    return share.resolve() / "plugin-sources"


# ---- 登记进个人市场（走客户端官方 CLI，只登记不安装） ----
def register_personal_plugin(plugin_dir, share_dir=None,
                             daimon_bin=None, node_bin=None, market=None):
    """把本地插件目录登记进个人市场（不安装）。

    调用官方 `kimi-daimon kimi-plugin register-personal <dir> --share-dir <share> [--market <name>] --json`，
    由 CLI 完成 realpath/树扫描/manifest 校验并写入
    <share>/daimon/plugin-market/<market>/<id>.json（registeredBy "cli"）。
    market 解析顺序：显式 market 参数 > 产物路径的市场段（plugin-sources/ 或旧 plugins/ 根）
    > personal。非 personal 市场的插件安装后以
    <id>@<market> 为插件 id，并在市场目录生成 marketplace.orig 只读派生清单。
    登记后插件出现在客户端插件页「个人」页签（未安装），用户点 ＋ 安装，
    daemon 侧热更活跃会话，无需重启。同 id 重复登记覆盖条目元数据；
    同版本重复登记会打印「版本号未变化」提示。
    返回 CLI 的 JSON 输出（无法解析时返回 {"raw_output": ...}）。"""
    plugin_dir = str(Path(plugin_dir).expanduser().resolve())
    share_dir = str(Path(share_dir).expanduser()) if share_dir else str(resolve_share_dir())
    if daimon_bin is None:
        daimon_bin = resolve_daimon_bin()
    if not os.path.exists(daimon_bin):
        raise FileNotFoundError(f"kimi-daimon 不存在: {daimon_bin}（DAIMON_RUNTIME_BINARY_PATH 指向的路径无效，或用 --daimon-bin 指定）")
    if not node_bin:
        # 托管 Node 与 kimi-daimon 同目录（daemon 管理布局 tools/node/<ver>/bin/），
        # 跨平台（Windows 为 node.exe）；其次 PATH 里的 node。
        sibling = os.path.join(os.path.dirname(daimon_bin),
                               "node.exe" if sys.platform == "win32" else "node")
        if os.path.exists(sibling):
            node_bin = sibling
        else:
            node_bin = shutil.which("node")
    if not node_bin:
        raise FileNotFoundError("找不到可用 Node（kimi-daimon 同目录无托管 node，PATH 里也没有；可用 --node 指定）")
    cmd = [daimon_bin, "--node", node_bin, "kimi-plugin", "register-personal", plugin_dir,
           "--share-dir", share_dir, "--json"]
    resolved_market = resolve_register_market(plugin_dir, market)
    if resolved_market != DEFAULT_MARKET or market:
        cmd += ["--market", resolved_market]
    # Windows 的 kimi-daimon 是 .cmd shim，CreateProcess 不能直接执行批处理
    # （WinError 193），经 cmd.exe 转发：/d 关 AutoRun，/s /c 保留参数引号语义
    if os.name == "nt" and daimon_bin.lower().endswith((".cmd", ".bat")):
        cmd = [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c"] + cmd
    # stdout 与 stderr 分开捕获：stdout 只放 CLI 的 JSON，保证 json.loads 稳定可解析
    # （合并时 CLI 的 stderr 日志可能插进 JSON 中间，批量场景解析必崩）；
    # 失败时把两路输出都带进异常，不丢诊断信息。
    # kimi-daimon CLI 输出总是 UTF-8（node 写 pipe stdout 不受 Windows 代码页影响），
    # 父进程显式按 utf-8 解码并容错，避免 GBK locale 下 UnicodeDecodeError 掩盖真实结果
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = "\n".join(x for x in (out, err) if x)
        raise RuntimeError(f"kimi-plugin register-personal 失败（exit {proc.returncode}）:\n{detail}")
    try:
        result = json.loads(out) if out else {}
    except ValueError:
        result = {"raw_output": out}
    if err:
        result["cli_stderr"] = err
    return result

# ---- 转换报告的登记计划（convert_plugin_repo.py 与 register_converted.py 共用同一口径） ----
def build_registration_plan(results):
    """把 conversion-report 的 results 分成互斥的登记计划桶，按优先级：

    failed（转换失败）> hold_validate_errors（校验 error）> hold_deps_unavailable
    （依赖不可用）> hold_rights_review（权利扫描命中，需用户审阅后再登记）
    > hold_hooks_or_setup（含 hooks 或 setup.sh，需用户审阅后再登记）
    > registerable（可直接登记）。

    桶互斥，同一插件只进一个桶，各桶计数可直接相加对账。同时含 hooks/setup.sh
    与依赖不可用/权利命中的插件归前者（依赖不可用登记无意义；权利命中需先审权利），
    并在条目上标 also_held_for_review。convert 把它写进
    conversion-report.json 的 registration_plan 字段；register_converted.py
    读不到该字段（旧报告）时用本函数现算，两处判定不会分叉。"""
    plan = {"registerable": [], "hold_rights_review": [], "hold_hooks_or_setup": [],
            "hold_deps_unavailable": [], "hold_validate_errors": [], "failed": []}
    for r in results or []:
        name = (r.get("kimi_name") or r.get("index_name") or r.get("origin")
                or r.get("source_dir") or "<unknown>")
        if not r.get("success"):
            plan["failed"].append({"name": name, "error": r.get("error") or "未知错误"})
            continue
        if r.get("validate_errors"):
            plan["hold_validate_errors"].append(
                {"name": name, "errors": list(r.get("validate_errors"))})
            continue
        if not (r.get("usability") or {}).get("usable", True):
            entry = {"name": name,
                     "blockers": list((r.get("usability") or {}).get("blockers") or [])}
            if r.get("hooks") or r.get("setup_sh"):
                entry["also_held_for_review"] = True
            plan["hold_deps_unavailable"].append(entry)
            continue
        rights = r.get("rights") or {}
        if rights.get("severity"):
            entry = {"name": name, "severity": rights["severity"],
                     "matches": list(rights.get("restricted") or []),
                     "signals": list(rights.get("vendor") or [])}
            if r.get("hooks") or r.get("setup_sh"):
                entry["also_held_for_review"] = True
            plan["hold_rights_review"].append(entry)
            continue
        hooks = r.get("hooks") or []
        if hooks or r.get("setup_sh"):
            plan["hold_hooks_or_setup"].append(
                {"name": name, "hooks": len(hooks), "setup_sh": bool(r.get("setup_sh"))})
            continue
        plan["registerable"].append(name)
    return plan


def readback_market_entry(pid, share_dir, market=DEFAULT_MARKET,
                          expected_version="", expected_dir=None):
    """登记后回读个人市场清单，验证条目真实存在且 id/pluginVersion/source path 匹配。

    纯校验、只读注册表文件（条目始终由官方 CLI 写入，本函数不写），
    返回 (verified: bool, detail: dict)。版本字段以 daemon 真实写入的
    pluginVersion 为准（plugin-market service 的唯一写入点）；传了
    expected_version 而条目缺该字段时判定验证失败——读不到版本不能当成
    版本匹配。source path 字段名各版本 CLI 不一，逐个候选尝试；条目里
    根本没有该字段时跳过这一项。"""
    f = Path(share_dir) / "daimon" / "plugin-market" / market / f"{pid}.json"
    if not f.is_file():
        return False, {"error": f"个人市场清单中不存在条目文件: {f}"}
    try:
        entry = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return False, {"error": f"清单条目不可读或不是合法 JSON: {e}", "file": str(f)}
    if not isinstance(entry, dict):
        return False, {"error": "清单条目顶层不是 JSON 对象", "file": str(f)}
    name = entry.get("name") or entry.get("id")
    if name != pid:
        return False, {"error": f"清单条目 name/id 不匹配: {name!r} != {pid!r}",
                       "file": str(f)}
    detail = {"file": str(f), "name": name, "checks": ["id"]}
    entry_version = entry.get("pluginVersion")
    detail["version"] = entry_version
    if expected_version:
        if not entry_version:
            return False, {**detail,
                           "error": "清单条目缺少 pluginVersion 字段，无法核对版本"}
        if entry_version != expected_version:
            return False, {**detail,
                           "error": f"版本不匹配: 清单 {entry_version!r} != 本次登记 {expected_version!r}"}
        detail["checks"].append("version")
    for key in ("source", "sourcePath", "source_path", "path", "dir", "pluginDir"):
        if isinstance(entry.get(key), str) and entry[key]:
            detail["sourcePath"] = entry[key]
            if expected_dir is not None:
                try:
                    same = (Path(entry[key]).expanduser().resolve()
                            == Path(expected_dir).resolve())
                except OSError:
                    same = entry[key] == str(expected_dir)
                if not same:
                    return False, {**detail,
                                   "error": f"源路径不匹配: 清单 {entry[key]!r} != 本次登记 {expected_dir!s}"}
                detail["checks"].append("sourcePath")
            break
    return True, detail

# ---- 图标：--icon-url 下载为插件根目录的 icon.<ext> ----
def fetch_icon_file(icon_url, plugin_root, max_bytes=MAX_ICON_BYTES):
    """把 iconUrl 下载成插件根目录的 icon.<ext>（个人市场图标约定）。

    content-type 必须是 image/*（缺失或错误一律不生成；扩展名只决定落盘
    后缀，不能充当放行依据）；扩展名限 png/jpg/jpeg/svg/webp；大小不超过
    max_bytes（个人市场单图标上限）。调用即代表「要换图标」，函数入口先
    清理目录里的候选 icon 文件，避免下载/校验失败时旧图标被静默沿用。成功返回写入的 Path；
    失败打印警告并返回 None（不阻断脚手架；manifest 里的 iconUrl 仍保留，
    客户端插件页对本地插件仍使用它）。"""
    import urllib.request
    ext_map = {
        "image/png": ".png", "image/jpeg": ".jpg",
        "image/svg+xml": ".svg", "image/webp": ".webp",
    }
    url_ext = os.path.splitext(str(icon_url).split("?")[0])[1].lower()
    # 调用本函数即意味着「要换图标」：先清掉目录里的候选 icon 文件，
    # 否则下载/校验失败时旧图标会被个人市场按「首个匹配」语义静默沿用。
    cleaned_stale = False
    for candidate in ICON_FILE_CANDIDATES:
        stale = Path(plugin_root) / candidate
        if stale.is_file():
            stale.unlink()
            cleaned_stale = True
    stale_note = "（旧 icon 候选文件已清理）" if cleaned_stale else ""
    try:
        with urllib.request.urlopen(str(icon_url), timeout=15) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(max_bytes + 1)
    except Exception as e:  # noqa: BLE001 — 任何下载失败都降级为警告
        print(f"警告：图标下载失败（保留 iconUrl，未生成 icon 文件）{stale_note}: {e}")
        return None
    if not content_type.startswith("image/"):
        print(f"警告：图标 content-type 非图片（{content_type or '未知'}），未生成 icon 文件。{stale_note}")
        return None
    ext = ext_map.get(content_type) or (
        url_ext if url_ext in (".png", ".jpg", ".jpeg", ".svg", ".webp") else "")
    if not ext:
        print(f"警告：无法确定图标扩展名（content-type {content_type}），未生成 icon 文件。")
        return None
    if len(data) > max_bytes:
        print(f"警告：图标超过 {max_bytes // 1024} KiB，未生成 icon 文件（个人市场会省略超限图标）。")
        return None
    if len(data) == 0:
        print("警告：图标响应体为空，未生成 icon 文件。")
        return None
    dest = Path(plugin_root) / f"icon{ext}"
    dest.write_bytes(data)
    print(f"icon -> {dest.name}（{len(data)} bytes）")
    return dest

# ---- 登记前强制校验 ----
def run_validator(plugin_dir, validator=None):
    """登记前跑 validate_plugin.py：0 error 返回 True；校验失败或校验器自身崩溃返回 False。
    校验器不存在时不阻断（返回 True）。"""
    validator = validator or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "validate_plugin.py")
    if not os.path.exists(validator):
        return True
    return subprocess.call([sys.executable, validator, str(plugin_dir)]) == 0

# ---- 文件 IO ----
def winlong(path):
    """Windows 下给绝对路径加 \\\\?\\ 前缀，绕过 MAX_PATH=260：系统 LongPathsEnabled=0
    （默认）时 Python 文件 API 对深路径直接 WinError 3/206，加前缀不经该检查；
    os.path.join/relpath/basename 等纯路径运算不受前缀影响。macOS/Linux 原样返回。"""
    if os.name != "nt":
        return path
    path = os.path.abspath(path)
    return path if path.startswith("\\\\?\\") else "\\\\?\\" + path

def load_json(path):
    with open(winlong(path), encoding="utf-8") as f:
        return json.load(f)

def write_json(path, data, force=False):
    path = Path(winlong(path))
    if path.exists() and not force:
        raise FileExistsError(f"{path} 已存在（用 force 覆盖）")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path

def create_stub_file(path, content, force=False):
    """存在且未 force 时静默跳过（不报错）。"""
    path = Path(path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True

def bump_patch_version(version):
    """patch 位递增：剥掉 build metadata 与 prerelease（+local.x / -beta.1 等），
    X.Y.Z -> X.Y.(Z+1)。无法解析为 X.Y.Z 时回退 0.1.0。
    用于 create_plugin.py --force 重跑时的版本管理。"""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
    if not m:
        return "0.1.0"
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"


def bump_local_version(version):
    """开发迭代版本号：剥掉已有 +local.* 后在 base 版本上追加 +local.<时间戳>。
    个人市场按版本号字符串不等检出更新，不污染正式 semver。
    用于 cachebuster.py 与 update_plugin.py。"""
    base = re.sub(r"\+local\..*$", "", str(version or "0.1.0"))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{base}+local.{stamp}"


# ---- mcp.json 读取 ----
def read_mcp_servers(path):
    """读 mcp.json 的 mcpServers（坏文件/缺字段按空处理）。只读小文件。"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(d, dict):
        return {}
    servers = d.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


# ---- 清单（kimi.plugin.json） ----
def build_plugin_json(name, display_name, a):
    m = {
        "$schema": SCHEMA, "name": name, "version": "0.1.0",
        "description": a.description or f"{display_name} 插件",
        "keywords": [k.strip() for k in (a.keywords or "").split(",") if k.strip()],
        "author": a.author or "Local developer", "license": "MIT",
        "skillInstructions": a.skill_instructions or "",
        "interface": {
            "displayName": display_name,
            "shortDescription": a.short or f"在 Kimi 里使用 {display_name}",
            "longDescription": a.long or a.description or f"{display_name} 的 Kimi 本地插件。",
            "developerName": a.author or "Local developer",
            "websiteURL": a.homepage or "",
            "iconUrl": a.icon_url or "",
            "category": a.category or DEFAULT_CATEGORY,
        },
    }
    if a.type in ("skill-only", "mcp+skills"):
        m["skills"] = "./skills/"
    if a.type in ("mcp", "mcp+skills"):
        if getattr(a, "mcp_url", None):
            m["mcpServers"] = {name: {"url": a.mcp_url}}  # 不写 enabledTools：空数组是空白名单（一个工具都不注册），缺省才是全量
        else:
            m["mcpServers"] = {name: {"command": "TODO_CMD", "args": []}}
            m["interface"]["hostKind"] = "local"
            m["interface"]["platforms"] = ["macos", "linux", "windows"]
    # Kimi Code v1 manifest 的其余组件（原始 JSON 格式以 manifest.ts 解析器为准）：
    # agents/commands 是 "./" 前缀的目录路径（字符串或字符串数组）；
    # sessionStart 引用插件自带 skill；systemPromptPath 是 "./" 前缀的文件路径；
    # hooks 是 {event, command, timeout?} 对象数组（HookDefSchema，strict）。
    if getattr(a, "with_agents", False):
        m["agents"] = ["./agents/"]
    if getattr(a, "with_commands", False):
        m["commands"] = "./commands/"
    if getattr(a, "with_session_start", False):
        m["sessionStart"] = {"skill": name}
    if getattr(a, "with_system_prompt", False):
        m["systemPromptPath"] = "./system-prompt.md"
    if getattr(a, "with_hooks", False):
        m["hooks"] = [{"event": "SessionStart", "command": "bash ./hooks/session-start.sh"}]
    return m
