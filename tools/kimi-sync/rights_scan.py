#!/usr/bin/env python3
"""rights_scan.py — 外部插件源目录的权利限制扫描（转换/登记前置）。

扫描两类信号（移植自 agent-plugin-manager 分发版 kimi_install.py 的同名逻辑）：
- 权利限制短语（severity: high）：仅在许可语境触发——LICENSE*/COPYING*/NOTICE* 文件、
  manifest 的 license 字段、SKILL.md frontmatter 的 license 字段、方言 manifest 的
  license 值（extra_license 传入）。命中专有许可、All Rights Reserved、Confidential、
  EULA、禁止再分发、限定平台使用、非商业/试用、内部使用、商业秘密等短语。
  含已知开源许可标记（MIT/Apache/GPL/BSD/ISC/MPL 等）的 LICENSE 文件跳过短语扫描，
  避免 BSD 自带 "All rights reserved." 误报；README 正文命中只降级为警告（warnings）。
- 厂商/平台信号（severity: notice）：manifest 的 author/publisher 含知名厂商名或官方
  org 前缀、仓库/主页/git remote URL 指向厂商官方 org、来源路径含
  marketplace/official/bundled 等成分。代码签名证书组织字段是可空跑的检查点
  （本地目录形态不携带签名材料，固定返回 None）。

命中不阻断转换：convert 把命中写进 conversion-report.json 并把插件分流进
registration_plan 的 hold_rights_review 暂缓桶，用户审阅 plugin-builder-report.md
后用 register_converted.py --only 放行；单个登记（register_personal）由本模块的
CLI 形态把守，命中需 --confirm-rights 显式确认（exit 7 中止）。

CLI：python3 rights_scan.py <目录> [--confirm-rights]
  输出结构化 JSON；命中且未确认时 exit 7，全干净或已确认 exit 0。
  扫描为启发式风险提示，不能替代人工甄别。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plugin_common as pc

RIGHTS_MAX_READ = 256 * 1024  # 单文件有界读取上限

# 法务提示文案（stderr 用纯文本）
INSTALL_SAFETY_NOTICE = ("安全提示：插件可能来自第三方，请在安装前确认插件来源可信，以免造成账号、数据损失等。"
                         "Kimi Work对插件的扫描不能覆盖全部风险，请您注意甄别。")
RIGHTS_CONFIRM_NOTICE = "检测到该插件可能带有权利限制或平台专属条款。继续安装前，请确认您有权安装和使用该插件。"
RIGHTS_RISK_NOTICE = ("权利风险提示：检测到该插件的许可文件包含权利限制条款（{categories}），"
                      "可能禁止再分发或限定使用平台。继续安装前，请确认您有权安装和使用该插件；"
                      "继续安装即视为您已自行确认并承担相应责任。")

# 权利限制短语：匹配为 case-insensitive + 字母数字边界（防嵌入更长单词的误报）
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
# 已知开源许可标记：LICENSE 文件含有这些标记时跳过权利限制短语扫描
KNOWN_OSS_MARKERS = (
    "mit license", "apache license", "gnu general public license",
    "gnu lesser general public license", "mozilla public license", "isc license",
    "bsd license", "permission is hereby granted",
    "redistribution and use in source and binary forms",
)
# 厂商元数据信号
VENDOR_NAMES = ("anthropic", "openai", "google", "microsoft", "apple", "meta")
VENDOR_ORG_PREFIXES = ("@anthropic-ai", "@anthropic", "@openai", "@google", "@googleapis",
                       "@microsoft", "@apple", "@meta", "@facebook", "@facebookincubator")
VENDOR_ORG_URLS = ("github.com/anthropics/", "github.com/anthropic-ai/", "github.com/openai/",
                   "github.com/google/", "github.com/googleapis/", "github.com/microsoft/",
                   "github.com/apple/", "github.com/meta/", "github.com/facebook/",
                   "github.com/facebookincubator/")
VENDOR_PATH_COMPONENTS = {"marketplace", "marketplaces", "official", "bundled"} | set(VENDOR_ORG_PREFIXES)


def _phrase_re(phrase):
    """case-insensitive 子串匹配，两侧要求非字母数字边界（防误报）。"""
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])", re.I)


RIGHTS_RESTRICT_RES = [(phrase, label, _phrase_re(phrase)) for phrase, label in RIGHTS_RESTRICT_PHRASES]
VENDOR_NAME_RES = [(name, _phrase_re(name)) for name in VENDOR_NAMES]


def _read_bounded(f, limit=RIGHTS_MAX_READ):
    """有界读取文本（≤256KB），失败/超限返回 None，语义上由调用方跳过。"""
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
    """读取顶层 manifest（package.json / plugin.json / kimi.plugin.json），有界 + 坏 JSON 跳过。"""
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
    """顶层 SKILL.md 与 skills/*/SKILL.md 的 frontmatter license 字段值。"""
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
    """厂商名（边界匹配）或官方 org 前缀（子串）。返回命中的值或 None。"""
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
    """代码签名证书主体组织（O=...）检查点。本地目录形态一般不携带签名材料，
    本模块无对应解析器，固定返回 None（可空跑的检查点，留待后续平台接入）。"""
    return None


def scan_rights(src, extra_license=None):
    """扫描源目录的权利限制信号。返回 {"restricted": [...], "warnings": [...], "vendor": [...]}。

    restricted：许可语境权利限制短语命中（severity=high，需用户显式确认）；
    warnings：README 正文命中（降级警告）；
    vendor：厂商元数据信号（severity=notice，同样需用户显式确认）。
    extra_license：额外的许可语境字符串 [(文件标签, context, 值)]——方言 manifest
    （如 .codex-plugin/plugin.json）的 license 字段不在顶层文件名扫描范围内，由
    调用方解析后传入。"""
    src = str(src)
    restricted, warnings, vendor = [], [], []

    # 1. 许可语境文件：LICENSE*/COPYING*/NOTICE*（含已知开源许可标记的跳过短语扫描）
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

    # 2. manifest 的 license 字段 + 厂商 author/publisher/URL 信号
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

    # 2b. 方言 manifest 的 license 值（调用方解析传入）
    for label, context, value in extra_license or []:
        if isinstance(value, str) and value:
            _collect_phrase_hits(value, label, context, restricted)

    # 3. SKILL.md frontmatter 的 license 字段
    for rel, lic in _skill_frontmatter_license(src):
        _collect_phrase_hits(lic, rel, "skill-frontmatter-license", restricted)

    # 4. git remote URL 厂商信号
    git_cfg = _read_bounded(os.path.join(src, ".git", "config"))
    if git_cfg:
        m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', git_cfg)
        if m:
            hit = _vendor_in_url(m.group(1))
            if hit:
                vendor.append({"signal": "vendor-url", "value": hit, "source": ".git/config"})

    # 5. 来源路径特征
    for part in Path(src).parts:
        low = part.lower()
        if low in VENDOR_PATH_COMPONENTS:
            vendor.append({"signal": "vendor-path", "value": low, "source": str(src)})

    # 6. 代码签名检查点（可空跑）
    sig = signing_org_signal(src)
    if sig:
        vendor.append(sig)

    return {"restricted": restricted, "warnings": warnings, "vendor": vendor}


