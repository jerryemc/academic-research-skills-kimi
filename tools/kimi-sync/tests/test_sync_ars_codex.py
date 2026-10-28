import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import sync_ars_codex as syncer

WORKFLOWS = json.loads((TOOLS_DIR / "ars_workflows.json").read_text(encoding="utf-8"))["workflows"]
SOURCE_SKILL = Path("plugins/ars-codex/skills/academic-research-suite")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def commit_upstream(repo: Path) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "fixture update")
    return git(repo, "rev-parse", "HEAD")


def write_upstream_workflow(upstream: Path, name: str) -> None:
    workflow = upstream / SOURCE_SKILL / "ars" / name / "WORKFLOW.md"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Upstream instructions for {name}.\n"
        "---\n\n"
        f"Workflow body for {name}.\n",
        encoding="utf-8",
    )


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.com")
    for name in WORKFLOWS:
        write_upstream_workflow(repo, name)
    routing_core = repo / SOURCE_SKILL / "ars" / "shared" / "references" / "routing_core.md"
    routing_core.parent.mkdir(parents=True)
    routing_core.write_text("Route one workflow.\n", encoding="utf-8")
    manifest = repo / "plugins/ars-codex/.codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"ars-codex","version":"3.24.0"}\n', encoding="utf-8")
    (repo / "README.md").write_text("Codex upstream readme.\n", encoding="utf-8")
    (repo / SOURCE_SKILL / "SKILL.md").write_text("Codex router\n", encoding="utf-8")
    (repo / SOURCE_SKILL / "codex").mkdir()
    (repo / SOURCE_SKILL / "codex" / "policy.md").write_text("Codex only\n", encoding="utf-8")
    commit_upstream(repo)
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    target = tmp_path / "target"
    target.mkdir()
    (target / "kimi.plugin.json").write_text(
        '{"name":"academic-research-skills","version":"0.1.0"}\n',
        encoding="utf-8",
    )
    (target / ".upstream-commit").write_text("old-sha\n", encoding="utf-8")
    return target


def tree_bytes(root: Path) -> dict:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_sync_mirrors_content_records_source_and_generates_entries(repo: Path, upstream: Path):
    stale = repo / "ars" / "old-workflow" / "WORKFLOW.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale\n", encoding="utf-8")
    expected_sha = git(upstream, "rev-parse", "HEAD")

    report = syncer.sync_upstream(repo, upstream)

    assert report["source_sha"] == expected_sha
    assert report["source_version"] == "3.24.0"
    assert report["added_workflows"] == []
    assert report["removed_workflows"] == ["old-workflow"]
    assert not stale.exists()
    assert (repo / ".upstream-commit").read_text(encoding="utf-8").strip() == expected_sha
    assert (repo / "README.upstream.md").read_text(encoding="utf-8") == "Codex upstream readme.\n"
    manifest = json.loads((repo / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"].startswith("3.24.0+kimi.")
    assert len(manifest["version"].rsplit(".", 1)[-1]) == 8
    assert len(list((repo / "skills").glob("*/SKILL.md"))) == 7
    for name in WORKFLOWS:
        assert (repo / "ars" / name / "WORKFLOW.md").is_file()
    assert not (repo / "SKILL.md").exists()
    assert not (repo / "codex").exists()
    assert not (repo / ".codex-plugin").exists()


def test_new_upstream_workflow_is_only_reported(repo: Path, upstream: Path):
    write_upstream_workflow(upstream, "new-workflow")
    commit_upstream(upstream)

    report = syncer.sync_upstream(repo, upstream)

    assert report["added_workflows"] == ["new-workflow"]
    assert (repo / "ars" / "new-workflow" / "WORKFLOW.md").is_file()
    assert not (repo / "skills" / "new-workflow").exists()


def test_missing_required_workflow_leaves_target_untouched(repo: Path, upstream: Path):
    syncer.sync_upstream(repo, upstream)
    before = {
        name: tree_bytes(repo / name)
        for name in ("ars", "skills")
    }
    before_manifest = (repo / "kimi.plugin.json").read_bytes()
    before_sha = (repo / ".upstream-commit").read_bytes()
    (upstream / SOURCE_SKILL / "ars" / "academic-paper" / "WORKFLOW.md").unlink()
    commit_upstream(upstream)

    with pytest.raises(FileNotFoundError, match="academic-paper"):
        syncer.sync_upstream(repo, upstream)

    assert tree_bytes(repo / "ars") == before["ars"]
    assert tree_bytes(repo / "skills") == before["skills"]
    assert (repo / "kimi.plugin.json").read_bytes() == before_manifest
    assert (repo / ".upstream-commit").read_bytes() == before_sha


def test_repeat_sync_reports_existing_entries_as_unchanged(repo: Path, upstream: Path):
    syncer.sync_upstream(repo, upstream)

    report = syncer.sync_upstream(repo, upstream)

    assert report["entry_report"]["generated"] == []
    assert sorted(report["entry_report"]["unchanged"]) == sorted(WORKFLOWS + ["academic-research-suite"])
