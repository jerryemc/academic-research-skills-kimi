#!/usr/bin/env python3
"""plugin_common.py — Shared helpers for Kimi plugin scaffolding.
Registration does not target ~/.kimi: the personal marketplace registry lives under
daimon-share (daimon/plugin-market/<market>/<id>.json, default market: personal),
and registration is delegated to the official client CLI
`kimi-daimon kimi-plugin register-personal` (registers only, does not install;
users install via "+" on the "Personal" tab of the Plugins page, with daemon-side
hot reload and no restart required).
Reused by create_plugin.py / register_personal.py / cachebuster.py / validate_plugin.py."""
import json, os, re, shutil, subprocess, sys, time
from pathlib import Path

# ---- Constants ----
MAX_PLUGIN_NAME_LENGTH = 64
DEFAULT_CATEGORY = "PRODUCTIVITY"
CATEGORIES = {"PRODUCTIVITY", "DEVELOPER", "FINANCE", "LIFESTYLE_HEALTH", "SYSTEM"}
SCHEMA = "https://catalog.msh.team/misc/kimi.plugin.schema.json"


def reconfigure_stdio_utf8():
    """Force stdout/stderr to UTF-8 (with error replacement) for this process.

    Prevents UnicodeEncodeError when printing non-ASCII output under a Windows
    default locale (e.g. GBK) with piped stdout (standard for agent invocation);
    callers always read UTF-8. Setting PYTHONIOENCODING works too, but scripts
    should enforce UTF-8 themselves rather than relying on the caller. Effective
    on Python 3.7+ where stream.reconfigure is available."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Personal marketplace registry (NOT ~/.kimi/plugins; do not read/write directly):
#   <share>/daimon/plugin-market/<market>/<id>.json — one entry file per plugin
# Entries are validated and written by the official CLI `kimi-daimon kimi-plugin register-personal`;
# installation state is still governed by Kimi Code runtime's installed.json (managed by the client).
MACOS_DEFAULT_SHARE_DIR = Path.home() / "Library" / "Application Support" / "kimi-desktop" / "daimon-share"


def resolve_daimon_bin(env=None):
    """Resolve kimi-daimon binary path from DAIMON_RUNTIME_BINARY_PATH
    (injected by the client when starting the daemon; cross-platform, points to
    kimi-daimon.cmd on Windows). Always present in runtime sessions, so no fallback
    is used; absence means the script is running outside a daemon session."""
    env = os.environ if env is None else env
    value = (env.get("DAIMON_RUNTIME_BINARY_PATH") or "").strip()
    if not value:
        raise RuntimeError(
            "DAIMON_RUNTIME_BINARY_PATH is not set (injected by the client when starting the daemon); "
            "run inside a Kimi session or specify --daimon-bin explicitly.")
    return value

# Personal marketplace icon convention: first matching file in plugin root (<= 256 KiB);
# falls back to the client's default icon if absent.
ICON_FILE_CANDIDATES = ("icon.png", "icon.jpg", "icon.jpeg", "icon.svg", "icon.webp")
MAX_ICON_BYTES = 256 * 1024

# ---- Naming ----
def normalize_plugin_name(name: str) -> str:
    n = re.sub(r"[^0-9a-zA-Z]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", n)

def validate_plugin_name(name: str) -> str:
    if not name:
        raise ValueError("plugin name is empty after normalization")
    if len(name) > MAX_PLUGIN_NAME_LENGTH:
        raise ValueError(f"plugin name exceeds {MAX_PLUGIN_NAME_LENGTH} characters: {name}")
    return name

# ---- Market names (shared by plugins/<market>/ directories and register --market) ----
# Matches daimon's PLUGIN_MARKET_NAME_PATTERN (lowercase directory-safe segment).
DEFAULT_MARKET = "personal"
MARKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

def normalize_market_name(name: str) -> str:
    """Normalize market name: lowercase, replace non-alphanumeric chars with '-', collapse and strip '-'."""
    n = re.sub(r"[^0-9a-z]+", "-", str(name).strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", n)

def validate_market_name(name: str) -> str:
    if not MARKET_NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid market name (must match {MARKET_NAME_RE.pattern}): {name}")
    return name

def infer_market_from_plugin_path(plugin_dir) -> str | None:
    """Infer market name from a plugin output path: extracts `<market>` from
    `.../plugin-sources/<market>/<name>/` or legacy `.../plugins/<market>/<name>/`
    (create/convert output convention), otherwise returns None.
    Default resolution order for registration: explicit --market > inferred > personal."""
    parts = Path(plugin_dir).resolve().parts
    if len(parts) >= 3 and parts[-3] in ("plugin-sources", "plugins") and MARKET_NAME_RE.match(parts[-2]):
        return parts[-2]
    return None

def resolve_register_market(plugin_dir, market=None) -> str:
    """Resolve registration market: explicit market (normalized & validated) > inferred from path > personal."""
    if market:
        return validate_market_name(normalize_market_name(str(market)))
    inferred = infer_market_from_plugin_path(plugin_dir)
    return inferred if inferred else DEFAULT_MARKET

def display_name_from_plugin_name(name: str) -> str:
    return " ".join(p.capitalize() for p in re.split(r"[-_]+", name) if p) or name

def plugin_link(plugin_name: str) -> str | None:
    """Build a plugin detail link (kimi-work://plugin?id=<id>, where id is the
    lowercased manifest name, or <id>@<market> for non-personal markets).
    Authoritative format is daimon-shared's personal-plugin-link.ts; prefer the
    `link` field from the register CLI JSON output when available.
    Returns None if id does not match the plugin id pattern (defensive check)."""
    pid = str(plugin_name).strip().lower()
    if not re.match(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*(?:@[a-z0-9][a-z0-9_-]{0,63})?$", pid):
        return None
    return f"kimi-work://plugin?id={pid}"

# ---- Share directory resolution: KIMI_SHARE_DIR > macOS client default > ~/.kimi ----
def resolve_share_dir(env=None, platform=None):
    """Resolve daimon-share directory: KIMI_SHARE_DIR takes precedence; macOS
    defaults to the client's daimon-share; other platforms fall back to ~/.kimi."""
    env = os.environ if env is None else env
    override = (env.get("KIMI_SHARE_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if (platform or sys.platform) == "darwin":
        return MACOS_DEFAULT_SHARE_DIR
    return Path.home() / ".kimi"

def resolve_plugin_sources_dir(share_dir=None):
    """Default builder source root; uses the same share location as registration, independent of cwd."""
    share = Path(share_dir).expanduser() if share_dir else resolve_share_dir()
    return share.resolve() / "plugin-sources"


# ---- Register into personal marketplace (via official client CLI; register only, no install) ----
def register_personal_plugin(plugin_dir, share_dir=None,
                             daimon_bin=None, node_bin=None, market=None):
    """Register a local plugin directory into the personal marketplace (does not install).

    Invokes `kimi-daimon kimi-plugin register-personal <dir> --share-dir <share> [--market <name>] --json`.
    The CLI handles realpath resolution, tree scanning, manifest validation, and writing
    <share>/daimon/plugin-market/<market>/<id>.json (with registeredBy "cli").
    Market resolution order: explicit market > path market segment (under plugin-sources/
    or legacy plugins/) > personal. Non-personal market plugins install with id
    <id>@<market> and generate a read-only derived marketplace.orig manifest in the market dir.
    After registration, the plugin appears on the client's "Personal" tab (uninstalled);
    clicking "+" installs it with daemon hot reload and no restart. Re-registering the same
    id overwrites entry metadata; re-registering the same version prints a "version unchanged" notice.
    Returns parsed CLI JSON output (or {"raw_output": ...} if unparseable)."""
    plugin_dir = str(Path(plugin_dir).expanduser().resolve())
    share_dir = str(Path(share_dir).expanduser()) if share_dir else str(resolve_share_dir())
    if daimon_bin is None:
        daimon_bin = resolve_daimon_bin()
    if not os.path.exists(daimon_bin):
        raise FileNotFoundError(
            f"kimi-daimon not found: {daimon_bin} "
            "(DAIMON_RUNTIME_BINARY_PATH points to an invalid path; specify --daimon-bin)")
    if not node_bin:
        # Managed Node sits alongside kimi-daimon (daemon layout: tools/node/<ver>/bin/),
        # cross-platform (node.exe on Windows); fall back to node on PATH.
        sibling = os.path.join(os.path.dirname(daimon_bin),
                               "node.exe" if sys.platform == "win32" else "node")
        if os.path.exists(sibling):
            node_bin = sibling
        else:
            node_bin = shutil.which("node")
    if not node_bin:
        raise FileNotFoundError(
            "no usable Node found (neither managed node next to kimi-daimon nor on PATH; specify --node)")
    cmd = [daimon_bin, "--node", node_bin, "kimi-plugin", "register-personal", plugin_dir,
           "--share-dir", share_dir, "--json"]
    resolved_market = resolve_register_market(plugin_dir, market)
    if resolved_market != DEFAULT_MARKET or market:
        cmd += ["--market", resolved_market]
    # On Windows, kimi-daimon is a .cmd shim and CreateProcess cannot execute batch files
    # directly (WinError 193); dispatch via cmd.exe (/d disables AutoRun, /s /c preserves quoting).
    if os.name == "nt" and daimon_bin.lower().endswith((".cmd", ".bat")):
        cmd = [os.environ.get("COMSPEC") or "cmd.exe", "/d", "/s", "/c"] + cmd
    # Capture stdout and stderr separately so stdout contains only CLI JSON for reliable
    # json.loads (merging stderr logs into stdout breaks batch JSON parsing); include both
    # streams in the exception on failure for diagnostics.
    # kimi-daimon CLI always emits UTF-8 (Node pipe stdout ignores Windows code pages);
    # decode explicitly as UTF-8 with error replacement to avoid GBK UnicodeDecodeError.
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = "\n".join(x for x in (out, err) if x)
        raise RuntimeError(f"kimi-plugin register-personal failed (exit {proc.returncode}):\n{detail}")
    try:
        result = json.loads(out) if out else {}
    except ValueError:
        result = {"raw_output": out}
    if err:
        result["cli_stderr"] = err
    return result

# ---- Registration plan for conversion reports (shared by convert_plugin_repo.py and register_converted.py) ----
def build_registration_plan(results):
    """Partition conversion-report results into mutually exclusive registration buckets,
    ordered by priority:

    failed (conversion failed) > hold_validate_errors (validation errors) >
    hold_deps_unavailable (missing/unusable dependencies) > hold_rights_review
    (rights scan hit; requires user review before registration) >
    hold_hooks_or_setup (contains hooks or setup.sh; requires user review) >
    registerable (ready to register directly).

    Buckets are mutually exclusive so counts sum cleanly. Plugins with hooks/setup.sh
    that also have unavailable deps or rights hits go into the higher-priority bucket
    and are marked with also_held_for_review. convert writes this into
    conversion-report.json's registration_plan field; register_converted.py computes it
    on the fly for older reports so both stay consistent."""
    plan = {"registerable": [], "hold_rights_review": [], "hold_hooks_or_setup": [],
            "hold_deps_unavailable": [], "hold_validate_errors": [], "failed": []}
    for r in results or []:
        name = (r.get("kimi_name") or r.get("index_name") or r.get("origin")
                or r.get("source_dir") or "<unknown>")
        if not r.get("success"):
            plan["failed"].append({"name": name, "error": r.get("error") or "unknown error"})
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
    """Read back a personal marketplace manifest entry after registration to verify
    it exists and matches id, pluginVersion, and source path.

    Read-only verification (entries are always written by the official CLI); returns
    (verified: bool, detail: dict). Version is checked against pluginVersion written
    by the daemon (the single writer in plugin-market service); if expected_version is
    provided and the entry lacks pluginVersion, verification fails. Source path field
    names vary across CLI versions, so candidates are tried in order and skipped if absent."""
    f = Path(share_dir) / "daimon" / "plugin-market" / market / f"{pid}.json"
    if not f.is_file():
        return False, {"error": f"entry file not found in personal marketplace registry: {f}"}
    try:
        entry = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return False, {"error": f"registry entry is unreadable or invalid JSON: {e}", "file": str(f)}
    if not isinstance(entry, dict):
        return False, {"error": "registry entry top-level value is not a JSON object", "file": str(f)}
    name = entry.get("name") or entry.get("id")
    if name != pid:
        return False, {"error": f"registry entry name/id mismatch: {name!r} != {pid!r}",
                       "file": str(f)}
    detail = {"file": str(f), "name": name, "checks": ["id"]}
    entry_version = entry.get("pluginVersion")
    detail["version"] = entry_version
    if expected_version:
        if not entry_version:
            return False, {**detail,
                           "error": "registry entry is missing pluginVersion; cannot verify version"}
        if entry_version != expected_version:
            return False, {**detail,
                           "error": f"version mismatch: registry {entry_version!r} != registered {expected_version!r}"}
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
                                   "error": f"source path mismatch: registry {entry[key]!r} != registered {expected_dir!s}"}
                detail["checks"].append("sourcePath")
            break
    return True, detail

# ---- Icon: download --icon-url to icon.<ext> in plugin root ----
def fetch_icon_file(icon_url, plugin_root, max_bytes=MAX_ICON_BYTES):
    """Download iconUrl to icon.<ext> in the plugin root (personal marketplace convention).

    Requires an image/* content-type (rejected if missing or non-image; URL extension
    only picks the file suffix and never bypasses validation). Allowed extensions:
    png/jpg/jpeg/svg/webp; size must not exceed max_bytes. Existing candidate icon files
    are removed on entry so a failed download/check does not silently leave a stale icon.
    Returns the written Path on success, or prints a warning and returns None on failure
    (non-blocking; iconUrl stays in the manifest and is still used by the client UI)."""
    import urllib.request
    ext_map = {
        "image/png": ".png", "image/jpeg": ".jpg",
        "image/svg+xml": ".svg", "image/webp": ".webp",
    }
    url_ext = os.path.splitext(str(icon_url).split("?")[0])[1].lower()
    # Calling this function signals an intent to replace the icon: remove stale candidate
    # icon files first so a failed fetch doesn't silently fall back to an old icon.
    cleaned_stale = False
    for candidate in ICON_FILE_CANDIDATES:
        stale = Path(plugin_root) / candidate
        if stale.is_file():
            stale.unlink()
            cleaned_stale = True
    stale_note = " (stale icon candidates removed)" if cleaned_stale else ""
    try:
        with urllib.request.urlopen(str(icon_url), timeout=15) as resp:
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            data = resp.read(max_bytes + 1)
    except Exception as e:  # noqa: BLE001 — downgrade any download failure to a warning
        print(f"Warning: icon download failed (keeping iconUrl, no icon file created){stale_note}: {e}")
        return None
    if not content_type.startswith("image/"):
        print(f"Warning: icon content-type is not an image ({content_type or 'unknown'}); no icon file created.{stale_note}")
        return None
    ext = ext_map.get(content_type) or (
        url_ext if url_ext in (".png", ".jpg", ".jpeg", ".svg", ".webp") else "")
    if not ext:
        print(f"Warning: unable to determine icon extension (content-type {content_type}); no icon file created.")
        return None
    if len(data) > max_bytes:
        print(f"Warning: icon exceeds {max_bytes // 1024} KiB; no icon file created (personal marketplace omits oversized icons).")
        return None
    if len(data) == 0:
        print("Warning: icon response body is empty; no icon file created.")
        return None
    dest = Path(plugin_root) / f"icon{ext}"
    dest.write_bytes(data)
    print(f"icon -> {dest.name} ({len(data)} bytes)")
    return dest

# ---- Mandatory pre-registration validation ----
def run_validator(plugin_dir, validator=None):
    """Run validate_plugin.py before registration: returns True on 0 errors, False if
    validation fails or the validator crashes. Non-blocking (returns True) if missing."""
    validator = validator or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "validate_plugin.py")
    if not os.path.exists(validator):
        return True
    return subprocess.call([sys.executable, validator, str(plugin_dir)]) == 0

