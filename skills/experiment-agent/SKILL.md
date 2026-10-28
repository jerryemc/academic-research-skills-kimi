---
name: experiment-agent
description: "Experiment executor and monitor for academic research. 2-agent system covering code experiments (ML training, statistical analysis, ETL, simulation) and human studies (surveys, field studies, interviews). 4 modes: run (execute + monitor code), manage (track human studies), validate (statistical interpretation + reproducibility verification), plan (Socratic experiment design). Triggers on: run experiment, execute code, train model, benchmark, manage study, track participants, field study, survey, validate results, check statistics, reproduce, plan experiment, design study, 跑實驗, 執行程式, 管理研究, 驗證結果, 規劃實驗."
---

# experiment-agent

Read `${KIMI_SKILL_DIR}/../../ars/experiment-agent/WORKFLOW.md` and follow that workflow for the user's request. Treat the invocation arguments as the requested mode, source material, and output constraints. Load only the supporting files needed for the current stage.
