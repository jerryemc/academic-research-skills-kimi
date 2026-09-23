# academic-research-skills（Kimi 版）

本仓库是上游 Claude 插件 [Imbad0202/academic-research-skills](https://github.com/Imbad0202/academic-research-skills) 的 **Kimi 原生插件转换与持续同步镜像**。

## 来源与许可

- **上游仓库**: https://github.com/Imbad0202/academic-research-skills
- **当前同步的上游 commit**: 见仓库根目录的 [.upstream-commit](.upstream-commit)（每次同步由 GitHub Actions 自动更新）
- **原作者**: Cheng-I Wu（GitHub: [Imbad0202](https://github.com/Imbad0202)）
- **许可证**: [CC-BY-NC-4.0](LICENSE)（知识共享署名-非商业性使用 4.0 国际）。再分发需署名、不得用于商业用途。上游原始 README 保留在 [README.upstream.md](README.upstream.md)。
- **转换工具**: `convert_plugin_repo.py`（Kimi Work plugin-builder 技能的副本，存于本仓库 `tools/kimi-sync/`，仅用于本仓库 private 范围内的自动同步）
- **首次转换日期**: 2026-09-23

## 安装（Kimi Code）

在 Kimi Code 的 **custom plugin install** 中填入本仓库 URL 即可安装：

```
https://github.com/jerryemc/academic-research-skills-kimi
```

## 更新机制

本仓库通过 GitHub Actions（[.github/workflows/sync.yml](.github/workflows/sync.yml)）每周一 07:23 UTC 自动拉取上游、重新转换、并把 `kimi.plugin.json` 的 `version` 升级为 `<上游版本>+sync.YYYYMMDD` 格式后推送。Kimi Code 依据 version 变化检出更新并提示安装。

安全说明：若上游的 `hooks/` 或 `scripts/` 目录发生改动，workflow **不会**自动推送，而是自动创建 Issue 提醒人工审阅，确认无误后在 Actions 页面手动 dispatch 才会同步。

## Hooks 说明

本插件包含两个会话事件 hook（脚本内容与原插件一致，未做任何修改）：

- **SessionStart**: 自动执行 `scripts/announce-ars-loaded.sh`（插件加载提示）
- **PreToolUse**（匹配 `Write|Edit|MultiEdit|Bash`）: 自动执行 `hooks/run_guard.sh`（写操作前守卫）

这两个脚本会在 Kimi Code 会话事件中自动执行，**无需**手动运行任何 setup 脚本。
