# docs/evidence/mooc2_evidence/ — 真实 mooc2 只读 E2E 证据（原始抓取）

> 本目录记录 2026-09-10 在正确入口（mooc2 `/mooc2-ans/mycourse/stu?...enc/t...`）
> 的一次**真实只读 E2E**：登录成功 + 课程目录读取。这是「本地 Playwright 能力」的
> 事实证据（另见 `docs/runbooks/LOCAL_CAPABILITY_MATRIX.md`）。

## 硬件（不入库，原因见 .gitignore）
| 文件 | 体积 | 为什么不入 | 替代（脱敏/裁剪后已入 tests/fixtures） |
|---|---|---|---|
| `page.html` | ≈10.6MB | 含当次会话 `enc/t` token；过大 | `tests/fixtures/dom/`（裁剪 catalog） |
| `page.png`   | ≈77KB | 截图（含会话渲染） | — |
| `probe.json` | 790B  | 含当次 `enc` token | `tests/fixtures/net/probe_mooc2_ok.json`（enc 已红act）|

## 只有当次产物（含 enc）才需要重新录制
`enc/t` 是**动态**令牌：每次会话取当次值，写死即失效。这也是**必须把真 DOM
裁剪、脱敏后落到 tests/fixtures**的原因 —— fixture 是稳定可复现的，enc 是易失效不可提交的。

## 何时必须重新录制
- 超星改目录 DOM 结构 / 完成标记（`.icon_Completed` / `已完成` / `jobUnfinishCount`）时；
- 当某个 regression 用到的 fixture 与真实页面不再匹配（库的一端漂移）。

## 脱敏原则
把 `enc`、`t`、Cookie、账号、真实时间戳换成占位符；只保留解析器真正消费的
结构片段（`#coursetree`、`.posCatalog_*`、`.icon_Completed`、完成标记文本等）。