#!/usr/bin/env python3
"""Generate thin Kimi Skill entrypoints for the vendored ARS workflows."""

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import yaml

ROUTER_NAME = "academic-research-suite"
DEFAULT_CONFIG = Path(__file__).with_name("ars_workflows.json")
ROUTER_TEMPLATE = Path(__file__).with_name("templates") / f"{ROUTER_NAME}.md"


def _frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or text.count("---") < 2:
        raise ValueError(f"{path}: missing YAML frontmatter")
    metadata = yaml.safe_load(text.split("---", 2)[1])
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: invalid YAML frontmatter")
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{path}: frontmatter name is required")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"{path}: frontmatter description is required")
    return name.strip(), description.strip()


def _entry_text(name, description, workflow):
    metadata = (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "---\n"
    )
    target = "${KIMI_SKILL_DIR}/../../ars/" + workflow + "/WORKFLOW.md"
    body = (
        f"\n# {name}\n\n"
        f"Read `{target}` and follow that workflow for the user's request. "
        "Treat the invocation arguments as the requested mode, source material, and output constraints. "
        "Load only the supporting files needed for the current stage.\n"
    )
    return metadata + body


def _router_text(workflows):
    links = "\n".join(
        f"- `{name}`: read `${{KIMI_SKILL_DIR}}/../../ars/{name}/WORKFLOW.md`."
        for name in workflows
    )
    return ROUTER_TEMPLATE.read_text(encoding="utf-8").replace("{{WORKFLOW_LINKS}}", links)


def _write_and_swap(repo_root, entries):
    skills = repo_root / "skills"
    changed = []
    unchanged = []
    for name, text in sorted(entries.items()):
        current = skills / name / "SKILL.md"
        if current.is_file() and current.read_text(encoding="utf-8") == text:
            unchanged.append(name)
        else:
            changed.append(name)

    expected_paths = {f"{name}/SKILL.md" for name in entries}
    current_paths = {
        str(path.relative_to(skills))
        for path in skills.glob("*/SKILL.md")
    } if skills.is_dir() else set()
    if not changed and current_paths == expected_paths:
        return changed, unchanged

    temp_root = Path(tempfile.mkdtemp(prefix=".ars-skills-", dir=repo_root))
    backup = None
    try:
        generated = temp_root / "skills"
        for name, text in entries.items():
            target = generated / name / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if skills.exists():
            backup = temp_root / "previous-skills"
            skills.rename(backup)
        generated.rename(skills)
        if backup is not None:
            shutil.rmtree(backup)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return changed, unchanged


def generate_entries(repo_root: Path, config_path: Path) -> dict:
    repo_root = Path(repo_root)
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    workflows = config.get("workflows")
    if not isinstance(workflows, list) or not workflows or not all(
        isinstance(name, str) and name and name != ROUTER_NAME for name in workflows
    ):
        raise ValueError("config workflows must be a non-empty list of unique workflow names")
    if len(set(workflows)) != len(workflows):
        raise ValueError("config workflows must not contain duplicates")

    discovered = {
        path.parent.name
        for path in (repo_root / "ars").glob("*/WORKFLOW.md")
    }
    new_upstreams = sorted(discovered - set(workflows))
    entries = {ROUTER_NAME: _router_text(workflows)}
    for workflow in workflows:
        source = repo_root / "ars" / workflow / "WORKFLOW.md"
        if not source.is_file():
            raise FileNotFoundError(f"required workflow missing: {workflow}: {source}")
        name, description = _frontmatter(source)
        if name != workflow:
            raise ValueError(f"{source}: frontmatter name {name!r} does not match directory")
        entries[name] = _entry_text(name, description, workflow)

    generated, unchanged = _write_and_swap(repo_root, entries)
    return {
        "generated": generated,
        "unchanged": unchanged,
        "new_upstreams": new_upstreams,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    report = generate_entries(args.repo_root, args.config)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