def severity_of(rights):
    """命中分级：许可短语 → high；仅厂商信号 → notice；全干净 → None。"""
    if not rights:
        return None
    if rights.get("restricted"):
        return "high"
    if rights.get("vendor"):
        return "notice"
    return None


def detect_license(src, meta_license=""):
    """优先用原包 metadata 的 license；否则粗看 LICENSE 文件头；都拿不到就 UNKNOWN。
    绝不把外部内容默认标成 MIT / 本开发者原创。"""
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
    # 中文输出钉死 UTF-8：Windows GBK locale + 管道捕获下 print 会 UnicodeEncodeError
    pc.reconfigure_stdio_utf8()
    ap = argparse.ArgumentParser(
        description="权利限制扫描：外部插件源目录的许可短语与厂商/平台信号检查")
    ap.add_argument("source", help="待扫描的源目录")
    ap.add_argument("--confirm-rights", action="store_true",
                    help="命中权利限制关键词或厂商/平台信号时，显式确认有权安装/登记后继续")
    a = ap.parse_args()
    src = Path(a.source).expanduser().resolve()
    if not src.is_dir():
        print(json.dumps({"success": False, "error": f"source 不是目录: {src}"},
                         ensure_ascii=False), file=sys.stderr)
        sys.exit(2)

    rights = scan_rights(str(src))
    severity = severity_of(rights)
    status = "clear"
    if severity and a.confirm_rights:
        status = "confirmed-by-user"
        print(f"权利扫描：severity={severity}，用户已通过 --confirm-rights 显式确认"
              "（继续即视为用户已自行确认并承担相应责任）。", file=sys.stderr)
    elif severity:
        status = "needs-confirmation"

    print(json.dumps({"success": status != "needs-confirmation",
                      "rightsCheck": status, "severity": severity,
                      "matches": rights["restricted"],
                      "warnings": rights["warnings"],
                      "signals": rights["vendor"]}, ensure_ascii=False, indent=2))
    for w in rights["warnings"]:
        print(f"权利扫描警告（README 正文命中，仅提示不拒绝）: "
              f"{w['category']} @ {w['file']}", file=sys.stderr)
    if status == "needs-confirmation":
        if severity == "high":
            cats = ", ".join(sorted({m["category"] for m in rights["restricted"]}))
            print(RIGHTS_RISK_NOTICE.format(categories=cats), file=sys.stderr)
            print("命中明细 → " + "; ".join(
                f"{m['category']} @ {m['file']}（{m['context']}）"
                for m in rights["restricted"]), file=sys.stderr)
        else:
            print(RIGHTS_CONFIRM_NOTICE, file=sys.stderr)
        print(INSTALL_SAFETY_NOTICE, file=sys.stderr)
        print("确认有权登记后重跑时加 --confirm-rights。", file=sys.stderr)
        sys.exit(7)


if __name__ == "__main__":
    main()
