# snapshot-archive — E2 / E6 调查证据收敛样张

> 收敛自原先铺在仓库根目录的一批 **CI `mvp-evidence-<run>/` 整包快照**（每份含
> `home/runner/work/...` 一整份 repo 副本 + 数 MB 截图）。那些副本没有归档价值
> （是 run 的输入而非输出），原始整包已删除，仅保留**以下有意义的抽样**，作为考古证据链。

## 截图（`*.png`，来自各次 run 的 `tmp/diag_at_end_*.png`）
| 文件 | 来源 run（evidence 快照） | 说明 |
|---|---|---|
| `diag_at_end_33832905215.png` | `e6_check3/...33832905215` | 播放结束诊断截图 |
| `diag_at_end_33835144518.png` | `e6_check4|e6_check6/...33835144518` | 播放结束诊断截图 |
| `diag_at_end_33836081390.png` | `e6_check5`/`e6_c7b/...33836081390` | 播放结束诊断截图 |
| `diag_at_end_33837881087.png` | `e6_c7/...33837881087` | 播放结束诊断截图 |
| `diag_at_end_33878146.png` | `tmp_artifacts2` | 播放结束诊断截图 |
| `diag_at_end_33878155.png` | `tmp_artifacts3` | 播放结束诊断截图 |

> 注：多个快照（e6_c7/e6_c7b/e6_check3~6）的 PNG 是**相同字节的重复拷贝**（同一次 run 多次下载），
> 这里按 run 号去重后仅留 1 份；`tmp_artifacts2/3` run 号与我手抄有偏差，文件名以证据为准。

## `state-sample/` — 代表性运行时状态（取自 `evidence_dl/33873929856`）
| 文件 | 内容 | 脱敏 |
|---|---|---|
| `course_state.json` | course_state（含 history 24 条 run，PASS/FAIL、timing、chapter） | **`raw_url` 的 `enc=`/`openc=` 已 `<<redacted>>`** |
| `tasks.json` | 该 run 时的 task registry（canonical 快照） | 无令牌 |
| `execution_queue.json` | 执行队列快照 | 无令牌 |
| `tdvp_discovery.json` | TDVP 目录发现结果 | 无令牌 |

> 原始快照里的 `state/cookies.json` **未归档**（含会话 cookies，属敏感，见 .gitignore 纪律）。

## 为何删原目录 / 如何防复发
- 原 8 目录（`e6_c7 e6_c7b e6_check3~6 evidence_dl tmp_artifacts2/3 tmp_success2`）已被
  `git rm --cached` 并从磁盘删除；根 `.gitignore` 已补 `e6_*/`, `mvp-evidence-*/`, `tmp_artifacts*/`
  等防复发。
- 以后要留证据，**只手动把诊断 PNG / 抽样 JSON 放到本目录**，不要整包 `git add` run 产物。