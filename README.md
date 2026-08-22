# CoreProtect-Autobuild

每天自动跟踪 [PlayPro/CoreProtect](https://github.com/PlayPro/CoreProtect) 仓库上游更新并自动构建。

## 获取构建

本仓库仅保留单一动态更新的最新 Release：

* **最新发布版本**：[Releases / latest](https://github.com/Linwendong83/CoreProtect-Autobuild/releases/tag/latest)
* **直接下载链接**：[CoreProtect 最新构建 Jar](https://github.com/Linwendong83/CoreProtect-Autobuild/releases/latest/download/CoreProtect-24.0.jar)

## 自动化特性

- **定时检测**：每天 UTC 20:00 自动检查上游提交。
- **智能跳过**：若上游无提交或仅变更文档/CI 配置文件，自动跳过构建。
- **单一 Release**：每次成功构建自动更新 `latest` Release 中的 Jar 产物与增量提交日志。
- **支持手动触发**：支持在 GitHub Actions 手动调度，并可通过 `force_build: true` 强制重新构建。
