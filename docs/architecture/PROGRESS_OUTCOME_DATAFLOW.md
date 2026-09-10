# Progress 五层 Outcome：数据流与成功判据（3D）

> 目标：不再把「completed==0」一棍子判 FAIL，而是分清**哪一层断了 / 哪个信号在撒谎**。
> 每个 outcome 是独立信号；failure 诊断必须落到具体层，而不是笼统 FAIL。
> 依据：`docs/engineering-review/ACTION_HISTORY_AUDIT.md`（62 runs 绿但 `progress.completed` 恒 0）。

---

## 1. 五层数据流（向下推，最终真源在最底）

```
        ┌─────────────────────────────┐
   L1   │  Browser / 播放              │  ended_seen、verdict=PASS、无挂死
        └─────────────┬───────────────┘
        ┌─────────────▼───────────────┐
   L2   │  E2 判定 / ExecutionResult   │  run 退出码、verdict=PASS/FAIL、timing/timed_out
        └─────────────┬───────────────┘
        ┌─────────────▼───────────────┐
   L3   │  Task Registry（派生真源）     │  TaskRecord.status + completion_evidence
        │   章完成 = 全部 task 带 SERVER_VERIFIED/RECHECK/UI 证据
        └─────────────┬───────────────┘
        ┌─────────────▼───────────────┐
   L4   │  TDVP / CourseDiscovery      │  server 中保留的真实「已完成任务点」数
        └─────────────┬───────────────┘
        ┌─────────────▼───────────────┐
   L5   │  CourseState.progress        │  progress.completed / total / active_task
        └─────────────┬───────────────┘
        ┌─────────────▼───────────────┐
   L6   │  超星 Server                  │  服务器真的接受了任务点（最终真源）
        └────────────────────────────┘
```

各层不自动相等 —— 这正是全部历史事故的根源（见 Audit §2）。

## 2. 五种 Outcome（各自独立的真相 / 成功判据）

| Outcome | 定义 | 「成功」判据 | 它测不了什么 |
|---|---|---|---|
| **Execution Outcome** | 播放循环能退出且正常（ended_seen / PASS / 无挂死） | exit_code 0 + verdict=PASS/normal、无 TIMEOUT | 不证明服务器接受点 |
| **Local-State Outcome** | registry 状态被**诚实**更新（无假完成、无重复选章、无降级真相丢失） | 状态机迁移正确；`progress` 从 registry 派生，单一真源 | 不证明服务器接受点 |
| **Registry Outcome** | 「已完成章节」集合 = 全部 task 有 SERVER_VERIFIED/RECHECK/UI 证据 | `done_chapter_ids_from_registry` 与 server 真源偏离为 0？（需 server 采样）| 本地证据可自欺（假 COMPLETED） |
| **Progress Outcome** | `course_state.progress.completed` 反映真相（章节级别 0→N） | run 结束后 completed≥0 且与 done 章一致、单调不降 | 它只反映本地账，不保证服务器真接受 |
| **Server/Persistence Outcome** | 超星真的多记了一个点（course registered） | 事件「server 接受新完成点」可观测（TDVP 探针 × 服务器渲染） | —— |

### 核心判据（不是 "completed==0 → FAIL"）
1. **completed==0 必须拆诊断**，而不是直接 FAIL：
   - L6 为假 → 服务器排斥/未持久化（真不推进）→ 该告警；
   - L5 会计断（运行时从不写 completed）→ 计数器没维护，是**工程 bug**，不是业务不推进；
   - L2 假 PASS（播放器退出但服务器不认）→ E2 verdict 对本地、对服务器都是假的；
   只有在排除「L5 会计断」后，`completed==0` 才可能代表真未推进 → 才谈得上 FAIL/告警。
2. **本次修复目标**：把 L5 接入运行时（`progress.completed` 不再是 test-only），
   使 completed 由 registry 的 done 章驱动（真推进），而不是恒 0。
3. **仍未做（谨慎保留，不拍板）**：L6 "服务器真接受点" 的端到端证明、跨天持久化判定——视为后续。

## 3. 与本批其他 P0 的关系
- P0-06（不掩失败）是 **Execution Outcome** 的真实判定。
- P0-09/11（reconcile）是 **Local-State / registry 账本** 的诚实化。
- P1-12 / 本 3D 是 **Progress Outcome（L5）** —— 把 L5 从"test-only 不维护"修成"运行时由 done 章驱动"。

## 4. 验收（本 3D 交付自检）
- [x] 文档说明五种 Outcome 与各自判据（本节）。
- [x] `run_scheduler` 真实路径结束后，`progress.completed` 由 registry 的 done 章驱动（0→N）。
- [x] regression：`tests/regression/test_regression_p12_progress_advances.py` ——
      修复前红（runtime 从不写 `.completed`，registry 有 done 章但仍 0）、修复后绿；CI 自动执行。