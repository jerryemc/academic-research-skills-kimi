# Academic Research Skills for Other Agents (Kimi Code · Antigravity)

本仓库是 [Imbad0202/academic-research-skills-codex](https://github.com/Imbad0202/academic-research-skills-codex) 的下游适配插件，同时支持 **Kimi Code / Kimi Code Desktop** 与 **Google Antigravity**（Desktop App / IDE / `agy` CLI）。它提供一个总 Router 和六个可独立调用的 Skill 入口，并保留可自动同步的上游工作流内容。

## 来源与许可

- **直接上游**: [ARS-Codex](https://github.com/Imbad0202/academic-research-skills-codex)
- **原始 ARS**: [academic-research-skills](https://github.com/Imbad0202/academic-research-skills)
- **原作者**: Cheng-I Wu（GitHub: [Imbad0202](https://github.com/Imbad0202)）
- **当前上游 commit**: 见 [.upstream-commit](.upstream-commit)
- **许可证**: [CC-BY-NC-4.0](LICENSE)。再分发需署名，不得用于商业用途。
- **上游 README**: [README.upstream.md](README.upstream.md)

## 安装

### Kimi Code

在 Kimi Code 的 **Install custom plugin** 中填入：

```text
https://github.com/jerryemc/academic-research-skills_other-agents
```

也可以填入本仓库的本地目录路径。安装或更新后，执行 `/reload` 或新建会话。

### Antigravity

Antigravity 读取 [`.antigravity-plugin/plugin.json`](.antigravity-plugin/plugin.json)，与 Kimi 的 `kimi.plugin.json` 互不干扰，两者共用同一份 `skills/`。

```bash
git clone https://github.com/jerryemc/academic-research-skills_other-agents.git
agy plugin install ./academic-research-skills_other-agents
```

或者把仓库软链接到全局插件目录（开发时修改即时生效）：

```bash
ln -s /path/to/academic-research-skills_other-agents ~/.gemini/config/plugins/academic-research-skills
```

安装后在 Desktop App 的 **Customizations → Installed** 中可见，七个入口可直接用斜杠命令调用，例如 `/academic-research-suite`、`/deep-research`、`/academic-paper`。

## 两种调用方式

### 总 Router

```text
/skill:academic-research-skills:academic-research-suite 我想系统审阅这篇论文
```

（Antigravity 中为 `/academic-research-suite 我想系统审阅这篇论文`。）

总入口先应用路由规则，再读取一个工作流及其当前阶段所需的支持文件。

### 独立 Skill 入口

| Skill | 用途 |
|---|---|
| `deep-research` | 研究问题、文献检索、深度研究、证据综合 |
| `academic-paper` | 论文规划、写作、修订、引用与格式 |
| `academic-paper-reviewer` | 模拟审稿、复审与评审校准 |
| `academic-pipeline` | 从研究到论文完成的分阶段流程 |
| `sr-screener` | 系统综述与范围综述的文献筛选 |
| `experiment-agent` | 代码实验、人体研究方案与可复现性验证 |

例如：

```text
/skill:academic-research-skills:academic-paper 使用 plan 模式规划我的论文
```

## 目录与生成规则

```text
ars/                              # 上游工作流唯一内容副本
skills/
  academic-research-suite/SKILL.md # 总 Router，生成文件
  <workflow>/SKILL.md              # 六个薄入口，生成文件
tools/kimi-sync/
  ars_workflows.json              # 允许公开的六个工作流
  generate_ars_skill_entries.py   # 入口生成器
  sync_ars_codex.py               # 上游同步器
  templates/                      # Kimi 专用模板
```

- 独立入口只引用 `ars/<workflow>/WORKFLOW.md`，不复制工作流正文。
- 入口中的 `${KIMI_SKILL_DIR}` 由 Kimi Code 展开；生成器同时写入一段路径回退说明，让不展开该变量的宿主（如 Antigravity）按 `SKILL.md` 所在目录解析 `../../ars/`。
- 同步器会让 `.antigravity-plugin/plugin.json` 的 `version` 与 `kimi.plugin.json` 保持一致。
- `ars/` 和生成后的 `skills/` 都提交到仓库，安装时无需构建。
- **不要手工修改生成的 `skills/*/SKILL.md`**；修改映射或模板后重新运行生成器。
- 上游只修改工作流正文、references 或 scripts 时，薄入口通常保持不变。

首版只注册 Skills，不注册 Hooks、commands 或 plugin agents。`ars/` 中保留的上游 Hooks 和 commands 仅是资源，不会由 Kimi 自动执行。

## 每周同步

GitHub Actions 每周检查 ARS-Codex，并在发现更新时：

1. 同步 `ars/` 并重新生成入口；
2. 运行同步器测试和 Kimi manifest 校验；
3. 推送 `sync/ars-codex-<short-sha>` 分支；
4. 创建或更新人工审阅 PR，**不直接推送或自动合并 main**。

新增上游工作流只会写入同步报告，不会自动注册。需要公开时，人工审阅后更新 `tools/kimi-sync/ars_workflows.json`。必需工作流被删除、重命名或任一检查失败时，同步任务失败且不发布。

本地手动同步：

```bash
python3 tools/kimi-sync/sync_ars_codex.py \
  --upstream /path/to/academic-research-skills-codex \
  --repo-root .
```

## 验证

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest tools/kimi-sync/tests -v
python3 tools/kimi-sync/validate_plugin.py .
agy plugin validate --strict .   # Antigravity（本地安装了 agy 时）
```

发布前还应在 Kimi Code Desktop 与 Antigravity 中从新会话验证七个入口、资源路径和普通 Write/Edit。
