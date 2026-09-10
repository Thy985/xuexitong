# tests/fixtures/ — 测试夹具（真实快照 + 脱敏数据）

> 本目录存放**真实或忠实模拟的运行时快照**，供 unit/integration/regression 复现。
> 原则：fixture 一律**脱敏**（红线：不得含会话 token / 真实账号 / 个人时序），
> 但**保留与解析器匹配的必要 DOM 结构**，否则测不出真实漂移。

## 布局

```
dom/   真实 DOM snapshot（课程目录树 / 登录页 / 「用户未登录」页）
state/ 课程/registry 状态快照（canonical tasks.json 结构）
net/   网络/证据快照（登录+渲染结果）
```

## 现有文件

| 文件 | 内容 | 用途 |
|---|---|---|
| `dom/chaoxing_course_catalog.html` | mooc2 课程目录树（占位 id），含 已完成/待完成/激活 节点、`#coursetree`/`.posCatalog_*`/`.icon_Completed`/文本 `已完成` | **P0-03 DOM 漂移回归**：TDVP 目录树解析 |
| `dom/chaoxing_login_required.html` | 真实「用户未登录」页（公开错误页） | 登录态判定回归（`_is_login_warning`） |
| `state/registry_tasks_sample.json` | canonical `tasks.json` 结构（占位 id/时间戳），含 COMPLETED/VERIFYING/DISCOVERED 三种状态 | reconcile / 状态机 / 进度会计回归输入 |
| `net/probe_mooc2_ok.json` | mooc2 真实 E2E 的 probe 结果（`enc/t` 已红act） | 记录「正确入口真实登录可达」基线证据 |

## 如何新增
1. 只收录**真实录制 → 结构保留 + 敏感置换**的快照，不放数据库导演。
2. 新 fixture 在对应 `regression/` 用例里显式 `pytest` `read_text`/`json.load` 消费。
3. 属敏感（含 token/账号）的内容绝不进入 fixtures（放 `docs/evidence/` 或不入库）。