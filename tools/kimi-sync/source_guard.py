#!/usr/bin/env python3
"""source_guard.py — 外部插件源目录的打包前防护（convert_plugin_repo.py 在 copytree 之前调用）。

防护口径（移植自 agent-plugin-manager 分发版 kimi_install.py 的同名逻辑）：
- 疑似凭证/密钥文件跳过（匹配前统一 lower()，防大小写绕过），跳过清单写进转换报告；
- 指向源目录外的链接直接拒绝打包（symlink 与 Windows junction 同口径——junction
  无需提权即可创建，os.path.islink 看不到它，walk/copytree 却会当普通目录递归并
  物化目标内容）；树内链接指向敏感目标或被排除目录（如 .ssh）的，链接本身也跳过
  （防凭证经物化旁路带入产物）；
- 单文件 / 源目录总体积上限，超限拒绝；
- 凭证/IDE 目录（.ssh/.aws/.gnupg/.idea/.vscode）整目录排除，不进产物。

copytree 保持默认 symlinks=False（树内链接物化为内容副本，产物跨平台可移植）；
越界链接已被 check_source_tree 拒绝，物化只会复制树内内容。skip 集合与 copytree
收到的 dir 保持同一路径形式（调用方传什么路径就按什么路径 walk），保证成员判断命中。
"""
import fnmatch
import os

# 整目录排除：版本控制/依赖/IDE/凭证目录（convert 自身的 COPY_EXCLUDE_DIRS 是其子集）
EXCLUDE_DIRS = {".git", ".github", "node_modules", "__pycache__", ".idea", ".vscode",
                ".ssh", ".aws", ".gnupg"}
# 报告里需要点名的新排除项（其余 .git/node_modules 等 convert 本来就一直排除）
CREDENTIAL_DIRS = {".ssh", ".aws", ".gnupg"}

# 疑似凭证/密钥文件：打包时跳过并记入报告，绝不随迁（匹配前统一 lower()）
SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_dsa*", "id_ecdsa*", "*.ppk", "*.jks",
    ".npmrc", ".netrc", "credentials", "credentials.*", ".git-credentials",
    ".pgpass", ".my.cnf", ".envrc", ".bash_history", ".zsh_history",
)
MAX_TOTAL_BYTES = 500 * 1024 * 1024   # 源目录总量上限
MAX_FILE_BYTES = 50 * 1024 * 1024     # 单文件体积上限


class SourceGuardError(Exception):
    """源目录安检未通过（越界 symlink / 体积超限），该插件不得打包。"""


def is_sensitive_file(name) -> bool:
    low = str(name).lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SENSITIVE_FILE_PATTERNS)


def is_link(p) -> bool:
    """symlink 或 Windows junction 都视为链接。

    os.path.islink 只认 symlink，对 junction（mklink /J，无需提权即可创建）
    返回 False；junction 在 walk/copytree 里会被当普通目录递归并物化目标内容，
    必须与 symlink 同口径检查。Python 3.12+ 用 os.path.isjunction；更早版本的
    Windows 用 reparse point 文件属性兜底（FILE_ATTRIBUTE_REPARSE_POINT=0x400，
    symlink 本身也是 reparse point，与 islink 互补）。"""
    if os.path.islink(p):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        try:
            return bool(isjunction(p))
        except OSError:
            return False
    if os.name == "nt":
        try:
            return bool(getattr(os.lstat(p), "st_file_attributes", 0) & 0x400)
        except OSError:
            return False
    return False


def _target_in_excluded_dir(target_real, src_real) -> bool:
    """链接目标（调用前已确认未越界）是否落在被整目录排除的目录里（含目标本身）。

    命中即不得物化：否则 .ssh/.aws 等凭证目录可经树内链接旁路进产物。"""
    rel = os.path.relpath(target_real, src_real)
    return any(part.lower() in EXCLUDE_DIRS for part in rel.split(os.sep))


def check_source_tree(src):
    """复制前安检。返回 {"skip": set(绝对路径), "skipped_sensitive": [相对路径],
    "excluded_credential_dirs": [相对路径]}；越界 symlink 与体积超限抛 SourceGuardError。

    skip 集合按 src 传入时的路径形式生成，与 copytree ignore 回调收到的 dir 一致；
    越界判断单独用 realpath（macOS 上 /var 是 /private/var 的 symlink）。
    """
    src = os.path.normpath(str(src))
    src_real = os.path.realpath(src)
    total = 0
    skipped = []
    cred_dirs = []
    skip = set()
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        kept = []
        for d in dirnames:
            if d.lower() in EXCLUDE_DIRS:
                if d.lower() in CREDENTIAL_DIRS:
                    cred_dirs.append(os.path.relpath(os.path.join(dirpath, d), src))
            else:
                kept.append(d)
        dirnames[:] = kept
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if is_link(p):
                target = os.path.realpath(p)
                if target != src_real and not target.startswith(src_real + os.sep):
                    raise SourceGuardError(
                        f"源目录含指向源目录外的链接（symlink/junction），拒绝打包: {p} -> {target}")
                # 树内链接指向敏感目标文件或被排除目录（如 .ssh）：链接本身也不打包，
                # 防凭证经物化旁路带入产物
                if is_sensitive_file(os.path.basename(target)) or _target_in_excluded_dir(target, src_real):
                    skipped.append(os.path.relpath(p, src))
                    skip.add(p)
        # junction 不是 islink，os.walk 会当普通目录下钻（循环链接会无限递归、
        # 体积重复统计）；越界已被上面拒绝，树内 junction 与 symlink 同口径交由
        # copytree 物化，guard 自身不再下钻
        dirnames[:] = [d for d in dirnames if not is_link(os.path.join(dirpath, d))]
        for name in filenames:
            p = os.path.join(dirpath, name)
            if is_link(p):
                continue
            if is_sensitive_file(name):
                skipped.append(os.path.relpath(p, src))
                skip.add(p)
                continue
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                raise SourceGuardError(
                    f"单文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB，拒绝打包: {p}")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise SourceGuardError(
                    f"源目录总体积超过 {MAX_TOTAL_BYTES // 1024 // 1024}MB，拒绝打包: {src}")
    return {"skip": skip, "skipped_sensitive": skipped,
            "excluded_credential_dirs": cred_dirs}


def make_copy_ignorer(exclude_names=(), guard=None):
    """生成 copytree 的 ignore 回调：大小写不敏感的精确名排除（convert 的方言排除
    + 本模块的凭证/IDE 目录）+ 疑似凭证文件 + 预扫描 skip 集合 + *.pyc。"""
    names_lc = {str(n).lower() for n in exclude_names} | {d.lower() for d in EXCLUDE_DIRS}
    skip = (guard or {}).get("skip") or set()

    def ignore(dir_path, names):
        out = []
        for n in names:
            low = n.lower()
            if low in names_lc or low.endswith(".pyc") or is_sensitive_file(n):
                out.append(n)
                continue
            if os.path.join(dir_path, n) in skip:
                out.append(n)
        return out

    return ignore
