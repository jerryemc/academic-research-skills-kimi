---
name: academic-research-suite
description: "Route academic research, literature reviews, paper writing, manuscript review, study screening, experiment planning, and end-to-end research pipelines to the appropriate ARS workflow."
---

# Academic Research Suite

Select exactly one workflow for the user's request. Apply the routing rules in `${KIMI_SKILL_DIR}/../../ars/shared/references/routing_core.md` before reading a workflow.

- `deep-research`: read `${KIMI_SKILL_DIR}/../../ars/deep-research/WORKFLOW.md`.
- `academic-paper`: read `${KIMI_SKILL_DIR}/../../ars/academic-paper/WORKFLOW.md`.
- `academic-paper-reviewer`: read `${KIMI_SKILL_DIR}/../../ars/academic-paper-reviewer/WORKFLOW.md`.
- `academic-pipeline`: read `${KIMI_SKILL_DIR}/../../ars/academic-pipeline/WORKFLOW.md`.
- `sr-screener`: read `${KIMI_SKILL_DIR}/../../ars/sr-screener/WORKFLOW.md`.
- `experiment-agent`: read `${KIMI_SKILL_DIR}/../../ars/experiment-agent/WORKFLOW.md`.

Read only the selected workflow first, then load the agents, references, templates, and shared files required for the current stage. Use Kimi Code's available tools and the active session model; do not assume another host's tool names, model policy, hooks, or command registry.
