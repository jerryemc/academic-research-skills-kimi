#!/usr/bin/env python3
"""rights_scan.py — Rights-restriction scan for external plugin source directories (pre-conversion/registration).

Scans two signal categories (ported from agent-plugin-manager's kimi_install.py):
- Rights-restriction phrases (severity: high): triggered only in license contexts —
  LICENSE*/COPYING*/NOTICE* files, manifest license fields, SKILL.md frontmatter license
  fields, and dialect manifest license values (passed via extra_license). Matches phrases
  indicating proprietary licenses, All Rights Reserved, Confidential, EULA, no redistribution,
  platform-restricted use, non-commercial/trial, internal use, or trade secrets.
  LICENSE files containing known open-source license markers (MIT/Apache/GPL/BSD/ISC/MPL, etc.)
  skip phrase scanning to avoid false positives on BSD's "All rights reserved."; README body
  hits are downgraded to warnings.
- Vendor/platform signals (severity: notice): manifest author/publisher matching known vendor
  names or official org prefixes, repository/homepage/git remote URLs pointing to official vendor
  orgs, or source path segments such as marketplace/official/bundled. Code-signing certificate
  organization is a no-op hook (local directories carry no signing material and return None).

Hits do not block conversion: convert records them in conversion-report.json and routes the
plugin into registration_plan's hold_rights_review bucket so users can review
plugin-builder-report.md and approve via `register_converted.py --only`. Single-plugin
registration (register_personal) is gated by this module's CLI, requiring --confirm-rights
on hits (otherwise exits with code 7).

CLI: python3 rights_scan.py <dir> [--confirm-rights]
  Outputs structured JSON; exits 7 if hits are unconfirmed, 0 if clean or confirmed.
  Scanning is heuristic and does not replace manual review.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

RIGHTS_MAX_READ = 256 * 1024  # Bounded per-file read limit

# Legal notice text (plain text on stderr)
INSTALL_SAFETY_NOTICE = (
    "Security notice: this plugin may come from a third party. Verify that the source is trusted "
    "before installing to avoid account or data loss. Kimi Work's plugin scan cannot cover all risks; "
    "please review carefully."
)
RIGHTS_CONFIRM_NOTICE = (
    "This plugin may contain rights restrictions or platform-exclusive terms. "
    "Before continuing, confirm that you have the right to install and use this plugin."
)
RIGHTS_RISK_NOTICE = (
    "Rights risk notice: this plugin's license files contain restrictive terms ({categories}) "
    "that may prohibit redistribution or restrict platform use. Before continuing, confirm that "
    "you have the right to install and use this plugin; continuing means you accept responsibility."
)

# Rights-restriction phrases: matched case-insensitively with alphanumeric boundaries to avoid embedded-word hits
RIGHTS_RESTRICT_PHRASES = (
    ("proprietary", "proprietary-license"),
    ("all rights reserved", "all-rights-reserved"),
    ("confidential", "confidential"),
    ("end user license agreement", "eula"),
    ("eula", "eula"),
    ("not for redistribution", "no-redistribution"),
    ("may not be redistributed", "no-redistribution"),
    ("do not redistribute", "no-redistribution"),
    ("for use only with", "use-restriction"),
    ("solely for use with", "use-restriction"),
    ("limited to use within", "use-restriction"),
    ("non-commercial use only", "non-commercial-only"),
    ("evaluation license", "evaluation-or-trial"),
    ("trial license", "evaluation-or-trial"),
    ("internal use only", "internal-use-only"),
    ("trade secret", "trade-secret"),
)
# Known OSS markers: LICENSE files containing any of these skip restriction phrase scanning
KNOWN_OSS_MARKERS = (
    "mit license", "apache license", "gnu general public license",
    "gnu lesser general public license", "mozilla public license", "isc license",
    "bsd license", "permission is hereby granted",
    "redistribution and use in source and binary forms",
)
# Vendor metadata signals
VENDOR_NAMES = ("anthropic", "openai", "google", "microsoft", "apple", "meta")
VENDOR_ORG_PREFIXES = ("@anthropic-ai", "@anthropic", "@openai", "@google", "@googleapis",
                       "@microsoft", "@apple", "@meta", "@facebook", "@facebookincubator")
VENDOR_ORG_URLS = ("github.com/anthropics/", "github.com/anthropic-ai/", "github.com/openai/",
                   "github.com/google/", "github.com/googleapis/", "github.com/microsoft/",
                   "github.com/apple/", "github.com/meta/", "github.com/facebook/",
                   "github.com/facebookincubator/")
VENDOR_PATH_COMPONENTS = {"marketplace", "marketplaces", "official", "bundled"} | set(VENDOR_ORG_PREFIXES)


def _phrase_re(phrase):
    """Case-insensitive substring match requiring non-alphanumeric boundaries on both sides."""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", re.I)


RIGHTS_RESTRICT_RES = [(phrase, label, _phrase_re(phrase)) for phrase, label in RIGHTS_RESTRICT_PHRASES]
VENDOR_NAME_RES = [(name, _phrase_re(name)) for name in VENDOR_NAMES]


def _read_bounded(f, limit=RIGHTS_MAX_READ):
    """Read up to limit bytes (default 256 KB) as UTF-8; return None on OSError so callers skip."""
    try:
        with open(f, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return None


def _collect_phrase_hits(text, rel, context, out):
    for phrase, label, rx in RIGHTS_RESTRICT_RES:
        if rx.search(text):
            out.append({"category": label, "file": rel, "context": context,
                        "matched": phrase})


def _manifest_dicts(src):
    """Read top-level manifests (package.json / plugin.json / kimi.plugin.json), bounded and skipping bad JSON."""
    for name in ("package.json", "plugin.json", "kimi.plugin.json"):
        text = _read_bounded(os.path.join(src, name))
        if not text:
            continue
        try:
            d = json.loads(text)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield name, d


def _skill_frontmatter_license(src):
    """Yield (relpath, license_value) from frontmatter in root SKILL.md and skills/*/SKILL.md."""
    files = [os.path.join(src, "SKILL.md")]
    skills_dir = os.path.join(src, "skills")
    if os.path.isdir(skills_dir):
        try:
            for sub in sorted(os.listdir(skills_dir)):
                files.append(os.path.join(skills_dir, sub, "SKILL.md"))
        except OSError:
            pass
    for f in files:
        text = _read_bounded(f)
        if not text:
            continue
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            kv = re.match(r"^license\s*:\s*(.+?)\s*$", line, re.I)
            if kv:
                yield os.path.relpath(f, src), kv.group(1).strip('"').strip("'")


def _vendor_in_text(text):
    """Match vendor name (boundary) or official org prefix (substring). Return matched value or None."""
    low = text.lower()
    for name, rx in VENDOR_NAME_RES:
        if rx.search(text):
            return name
    for org in VENDOR_ORG_PREFIXES:
        if org in low:
            return org
    return None


def _vendor_in_url(url):
    low = url.lower()
    for org_url in VENDOR_ORG_URLS:
        if org_url in low:
            return org_url
    return None


def signing_org_signal(src):
    """Code-signing certificate subject organization (O=...) hook. Local directories
    do not carry signing material; returns None (no-op placeholder for future platforms)."""
    return None


def scan_rights(src, extra_license=None):
    """Scan a source directory for rights-restriction signals.
    Returns {"restricted": [...], "warnings": [...], "vendor": [...]}.

    restricted: restriction phrase hits in license contexts (severity=high, requires confirmation);
    warnings: README body hits (downgraded to warnings);
    vendor: vendor metadata signals (severity=notice, also requires confirmation).
    extra_license: additional license-context tuples [(file_label, context, value)] for dialect
    manifests (e.g. .codex-plugin/plugin.json) parsed by the caller."""
    src = str(src)
    restricted, warnings, vendor = [], [], []

    # 1. License-context files: LICENSE*/COPYING*/NOTICE* (skip phrase scan if known OSS markers present)
    try:
        entries = sorted(os.listdir(src))
    except OSError:
        entries = []
    for name in entries:
        f = os.path.join(src, name)
        if not os.path.isfile(f):
            continue
        low = name.lower()
        if low.startswith(("license", "copying", "notice")):
            text = _read_bounded(f)
            if text is None:
                continue
            if any(m in text.lower() for m in KNOWN_OSS_MARKERS):
                continue
            _collect_phrase_hits(text, name, "license-file", restricted)
        elif low.startswith("readme"):
            text = _read_bounded(f)
            if text is None:
                continue
            _collect_phrase_hits(text, name, "readme-body", warnings)

    # 2. Manifest license fields + vendor author/publisher/URL signals
    for name, meta in _manifest_dicts(src):
        lic = meta.get("license")
        if isinstance(lic, dict):
            lic = lic.get("type")
        if isinstance(lic, str) and lic:
            _collect_phrase_hits(lic, name, "manifest-license", restricted)
        for field in ("author", "publisher"):
            v = meta.get(field)
            if isinstance(v, dict):
                v = v.get("name") or ""
            if isinstance(v, str) and v:
                hit = _vendor_in_text(v)
                if hit:
                    vendor.append({"signal": "vendor-author", "value": hit,
                                   "source": f"{name}:{field}"})
        for field in ("homepage", "repository", "bugs"):
            v = meta.get(field)
            if isinstance(v, dict):
                v = v.get("url") or ""
            if isinstance(v, str) and v:
                hit = _vendor_in_url(v)
                if hit:
                    vendor.append({"signal": "vendor-url", "value": hit,
                                   "source": f"{name}:{field}"})

    # 2b. Dialect manifest license values passed by caller
    for label, context, value in extra_license or []:
        if isinstance(value, str) and value:
            _collect_phrase_hits(value, label, context, restricted)

    # 3. SKILL.md frontmatter license fields
    for rel, lic in _skill_frontmatter_license(src):
        _collect_phrase_hits(lic, rel, "skill-frontmatter-license", restricted)

    # 4. git remote URL vendor signal
    git_cfg = _read_bounded(os.path.join(src, ".git", "config"))
    if git_cfg:
        m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', git_cfg)
        if m:
            hit = _vendor_in_url(m.group(1))
            if hit:
                vendor.append({"signal": "vendor-url", "value": hit, "source": ".git/config"})

    # 5. Source path components
    for part in Path(src).parts:
        low = part.lower()
        if low in VENDOR_PATH_COMPONENTS:
            vendor.append({"signal": "vendor-path", "value": low, "source": str(src)})

    # 6. Code-signing hook (no-op)
    sig = signing_org_signal(src)
    if sig:
        vendor.append(sig)

    return {"restricted": restricted, "warnings": warnings, "vendor": vendor}


