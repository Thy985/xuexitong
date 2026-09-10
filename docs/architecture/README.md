# docs/architecture/ — 架构说明

本目录放置系统架构说明、状态机语义、调度/注册表/持久化设计文档。

当前仓库的核心架构文档主要分布在：
- 代码注释与「单一起源」模型：`models.py`（Root，shared domain model，未被运行时 import，暂留根层）。
- 各核心模块自带 docstring：`app/run.py`、`app/registry/task_registry.py`、`app/registry/reconcile.py`、
  `scheduler/scheduler.py`、`tvdp/tdvp.py`。
- 历史实验报告（含架构演变）：E3/E5/E6/E7 系列报告**均已迁入本目录**
  （`E3_Final_Report.md` / `E5_course_lifecycle_report.md` / `E6_scheduler_report.md` / `E7_tdvp_report.md`；
  配套 `evidence_*.json` 在 `docs/evidence/`）。

> 新增的架构/状态机说明可统一放本目录。