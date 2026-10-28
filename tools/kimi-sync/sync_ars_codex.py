#!/usr/bin/env python3
"""Synchronize ARS-Codex workflow content and regenerate Kimi Skill entries."""

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from generate_ars_skill_entries import DEFAULT_CONFIG, generate_entries

SOURCE_SKILL = Path("plugins/ars-codex/skills/academic-research-suite")
SOURCE_MANIFEST = Path("plugins/ars-codex/.codex-plugin/plugin.json")


def _workflows(ars_root):
    if not ars_root.is_dir():
        return set()
    return {path.parent.name for path in ars_root.glob("*/WORKFLOW.md")}


def _allowed_workflows():
    config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    return set(config["workflows"])


def _replace_directory(repo_root, staging, name):
    source = staging / name
    target = repo_root / name
    backup = staging / f"previous-{name}"
    if target.exists():
        target.rename(backup)
    source.rename(target)


def sync_upstream(repo_root: Path, upstream_repo: Path) -> dict:
    repo_root = Path(repo_root)
    upstream_repo = Path(upstream_repo)
    source_ars = upstream_repo / SOURCE_SKILL / "ars"
    allowed = _allowed_workflows()
    upstream_workflows = _workflows(source_ars)
    missing = sorted(allowed - upstream_workflows)
    if missing:
        raise FileNotFoundError(f"required workflow missing: {', '.join(missing)}")

    manifest_path = upstream_repo / SOURCE_MANIFEST
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_version = source_manifest["version"]
    source_sha = subprocess.run(
        ["git", "-C", str(upstream_repo), "rev-parse", "HEAD"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    current_workflows = _workflows(repo_root / "ars")
    added = sorted(upstream_workflows - allowed)
    removed = sorted(current_workflows - upstream_workflows)

    staging = Path(tempfile.mkdtemp(prefix=".ars-codex-sync-", dir=repo_root))
    try:
        shutil.copytree(source_ars, staging / "ars")
        if (repo_root / "skills").is_dir():
            shutil.copytree(repo_root / "skills", staging / "skills")
        shutil.copy2(upstream_repo / "README.md", staging / "README.upstream.md")
        target_manifest = json.loads((repo_root / "kimi.plugin.json").read_text(encoding="utf-8"))
        date_suffix = datetime.now(timezone.utc).strftime("%Y%m%d")
        target_manifest["version"] = f"{source_version}+kimi.{date_suffix}"
        (staging / "kimi.plugin.json").write_text(
            json.dumps(target_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging / ".upstream-commit").write_text(source_sha + "\n", encoding="utf-8")
        entry_report = generate_entries(staging, DEFAULT_CONFIG)

        _replace_directory(repo_root, staging, "ars")
        _replace_directory(repo_root, staging, "skills")
        for name in ("README.upstream.md", "kimi.plugin.json", ".upstream-commit"):
            shutil.copy2(staging / name, repo_root / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return {
        "source_sha": source_sha,
        "source_version": source_version,
        "added_workflows": added,
        "removed_workflows": removed,
        "entry_report": entry_report,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = sync_upstream(args.repo_root, args.upstream)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
