#!/usr/bin/env python3
"""source_guard.py — Pre-packaging safety guard for external plugin source directories
(called by convert_plugin_repo.py before copytree).

Guard rules (ported from agent-plugin-manager's kimi_install.py):
- Skip suspected credential/key files (lowercased before matching to prevent case bypasses),
  recording skipped items in the conversion report;
- Reject links pointing outside the source directory (symlinks and Windows junctions alike —
  junctions can be created without elevation and are missed by os.path.islink, yet walk/copytree
  traverses and materializes them as regular directories); in-tree links targeting sensitive
  files or excluded dirs (e.g. .ssh) are also skipped to prevent materialization bypasses;
- Enforce per-file and total source-tree size limits, rejecting oversized trees;
- Exclude credential/IDE directories (.ssh/.aws/.gnupg/.idea/.vscode) entirely from output.

copytree keeps default symlinks=False (in-tree links materialize as copies for cross-platform
portability); out-of-tree links are rejected by check_source_tree beforehand. The skip set
uses the same path form as the caller's src so copytree's ignore callback matches reliably.
"""
import fnmatch
import os

# Whole-directory exclusions: VCS, dependencies, IDE, and credential dirs (superset of convert's COPY_EXCLUDE_DIRS)
EXCLUDE_DIRS = {".git", ".github", "node_modules", "__pycache__", ".idea", ".vscode",
                ".ssh", ".aws", ".gnupg"}
# Newly excluded credential dirs called out in reports (.git/node_modules were always excluded)
CREDENTIAL_DIRS = {".ssh", ".aws", ".gnupg"}

# Suspected credential/key files: skipped during packaging and logged (lowercased before matching)
SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_dsa*", "id_ecdsa*", "*.ppk", "*.jks",
    ".npmrc", ".netrc", "credentials", "credentials.*", ".git-credentials",
    ".pgpass", ".my.cnf", ".envrc", ".bash_history", ".zsh_history",
)
MAX_TOTAL_BYTES = 500 * 1024 * 1024   # Total source-tree size limit
MAX_FILE_BYTES = 50 * 1024 * 1024     # Single-file size limit


class SourceGuardError(Exception):
    """Source tree failed safety checks (out-of-tree symlink/junction or size limit); refuse to package."""


def is_sensitive_file(name) -> bool:
    low = str(name).lower()
    return any(fnmatch.fnmatch(low, pat) for pat in SENSITIVE_FILE_PATTERNS)


def is_link(p) -> bool:
    """Treat both symlinks and Windows junctions as links.

    os.path.islink only detects symlinks and returns False for junctions (mklink /J,
    creatable without elevation), which walk/copytree would recurse into and materialize.
    Uses os.path.isjunction on Python 3.12+, falling back to FILE_ATTRIBUTE_REPARSE_POINT
    (0x400) on older Windows versions (symlinks are also reparse points, complementing islink)."""
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
    """Return True if a link target (already confirmed in-tree) lies inside an excluded dir.

    Prevents credential dirs like .ssh/.aws from bypassing exclusion via in-tree links."""
    rel = os.path.relpath(target_real, src_real)
    return any(part.lower() in EXCLUDE_DIRS for part in rel.split(os.sep))


def check_source_tree(src):
    """Pre-copy safety check. Returns {"skip": set(paths), "skipped_sensitive": [relpaths],
    "excluded_credential_dirs": [relpaths]}; raises SourceGuardError on out-of-tree links
    or size limit violations.

    The skip set uses src's input path form to match copytree's ignore callback; boundary
    checks use realpath separately (on macOS /var is a symlink to /private/var).
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
                        f"source directory contains link pointing outside root (symlink/junction), refusing to package: {p} -> {target}")
                # In-tree link targeting a sensitive file or excluded dir (e.g. .ssh): skip
                # the link itself so credentials cannot enter the output via materialization
                if is_sensitive_file(os.path.basename(target)) or _target_in_excluded_dir(target, src_real):
                    skipped.append(os.path.relpath(p, src))
                    skip.add(p)
        # Junctions are not islink, so os.walk would descend into them (risking infinite
        # loops and double-counted sizes); out-of-tree links were already rejected above,
        # and in-tree junctions are materialized by copytree just like symlinks.
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
                    f"file exceeds {MAX_FILE_BYTES // 1024 // 1024}MB limit, refusing to package: {p}")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise SourceGuardError(
                    f"source directory exceeds {MAX_TOTAL_BYTES // 1024 // 1024}MB total limit, refusing to package: {src}")
    return {"skip": skip, "skipped_sensitive": skipped,
            "excluded_credential_dirs": cred_dirs}


def make_copy_ignorer(exclude_names=(), guard=None):
    """Build a copytree ignore callback: case-insensitive exact name exclusions (convert's
    dialect exclusions + credential/IDE dirs) + sensitive files + pre-scan skip set + *.pyc."""
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