def severity_of(rights):
    """Classify severity: license phrase hits -> 'high'; vendor signals only -> 'notice'; clean -> None."""
    if not rights:
        return None
    if rights.get("restricted"):
        return "high"
    if rights.get("vendor"):
        return "notice"
    return None


def detect_license(src, meta_license=""):
    """Prefer license from package metadata; otherwise inspect LICENSE file header; else UNKNOWN.
    Never default external content to MIT or local-developer attribution."""
    if meta_license:
        return meta_license
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
        f = os.path.join(str(src), name)
        if not os.path.isfile(f):
            continue
        head = (_read_bounded(f, 4096) or "").lower()
        if not head:
            continue
        if "mit license" in head:
            return "MIT"
        if "apache license" in head:
            return "Apache-2.0"
        if "gnu general public license" in head:
            return "GPL"
        if "redistribution and use" in head:
            return "BSD"
        return "UNKNOWN"
    return "UNKNOWN"


def main():
    # Force UTF-8 stdio to avoid UnicodeEncodeError under Windows GBK locale with piped stdout
    pc.reconfigure_stdio_utf8()
    ap = argparse.ArgumentParser(
        description="Rights-restriction scan: check external plugin sources for restrictive license phrases and vendor/platform signals")
    ap.add_argument("source", help="Source directory to scan")
    ap.add_argument("--confirm-rights", action="store_true",
                    help="Explicitly confirm right to install/register when restriction phrases or vendor signals match")
    a = ap.parse_args()
    src = Path(a.source).expanduser().resolve()
    if not src.is_dir():
        print(json.dumps({"success": False, "error": f"source is not a directory: {src}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(2)

    rights = scan_rights(str(src))
    severity = severity_of(rights)
    status = "clear"
    if severity and a.confirm_rights:
        status = "confirmed-by-user"
        print(f"Rights scan: severity={severity}, explicitly confirmed via --confirm-rights "
              "(continuing means the user accepts responsibility).", file=sys.stderr)
    elif severity:
        status = "needs-confirmation"

    print(json.dumps({"success": status != "needs-confirmation",
                      "rightsCheck": status, "severity": severity,
                      "matches": rights["restricted"],
                      "warnings": rights["warnings"],
                      "signals": rights["vendor"]}, ensure_ascii=False, indent=2))
    for w in rights["warnings"]:
        print(f"Rights scan warning (README body hit, informational only): "
              f"{w['category']} @ {w['file']}", file=sys.stderr)
    if status == "needs-confirmation":
        if severity == "high":
            cats = ", ".join(sorted({m["category"] for m in rights["restricted"]}))
            print(RIGHTS_RISK_NOTICE.format(categories=cats), file=sys.stderr)
            print("Match details -> " + "; ".join(
                f"{m['category']} @ {m['file']} ({m['context']})"
                for m in rights["restricted"]), file=sys.stderr)
        else:
            print(RIGHTS_CONFIRM_NOTICE, file=sys.stderr)
        print(INSTALL_SAFETY_NOTICE, file=sys.stderr)
        print("Re-run with --confirm-rights after confirming you have the right to register.", file=sys.stderr)
        sys.exit(7)


if __name__ == "__main__":
    main()
