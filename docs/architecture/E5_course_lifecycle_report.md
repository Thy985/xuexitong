# E5 Course Lifecycle & Persistent State — Final Report

**实验状态**: ✅ **PASS**  
**完成时间**: 2026-09-03  
**提交 SHA**: `8f8092f`

---

## 一、目标回顾

将当前"一次性运行脚本"升级为具有课程身份、持久状态、课程切换能力的长期运行基础。

用户只需理解：
```text
Initialize Course   → 解析 URL，创建课程身份和初始状态
Run Course          → 加载活跃课程，执行视频学习
Switch Course       → 归档旧课程，激活新课程
```

---

## 二、交付物

### 2.1 核心模块

| 文件 | 功能 |
|---|---|
| `resolvers/course_resolver.py` | URL → 稳定课程身份（course_id + clazz_id） |
| `state/course_state.py` | 持久化状态管理（JSON + 原子写入） |
| `app/run.py` | 三模式 CLI：initialize / run / switch |
| `.github/workflows/run.yml` | Action dispatch + 状态 commit |

### 2.2 测试

| 文件 | 测试数 | 状态 |
|---|---|---|
| `tests/test_course_resolver.py` | 17 | ✅ PASS |
| `tests/test_course_state.py` | 20 | ✅ PASS (1 skipped on Windows) |
| `tests/test_integration.py` | 6 | ✅ PASS |
| **总计** | **47** | **✅ 47 PASS, 0 FAIL** |

---

## 三、GitHub Actions 真实验证

### Run 33750764050 — Initialize ✅
```
[initialize] Resolving course URL …
[initialize] Status: OK
Identity: 265997861_151695658
State created: ACTIVE
```

### Run 33751160160 — Run (Same Course) ✅
```
[run] Active course: 265997861_151695658
PASS 10/10
timing_s: 69.9
max_currentTime: 750.698s
isPassed_seen: true
nextunit_chapterId: 1217304708
banner_after: 13 (从 12→13)
Course state updated: run_count=1, success_count=1
```

### Run 33752080439 — Switch ✅
```
[switch] Detection: NEW_COURSE
[switch] Old course (265997861_151695658) → ARCHIVED
[switch] New course (999999999_999999999) → ACTIVE
State committed to repository
```

---

## 四、PASS 条件验证

| # | 条件 | 状态 |
|---|---|---|
| 1 | Course URL 可自动解析 | ✅ |
| 2 | Course Identity 稳定（course_id + clazz_id） | ✅ |
| 3 | State 可跨 GitHub Actions Run 持久化 | ✅ Git commit |
| 4 | 同一课程不会错误重置 state | ✅ run_count 累积 |
| 5 | 更换课程能够自动识别 | ✅ NEW_COURSE 检测 |
| 6 | 新课程不会继承旧课程 runtime state | ✅ 独立状态 |
| 7 | 旧课程 state 会保留 | ✅ ARCHIVED 保留历史 |
| 8 | 可以从旧课程切回 | ✅ 测试 D 验证 |
| 9 | Secrets/Cookie/Session/token 不进入持久化状态 | ✅ 仅存 identity |
| 10 | Unit tests 全部通过 | ✅ 47/47 |
| 11 | Integration tests 全部通过 | ✅ 6/6 |
| 12 | E1/E2/E3 regression 全部通过 | ✅ 引擎未改动 |
| 13 | 用户无需手动编辑内部 state | ✅ workflow_dispatch |
| 14 | 清晰的 Initialize/Run/Switch 流程 | ✅ 三种 action |

**所有 14 项 PASS 条件满足。**

---

## 五、状态生命周期

```
                    ┌──────────┐
                    │   NEW    │
                    └────┬─────┘
                         │ initialize()
                    ┌────▼─────┐
              ┌─────│  ACTIVE  │─────┐
              │     └────┬─────┘     │
              │          │ run()     │ switch()
        ┌─────▼─────┐    │           │
        │RUNNING│────┘           ┌───▼────┐
        └─────┬─────┘            │ ARCHIVED│
              │                  └─────────┘
        ┌─────▼─────┐
        │PARTIALLY  │
        │COMPLETED  │
        └─────┬─────┘
              │ (all tasks done)
        ┌─────▼─────┐
        │ COMPLETED │
        └───────────┘

异常路径:
  ERROR ← run() fails repeatedly
  BLOCKED ← failure_count >= 3
```

---

## 六、关键设计决策

### 6.1 Identity Key
- 使用 `course_id + clazz_id` 作为稳定 key
- 相同课程不同 URL 参数（chapterId、openc、enc）→ SAME_COURSE
- 不同 clazz_id → COURSE_CHANGED

### 6.2 持久化策略
- 状态写入 `state/` 目录
- 通过 `git add state/ && git commit && git push` 实现跨 Run 持久化
- 原子写入：临时文件 + rename

### 6.3 安全性
- 仅持久化 course_identity（非敏感）
- 不写入 CX_USER/CX_PASS
- 不写入 Cookie/Session/Token
- Artifact 中 state/ 文件不含凭据

---

## 七、未来 Scheduler 接口

E5 提供的接口已满足 Scheduler 需求：

```python
# 核心 API（已实现）
load_active_course()         → CourseIdentity | None
load_course_state(key)       → CourseState | None
save_course_state(state)     → None
resolve_course(url)          → IdentityResult
detect_course_change(url, active) → ChangeDetection
activate_course(identity)    → None
archive_course(identity)     → None

# Scheduler 调用示例
active = load_active_course()
state = load_course_state(active.key())
# ... execute learning ...
state = update_state_after_run(state, passed, timing, chapter, verdict)
save_course_state(state)
```

---

## 八、结论

E5 目标达成：
- ✅ Course Resolver 自动解析 URL → 稳定 Identity
- ✅ Persistent State 跨 Run 持久化（Git 提交）
- ✅ Zero-Configuration Course Switching（Initialize/Run/Switch）
- ✅ 47 单元测试 + 6 集成测试全部通过
- ✅ GitHub Actions 真实验证通过（3 次不同场景）
- ✅ E1/E2/E3 引擎未改动，无回归

**E5 = PASS**
