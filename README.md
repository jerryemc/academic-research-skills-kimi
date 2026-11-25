# Academic Research Skills for Other Agents (Kimi Code · Antigravity)

This repository is a downstream adaptation of [Imbad0202/academic-research-skills-codex](https://github.com/Imbad0202/academic-research-skills-codex) that installs as a native plugin in both **Kimi Code / Kimi Code Desktop** and **Google Antigravity** (Desktop App, IDE, and the `agy` CLI). It provides one router plus six skill entrypoints you can call directly, and keeps the upstream workflow content in a form that can be synced automatically.

## Source and License

- **Direct upstream**: [ARS-Codex](https://github.com/Imbad0202/academic-research-skills-codex)
- **Original ARS**: [academic-research-skills](https://github.com/Imbad0202/academic-research-skills)
- **Original author**: Cheng-I Wu (GitHub: [Imbad0202](https://github.com/Imbad0202))
- **Current upstream commit**: see [.upstream-commit](.upstream-commit)
- **License**: [CC-BY-NC-4.0](LICENSE). Redistribution requires attribution; commercial use is not permitted.
- **Upstream README**: [README.upstream.md](README.upstream.md)

## Installation

The two hosts read separate manifests and share the same `skills/` directory, so they don't interfere with each other:

| Host | Manifest |
|---|---|
| Kimi Code | [`kimi.plugin.json`](kimi.plugin.json) |
| Antigravity | [`.antigravity-plugin/plugin.json`](.antigravity-plugin/plugin.json) |

### Kimi Code

In Kimi Code, open **Install custom plugin** and enter:

```text
https://github.com/jerryemc/academic-research-skills_other-agents
```

A local path to a clone of this repository also works. After installing or updating, run `/reload` or start a new session.

### Antigravity

Install directly from GitHub:

```bash
agy plugin install https://github.com/jerryemc/academic-research-skills_other-agents
```

Or install from a local clone:

```bash
git clone https://github.com/jerryemc/academic-research-skills_other-agents.git
agy plugin install ./academic-research-skills_other-agents
```

For development, you can symlink the repository into the global plugin directory so edits take effect immediately:

```bash
ln -s /path/to/academic-research-skills_other-agents ~/.gemini/config/plugins/academic-research-skills
```

Run `agy plugin list` to confirm the install. The plugin appears under **Customizations → Installed** in the Desktop App, and its skills are available from the next message.

## How to Invoke

### Router

Kimi Code:

```text
/skill:academic-research-skills:academic-research-suite I want a systematic review of this paper
```

Antigravity:

```text
/academic-research-suite I want a systematic review of this paper
```

The router applies the routing rules first, then reads one workflow plus the supporting files needed for the current stage.

### Direct Skill Entrypoints

| Skill | Purpose |
|---|---|
| `deep-research` | Research questions, literature search, deep research, evidence synthesis |
| `academic-paper` | Paper planning, writing, revision, citations, and formatting |
| `academic-paper-reviewer` | Simulated peer review, re-review, and reviewer calibration |
| `academic-pipeline` | Staged workflow from research through a finished paper |
| `sr-screener` | Study screening for systematic and scoping reviews |
| `experiment-agent` | Code experiments, human-study protocols, and reproducibility checks |

Examples:

```text
# Kimi Code
/skill:academic-research-skills:academic-paper Use plan mode to plan my paper

# Antigravity
/academic-paper Use plan mode to plan my paper
```

## Layout and Generation Rules

```text
.antigravity-plugin/plugin.json   # Antigravity manifest
kimi.plugin.json                  # Kimi Code manifest
ars/                              # The single copy of upstream workflow content
skills/
  academic-research-suite/SKILL.md # Router (generated)
  <workflow>/SKILL.md              # Six thin entrypoints (generated)
tools/kimi-sync/
  ars_workflows.json              # The six workflows allowed to be exposed
  generate_ars_skill_entries.py   # Entrypoint generator
  sync_ars_codex.py               # Upstream synchronizer
  templates/                      # Entrypoint templates
```

- Each entrypoint only points to `ars/<workflow>/WORKFLOW.md`; the workflow text is never copied.
- Kimi Code expands `${KIMI_SKILL_DIR}` in the entrypoints. The generator also adds a path note so hosts that leave the variable unexpanded (such as Antigravity) resolve `../../ars/` relative to the directory containing `SKILL.md`.
- The synchronizer keeps the `version` in `.antigravity-plugin/plugin.json` identical to the one in `kimi.plugin.json`.
- Both `ars/` and the generated `skills/` are committed, so installation needs no build step.
- **Do not hand-edit the generated `skills/*/SKILL.md` files.** Change the mapping or the templates, then rerun the generator.
- When upstream only changes workflow bodies, references, or scripts, the thin entrypoints usually stay unchanged.

Only Skills are registered; Hooks, commands, and plugin agents are not. The upstream Hooks and commands kept under `ars/` are reference material only and are never executed automatically by either host.

## Weekly Sync

A GitHub Actions job checks ARS-Codex every week. When it finds an update, it:

1. syncs `ars/` and regenerates the entrypoints;
2. runs the synchronizer tests, the Kimi manifest check, and the Antigravity manifest version check;
3. pushes a `sync/ars-codex-<short-sha>` branch;
4. creates or updates a PR for manual review. **It never pushes to or auto-merges into `main`.**

New upstream workflows are only listed in the sync report and are not registered automatically. To expose one, update `tools/kimi-sync/ars_workflows.json` after manual review. If a required workflow is removed or renamed, or any check fails, the sync job fails and nothing is published.

Manual local sync:

```bash
python3 tools/kimi-sync/sync_ars_codex.py \
  --upstream /path/to/academic-research-skills-codex \
  --repo-root .
```

## Validation

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest tools/kimi-sync/tests -v
python3 tools/kimi-sync/validate_plugin.py .     # Kimi Code manifest
agy plugin validate --strict .                   # Antigravity manifest (requires agy)
```

Before releasing, start a new session in both Kimi Code Desktop and Antigravity and check all seven entrypoints, resource-path resolution, and ordinary Write/Edit operations.
