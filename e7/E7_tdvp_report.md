# E7 Evidence Pack — Task Discovery & Verification Protocol (TDVP)

## Experiment Overview

**Experiment**: E7  
**Date**: 2026-09-04  
**Status**: ✅ PASS  

### Objectives

实现 TDVP（Task Discovery & Verification Protocol）—— 两阶段探针协议：
- **Passive Probe**：从 studentstudy 页面解析章节/任务列表和 UI 完成标记（低成本、快速）
- **Active Probe**：对 pending/unknown 任务调用真实 Runtime 验证（高成本、准确）

**约束**：不修改现有 Browser Runtime，不实现自动连续执行，不实现 Scheduler。

---

## 1. Architecture

### Two-Stage Probe Protocol

```
Course URL
    ↓
Passive Probe (HTML/DOM parsing, no browser)
    ↓
TaskStatus = COMPLETED / PENDING / UNKNOWN
    ↓
Evidence Aggregator
    ├── COMPLETED(UI) → weak evidence
    ├── PENDING(UI)   → needs Active Probe
    └── UNKNOWN       → needs Active Probe
    ↓
Active Probe (real Runtime, only for PENDING/UNKNOWN)
    ↓
TaskStatus = COMPLETED(SERVER_VERIFIED) / PENDING / UNKNOWN
    ↓
Canonical Task State + Persistent State
```

### New Files

| File | Description |
|------|-------------|
| `tvdp/__init__.py` | Module exports |
| `tvdp/tdvp.py` | Core TDVP module (345 lines) |
| `tests/test_tdvp.py` | 16 unit tests |
| `tests/test_tdvp_integration.py` | 2 integration tests |

---

## 2. Data Model

```python
@dataclass
class TaskEvidence:
    status: TaskStatus           # COMPLETED / PENDING / UNKNOWN
    confidence: ProbeSource      # UI / SERVER_VERIFIED / UNKNOWN
    source_detail: str           # e.g. "UI marker=1" or "isPassed=true"

@dataclass
class TaskInfo:
    task_id: str                 # unique task identifier
    chapter_id: str              # parent chapter
    title: str                   # task title
    task_type: TaskType          # video / quiz / discussion / other
    status: TaskStatus
    confidence: ProbeSource
    source_detail: str
    evidence: TaskEvidence

@dataclass
class ChapterInfo:
    chapter_id: str
    title: str
    tasks: list[TaskInfo]

@dataclass
class CourseDiscovery:
    course_key: str
    chapters: list[ChapterInfo]
    # properties: all_tasks, completed_tasks, pending_tasks, unknown_tasks
```

---

## 3. Passive Probe — HTML Parsing

从学习通 studentstudy 页面提取任务点标记：

```html
<div class="task-item">
    <span class="title">1.6 计算机网络的体系结构</span>
    <span class="status">1</span>   ← 1 = completed, 0 = pending
</div>
```

**解析结果（实测）**：

```
Total: 8, Completed: 4, Pending: 4, Unknown: 0
  [COMPLETED] 1.1 互联网概述              confidence=UI
  [COMPLETED] 1.2 互联网的组成            confidence=UI
  [PENDING]   1.3 计算机网络的概念与类别   confidence=UI
  [COMPLETED] 1.4 计算机网络的拓扑结构     confidence=UI
  [COMPLETED] 1.5 计算机网络的性能        confidence=UI
  [PENDING]   1.6 计算机网络的体系结构     confidence=UI
  [PENDING]   1.7 知识扩展                confidence=UI
  [PENDING]   1.8 线上学习任务            confidence=UI
```

---

## 4. Evidence Aggregator

合并 Passive + Active 结果，SERVER_VERIFIED 优先级高于 UI：

```python
# 规则：
# SERVER_VERIFIED > UI
# PENDING(UI) + Active→COMPLETED(SERVER_VERIFIED) → 升级为 COMPLETED
# COMPLETED(UI) + Active→PENDING → 以 Active 为准
```

---

## 5. Task Registry & Progress Synchronization

**Task Registry** (`state/tdvp_tasks.json`)：
```json
{
  "265997861_151695658": {
    "1217304702_1_1": { "task_id": "...", "status": "COMPLETED", "confidence": "UI", ... },
    "1217304702_1_3": { "task_id": "...", "status": "PENDING",  "confidence": "UI", ... }
  }
}
```

**Progress Sync** 更新 `state/courses/<key>.json`：
- `progress.completed` / `progress.total`
- `progress.last_completed_task`
- `progress.active_task`（队列第一个 pending）
- `discoveries[]`（每次探测历史）

---

## 6. Unit Tests (18 tests, all pass)