# ---- File I/O ----
def winlong(path):
    """Prefix absolute paths with \\\\?\\ on Windows to bypass MAX_PATH=260: when
    LongPathsEnabled=0 (default), Python file APIs raise WinError 3/206 on deep paths,
    which the prefix bypasses; pure path ops (join/relpath/basename) are unaffected.
    Returned unchanged on macOS/Linux."""
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
        raise FileExistsError(f"{path} already exists (use force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path

def create_stub_file(path, content, force=False):
    """Silently skip without error if the file exists and force is False."""
    path = Path(path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True

def bump_patch_version(version):
    """Increment patch version: strip build metadata and prerelease (+local.x / -beta.1, etc.),
    converting X.Y.Z -> X.Y.(Z+1). Falls back to 0.1.0 if unparseable.
    Used for version management on `create_plugin.py --force` reruns."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
    if not m:
        return "0.1.0"
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"


def bump_local_version(version):
    """Dev iteration version: strip any existing +local.* and append +local.<timestamp>.
    The personal marketplace detects updates via version string inequality without
    polluting release semver. Used by cachebuster.py and update_plugin.py."""
    base = re.sub(r"\+local\..*$", "", str(version or "0.1.0"))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{base}+local.{stamp}"


# ---- Read mcp.json ----
def read_mcp_servers(path):
    """Read mcpServers from mcp.json (returns {} on corrupt file or missing field). For small files."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    if not isinstance(d, dict):
        return {}
    servers = d.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


# ---- Manifest (kimi.plugin.json) ----
def build_plugin_json(name, display_name, a):
    m = {
        "$schema": SCHEMA, "name": name, "version": "0.1.0",
        "description": a.description or f"{display_name} plugin",
        "keywords": [k.strip() for k in (a.keywords or "").split(",") if k.strip()],
        "author": a.author or "Local developer", "license": "MIT",
        "skillInstructions": a.skill_instructions or "",
        "interface": {
            "displayName": display_name,
            "shortDescription": a.short or f"Use {display_name} in Kimi",
            "longDescription": a.long or a.description or f"Local Kimi plugin for {display_name}.",
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
            m["mcpServers"] = {name: {"url": a.mcp_url}}  # Omit enabledTools: [] is an empty allowlist (registers no tools); omitting enables all
        else:
            m["mcpServers"] = {name: {"command": "TODO_CMD", "args": []}}
            m["interface"]["hostKind"] = "local"
            m["interface"]["platforms"] = ["macos", "linux", "windows"]
    # Additional Kimi Code v1 manifest components (raw JSON format follows manifest.ts parser):
    # agents/commands are "./"-prefixed directory paths (string or string array);
    # sessionStart references a bundled skill; systemPromptPath is a "./"-prefixed file path;
    # hooks is an array of {event, command, timeout?} objects (HookDefSchema, strict).
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
