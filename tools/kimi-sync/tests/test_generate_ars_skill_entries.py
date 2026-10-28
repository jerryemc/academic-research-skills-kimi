import json
import sys
from pathlib import Path

import pytest
import yaml

TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import generate_ars_skill_entries as generator

WORKFLOWS = [
    "deep-research",
    "academic-paper",
    "academic-paper-reviewer",
    "academic-pipeline",
    "sr-screener",
    "experiment-agent",
]
ROUTER = "academic-research-suite"


def write_workflow(repo: Path, name: str, body: str | None = None) -> Path:
    workflow = repo / "ars" / name / "WORKFLOW.md"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Instructions for {name}.\n"
        "---\n\n"
        f"# {name}\n\n{body or f'Upstream body for {name}.'}\n",
        encoding="utf-8",
    )
    return workflow


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for name in WORKFLOWS:
        write_workflow(tmp_path, name)
    routing_core = tmp_path / "ars" / "shared" / "references" / "routing_core.md"
    routing_core.parent.mkdir(parents=True)
    routing_core.write_text("Route to exactly one workflow.\n", encoding="utf-8")
    config = tmp_path / "ars_workflows.json"
    config.write_text(json.dumps({"workflows": WORKFLOWS}), encoding="utf-8")
    return tmp_path


def config_for(repo: Path) -> Path:
    return repo / "ars_workflows.json"


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def test_generates_router_and_six_thin_entries(repo: Path):
    report = generator.generate_entries(repo, config_for(repo))

    entries = sorted(path.parent.name for path in (repo / "skills").glob("*/SKILL.md"))
    assert entries == sorted(WORKFLOWS + [ROUTER])
    assert sorted(report["generated"]) == sorted(WORKFLOWS + [ROUTER])
    assert report["unchanged"] == []
    assert report["new_upstreams"] == []

    for name in WORKFLOWS:
        source = repo / "ars" / name / "WORKFLOW.md"
        entry = repo / "skills" / name / "SKILL.md"
        assert frontmatter(entry) == frontmatter(source)
        body = entry.read_text(encoding="utf-8").split("---", 2)[2]
        assert "${KIMI_SKILL_DIR}/../../ars/" + name + "/WORKFLOW.md" in body
        assert f"Upstream body for {name}." not in body

    router = (repo / "skills" / ROUTER / "SKILL.md").read_text(encoding="utf-8")
    assert "${KIMI_SKILL_DIR}/../../ars/shared/references/routing_core.md" in router
    for name in WORKFLOWS:
        assert "${KIMI_SKILL_DIR}/../../ars/" + name + "/WORKFLOW.md" in router


def test_workflow_body_update_leaves_entry_bytes_unchanged(repo: Path):
    generator.generate_entries(repo, config_for(repo))
    before = {
        path.relative_to(repo): path.read_bytes()
        for path in (repo / "skills").glob("*/SKILL.md")
    }
    write_workflow(repo, "academic-paper", body="A completely revised upstream workflow.")

    report = generator.generate_entries(repo, config_for(repo))

    assert report["generated"] == []
    assert sorted(report["unchanged"]) == sorted(WORKFLOWS + [ROUTER])
    after = {
        path.relative_to(repo): path.read_bytes()
        for path in (repo / "skills").glob("*/SKILL.md")
    }
    assert after == before


def test_new_upstream_workflow_is_reported_without_registration(repo: Path):
    write_workflow(repo, "new-workflow")

    report = generator.generate_entries(repo, config_for(repo))

    assert report["new_upstreams"] == ["new-workflow"]
    assert not (repo / "skills" / "new-workflow").exists()
    assert len(list((repo / "skills").glob("*/SKILL.md"))) == 7


def test_missing_required_workflow_preserves_existing_entries(repo: Path):
    generator.generate_entries(repo, config_for(repo))
    before = {
        path.relative_to(repo): path.read_bytes()
        for path in (repo / "skills").glob("*/SKILL.md")
    }
    (repo / "ars" / "academic-paper" / "WORKFLOW.md").unlink()

    with pytest.raises(FileNotFoundError, match="academic-paper"):
        generator.generate_entries(repo, config_for(repo))

    after = {
        path.relative_to(repo): path.read_bytes()
        for path in (repo / "skills").glob("*/SKILL.md")
    }
    assert after == before