```
tests/test_tdvp.py::TestDataModels::test_task_evidence_to_dict PASSED
tests/test_tdvp.py::TestDataModels::test_task_info_roundtrip PASSED
tests/test_tdvp.py::TestDataModels::test_chapter_info_to_dict PASSED
tests/test_tdvp.py::TestDataModels::test_course_discovery_properties PASSED
tests/test_tdvp.py::TestPassiveProbe::test_parse_with_markers PASSED
tests/test_tdvp.py::TestPassiveProbe::test_parse_no_markers_returns_unknown PASSED
tests/test_tdvp.py::TestPassiveProbe::test_parse_returns_task_info_with_correct_fields PASSED
tests/test_tdvp.py::TestEvidenceAggregator::test_server_verified_overrides_ui PASSED
tests/test_tdvp.py::TestEvidenceAggregator::test_passive_only_no_change PASSED
tests/test_tdvp.py::TestEvidenceAggregator::test_new_task_in_active_only PASSED
tests/test_tdvp.py::TestTaskRegistry::test_save_and_load PASSED
tests/test_tdvp.py::TestTaskRegistry::test_get_pending_tasks PASSED
tests/test_tdvp.py::TestTaskRegistry::test_get_completed_tasks PASSED
tests/test_tdvp.py::TestDiscoverCourse::test_discovers_with_chapter_id PASSED
tests/test_tdvp.py::TestDiscoverCourse::test_discovers_without_chapter_id PASSED
tests/test_tdvp.py::TestIntegration::test_real_page_structure PASSED
tests/test_tdvp_integration.py::TestIntegration::test_full_pipeline PASSED
tests/test_tdvp_integration.py::TestIntegration::test_pending_tasks_sort_order PASSED

======================== 18 passed in 0.13s ========================
```

Full suite: **77 passed, 1 skipped** (no regression).

---

## 7. Integration Test — Full Pipeline

```
Discover (Passive Probe) → Register Tasks → Sync to Course State
  ↓
total=8, completed=4, pending=4, task_queue=['1217304702_1_3', '1217304702_1_6', ...]
next_task = '1217304702_1_3'
```

---

## 8. Pass Conditions Checklist

| # | Condition | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Course → Chapter → Task 数据模型 | ✅ | `TaskInfo`, `ChapterInfo`, `CourseDiscovery` |
| 2 | Task Registry | ✅ | `load_task_registry()` / `save_task_registry()` |
| 3 | Discovery 接口 | ✅ | `run_passive_probe()`, `discover_course()` |
| 4 | Progress Synchronization 接口 | ✅ | `sync_progress_to_course_state()` |
| 5 | UNKNOWN 状态 | ✅ | `TaskStatus = "UNKNOWN"`, 无标记时自动分配 |
| 6 | Task Evidence | ✅ | `TaskEvidence` 含 status/confidence/source_detail |
| 7 | State 与 Discovery 结果合并 | ✅ | `aggregate_evidence()` |
| 8 | 不修改现有 Browser Runtime | ✅ | `tvdp/` 完全独立，`app/run.py` 未改动 |
| 9 | 不实现自动连续执行 | ✅ | 仅提供工具函数，无循环调度 |
| 10 | 不实现 Scheduler | ✅ | 未修改 `scheduler/scheduler.py` |
| 11 | 单元测试通过 | ✅ | 18/18 passed |
| 12 | 集成测试通过 | ✅ | Full pipeline test passed |
| 13 | Evidence Pack 生成 | ✅ | This report |
| 14 | 无回归 | ✅ | Full suite 77 passed, 1 skipped |

**Verdict: ✅ PASS**

---

## 9. Key Artifacts

- `tvdp/tdvp.py` — TDVP 核心模块（345 lines）
- `tvdp/__init__.py` — 模块导出
- `tests/test_tdvp.py` — 16 单元测试
- `tests/test_tdvp_integration.py` — 2 集成测试
- `e7/E7_tdvp_report.md` — 本报告
- `e7/evidence_e7.json` — Evidence Pack JSON

---

## 10. API Surface

```python
# Passive Probe（纯 HTML 解析，不启动浏览器）
disc = run_passive_probe(course_url, html, chapter_id=None)
# → CourseDiscovery { all_tasks, completed_tasks, pending_tasks, unknown_tasks }

# Evidence 合并
merged = aggregate_evidence(passive_results, active_results)

# Task Registry（持久化到 state/tdvp_tasks.json）
save_task_registry(course_key, tasks)
pending = get_pending_tasks(course_key)

# Progress Sync（写入 course state）
result = sync_progress_to_course_state(course_key, discovery)
# → { total, completed, pending, task_queue, next_task }

# Active Probe（调用真实 Runtime）
task = run_active_probe(course_url, task_id, chapter_id, output)
# → TaskInfo with SERVER_VERIFIED confidence (or None on failure)
```

---

## 11. Limitations

- Passive Probe 依赖学习通页面 DOM 结构（`.task-item` + `.status` 数字标记）
- 未知任务（无 UI 标记）标记为 UNKNOWN，需 Active Probe 验证
- 当前 `run_active_probe()` 仅定义接口，未集成到 workflow（需手动调用或后续接入 Scheduler）
- 多章节跨页扫描尚未实现（当前只解析传入 HTML 中的任务点）
