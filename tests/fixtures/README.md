# tests/fixtures/ — 测试夹具（真实快照 + 脱敏）

> 原则：**lixture 一律来自真实运行后脱敏/裁剪**，禁止手写「看起来像真的 DOM」
> （否则回潮「fixture 对、真站结构不同」）。每条 fixture 记真源与何时重录。

## 布局

```
dom/   真实 DOM 快照（#coursetree 学习页 / .catalog_* 目录页 / 登录页 / 未登录页）
state/ registry/tasks.json 内部 schema 快照（canonical 结构）
net/   网络/证据快照（登录+渲染确定结果）
_raw_capture/  真实抓取原始物料（**不入库**, gitignore）；只保留最小脱敏产物在 dom/
```

## 现有文件（真源）

| 文件 | 真源 | 用途 |
|---|---|---|
| `dom/chaoxing_course_catalog.html` | **真实** mooc1 `studentstudy` 学习页 `#coursetree`（来自 `scripts/capture_fixtures.py` → `_raw_capture/dom_learning_tree.html`，`posCatalog_select/.posCatalog_name/.posCatalog_sbar/.icon_Completed/.jobUnfinishCount/.catalog_points_yi`）| **P0-03 DOM 漂移回归**（TDVD 目录树结构锚定）|
| `dom/studentcourse_catalog.html` | **真实** mooc2 `studentcourse` 目录页 `.catalog_*` 语法（同样来自 `_raw_capture/dom_catalog_list.html`）| 目录/任务点状态锚（已完成 `icon_yiwanc` / 待完成 `knowledgeJobCount`/`catalog_points_yi`）|
| `dom/chaoxing_login_required.html` | **真实**「用户未登录」公开错误页 | 登录态判定回归（`_is_login_warning`）|
| `state/registry_tasks_sample.json` | canonical `tasks.json` **内部 schema**（人工材料化 shape，非 DOM；非“真站快照”类）| reconcile/状态机/进度会计回归输入 |
| `net/probe_mooc2_ok.json` | **真实** mooc2 只读 E2E probe 结果（`enc/t` 已红act）| 「正确入口真实登录可达」基线证据 |
| `net/xhr_student_course_catalog.json` | **真实** mooc2 `studentcourse` 目录页 **XHR 快照**（GET 200 text/html，来自 `_raw_capture/xhr_00.json`，body 截到目录片段）| 网络层喂给目录解析的服务端语法锚 |

## 关于 `multimedia_log` / `next_unit` 的 XHR 快照（诚实说明）
项目想保留真实 XHR 快照，但 `multimedia/log` POST 与 `next_unit` 的**响应体**目前**没有**被快照：
- 它们只在**真实播放视频（写行为）**时产生，会向超星登记一个观看/完成点，与「只读探测、不污染
  服务器」的纪律冲突 —— 所以脚本只读抓目录与学习页，**不主动触发播放**。
- 仓库里只有 E3 判定的**字符串**（`"multimedia_log": "PASS"`），没有可回放的 JSON body。
- 因此本表只落地真实、无害、可回放的目录页 XHR；`multimedia_log`/`next_unit` 的 body **明确未捕获
  （需写行为，按策略不办）**，不伪造。

## 重要：两种页面 DOM 不同（都真存在）
- **学习页**（点击章节后进入，`mooc1...studentstudy?chapterId=..`）→ 目录树是
  `#coursetree > ul > li > .posCatalog_select`（`posCatalog_name/posCatalog_sbar/
  icon_Completed/posCatalog_active`）。`tvdp.extract_catalog_from_page` 读它。
- **目录页**（mooc2 `/mycourse/studentcourse`，顶部 tab「章节」）→ 是
  `.catalog_unit > .chapter_item > .catalog_title`（`catalog_num/catalog_sbar/
  catalog_name.newCatalog_name` + `catalog_state.icon_yiwanc`/`catalog_task`.
  `knowledgeJobCount`/`catalog_points_kq`）。**没有** `#coursetree`。
- 嵌 `parse_task_status_from_page` 的**裸编号文本启发式**不能读真实 DOM（编号与标题间有 `</em>`），
  仅留在 legacy 单元里；不要在回归里拿它断言真实 DOM。

## 何时必须重新录制（真源）
- 超星改 `posCatalog_*`/`catalog_*` class / 完成标记文本（`已完成`/`N个待完成任务点`）时；
- 某回归用到的 fixture 与真实页面结构不再匹配（真源“漂移”）时。

## 如何更新（重录流程）
```
python scripts/capture_fixtures.py      # 登录+读目录（只读）→ tests/fixtures/_raw_capture/
# 从 _raw_capture/dom_learning_tree.html（#coursetree）与 dom_catalog_list.html（.catalog_*）
# 手工剪裁到 最小 + 脱敏（去掉 enc/utEnc/setlog/账号/时序），替换本表对应 dom/ 文件。
```
- 红线：**不提交** `_raw_capture/`（含会话 token、10MB 页）；只提交脱敏裁剪产物。

## 何时更新
- 触碰 DOM 解析/新 selector/完成判定 → 先更新真实 fixture 再修代码，让回归先红。
- 只做内部数据结构（registry/state schema）改动 → 真源为代码自身，无需重录。