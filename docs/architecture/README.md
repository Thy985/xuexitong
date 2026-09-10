# docs/architecture/ — 架构说明

本目录放置系统架构说明、状态机语义、调度/注册表/持久化设计文档。

当前仓库的核心架构文档主要分布在：
- 代码注释与「单一起源」模型：`models.py`（Root，shared domain model，未被运行时 import，暂留根层）。
- 各核心模块自带 docstring：`app/run.py`、`scheduler/scheduler.py`、`tvdp/tdvp.py`、`e6/task_registry.py`。
- 历史实验报告（含架构演变）：`e3/E3_Final_Report.md`、`e5/E5_course_lifecycle_report.md`、
  `e6/E6_scheduler_report.md`、`e7/E7_tdvp_report.md`（仍与其 `evidence_*.json` 配套保留在各自实验目录，未挪动）。

> 新增的架构/状态机说明可统一放本目录。