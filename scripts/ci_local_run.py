#!/usr/bin/env python3
"""xuexitong — M0 本地权威运行脚本（L2 验收入口）

用途（REQUIREMENTS R-01 / ACCEPTANCE L2）：
    以与 GHA 完全一致的引擎，在本地跑一次 scheduler，产出可复现的 evidence，
    供 M0 验收：verdict==PASS、连续 N 次稳定、失败能归因 failure_stage。

设计约束：
    - 不重写引擎：scheduler 直接复用 `scheduler.run_scheduler`（与 app/run.py --action
      scheduler 同一条路径）；run/initialize/switch 通过子进程转发给 app/run.py，
      保证「本地行、云就行」（R-20 上云将复用同一引擎，evidence 结构可 diff）。
    - 不改 state/ 语义，persistence 仍走 course_state / registry（git 跨 Run 保留）。
    - 合规不变：不构造/伪造/重放 multimedia/log，不修改 enc/playingTime/_t。

用法：
    # 单次 scheduler（自动从 state/active_course.json 读取课程）
    python scripts/ci_local_run.py --trigger manual --max-chapters 2

    # 连续跑 3 次，验证 M0「PASS 可复现、不重复、不卡死」
    python scripts/ci_local_run.py --repeat 3

    # 失败时把截图 + registry + evidence 打包 zip（R-03）
    python scripts/ci_local_run.py --collect-diagnostics
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO, _REPO / "resolvers", _REPO / "state", _REPO / "e2"):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

from utils.env_file import load_env_file  # noqa: E402


def ensure_credentials(root: Path, env: dict) -> list:
    """凭据前置检查：先补本地 `.env`（真实环境变量优先），返回仍缺失的键。

    原先只查 env 不读 .env，与 LOCAL_FIRST_SETUP.md「凭据写 .env」矛盾 →
    本地带 .env 也直接 exit 2，M0 入口跑不起来。
    """
    load_env_file(root, env)
    return [k for k in ("CX_USER", "CX_PASS") if not env.get(k)]


# ────────────────────────────────────────────────
# 验收口径
# ────────────────────────────────────────────────
def counts_as_pass(summary: dict) -> bool:
    """验收口径：只有真 PASS，或"确实无事可做"的干净 NOOP，才算通过。

    scheduler 已诚实交回 `passed=False` 时（如 TDVP 目录探查空导致没选到章），
    不得被 `decision==NOOP` 洗成绿 —— ACCEPTANCE「凡看起来能跑都不算通过」要拦的正是这个。
    判定原先散在 main() 两处且互不一致，现收敛于此。
    """
    if summary.get("failure_stage"):
        return False
    if summary.get("passed") is False:
        return False
    verdict = summary.get("verdict")
    if verdict == "PASS":
        return True
    return verdict in (None, "") and summary.get("decision") == "NOOP"


def _annotate_probe_empty(summary: dict) -> dict:
    """当 scheduler 返回"探针为空/没有任务"时，区分『真无任务』与『浏览器缺失』。

    若 NOOP 且 verdict 含 probe empty / No pending，但浏览器可执行文件缺失，
    则是**环境未就绪**被误报为业务无任务 —— 应明确打上 BROWSER_MISSING，
    而不是让用户误以为课程真的没任务。
    """
    verdict = str(summary.get("verdict") or "")
    decision = str(summary.get("decision") or "")
    low = verdict.lower()
    probeish = decision == "NOOP" and (
        "empty" in low or "probe" in low or "no pending" in low
    )
    if not probeish:
        return summary
    # 检测浏览器缺失
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path  # 不 launch，仅取可执行路径
            import os as _os
            if path and not _os.path.exists(path):
                summary["failure_stage"] = "BROWSER_MISSING"
                summary["error"] = (
                    f"Playwright 浏览器未安装（期望: {path}）。"
                    "请执行 `playwright install chromium` 后重跑。"
                )
    except Exception:
        pass
    return summary


# ────────────────────────────────────────────────
# 单一引擎：scheduler 直接 import；run/init/switch 走子进程
# ────────────────────────────────────────────────
def _run_scheduler(args, run_id: str) -> dict:
    from scheduler import run_scheduler

    try:
        result = run_scheduler(
            args.course_url or "",
            args.chapter_id or "",
            args.trigger,
            run_id,
            max_chapters=args.max_chapters,
        )
    except Exception as e:  # 调度器自身异常也如实归因，不静默
        return {
            "decision": "ERROR",
            "result": "FAILED",
            "course_key": None,
            "verdict": "FAIL",
            "passed_count": 0,
            "failure_stage": "SCHEDULER_CRASH",
            "timing_s": 0.0,
            "error": f"{type(e).__name__}: {e}",
            "run_id": run_id,
        }
    summary = {
        "decision": getattr(result, "decision", None),
        "result": getattr(result, "result", None),
        "course_key": getattr(result, "course_key", None),
        "verdict": getattr(result, "verdict", None),
        "passed": getattr(result, "passed", None),
        "passed_count": (getattr(result, "evidence", None) or {}).get("passed_count"),
        "failure_stage": getattr(result, "failure_stage", None),
        "timing_s": getattr(result, "timing_s", None),
        "error": getattr(result, "error", None),
        "run_id": run_id,
    }
    # 把"探针为空"分清楚是『真无任务』还是『浏览器缺失』，避免环境故障伪装成业务空
    return _annotate_probe_empty(summary)


def _forward_to_app_run(args, run_id: str) -> dict:
    """run / initialize / switch：转发给 app/run.py 新进程（保持单一真实引擎）。"""
    cmd = [sys.executable, str(_REPO / "app" / "run.py")]
    cmd += ["--action", args.action]
    if args.course_url:
        cmd += ["--course-url", args.course_url]
    if args.chapter_id:
        cmd += ["--chapter-id", args.chapter_id]
    if args.action == "run":
        cmd += ["--max-chapters", str(args.max_chapters)]
    out = (_REPO / args.output.replace("<ts>", str(int(time.time())))).resolve()
    cmd += ["--output", out]
    cmd += ["--run-id", run_id]
    print(f"[ci_local] 转发 app/run.py: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=os.environ.copy(), cwd=str(_REPO))
    # 尝试从 evidence 读回小结，失败则用退出码兜底
    summary = {"run_id": run_id, "exit_code": proc.returncode}
    try:
        p = Path(out)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            res = data.get("result") or {}
            summary["verdict"] = res.get("verdict")
            summary["passed_count"] = res.get("passed_count")
            summary["failure_stage"] = res.get("failure_stage")
            summary["course_key"] = res.get("course_key")
    except Exception:
        pass
    return summary


# ────────────────────────────────────────────────
# 诊断打包（R-03）
# ────────────────────────────────────────────────
def _collect_diagnostics(workdir: Path, run_summary: dict) -> Path | None:
    """有失败时，收集 diag_*.png + scheduler evidence + 状态文件，打成 zip。

    最低保障：run_summary（内含 failure_stage / error）始终写入失败摘要 JSON，
    保证即使无截图/无 state 也产出一个可定位失败原因的 zip（R-03）。
    """
    wp = workdir
    wp.mkdir(parents=True, exist_ok=True)
    collected = []
    # 0) 失败摘要（必写，保证 zip 至少能说明失败原因）
    summary_file = wp / "failure_summary.json"
    summary_file.write_text(
        json.dumps(run_summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    collected.append(summary_file)
    # 1) 截图（引擎失败时通常写到 /tmp / 运行目录 / 仓库根）
    for base in (Path("/tmp"), Path(os.getcwd()), _REPO):
        try:
            for png in base.glob("diag_*.png"):
                dst = wp / png.name
                if not dst.exists():
                    shutil.copy(png, dst)
                    collected.append(dst)
        except OSError:
            continue
    # 2（关键状态文件（基于仓库根，避免 cwd 依赖）
    state_root = _REPO / "state"
    if state_root.exists():
        for sf in state_root.rglob("*.json"):
            rel = sf.relative_to(state_root)
            dst = wp / "state" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy(sf, dst)
                collected.append(dst)
            except OSError:
                pass
    zip_name = wp / f"diag_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in collected:
            if f.resolve() == zip_name.resolve():
                continue
            # 用相对 zip 根(workdir)的路径做 arcname，保留目录结构，避免不同目录同名文件撞车
            try:
                arcname = f.relative_to(wp)
            except ValueError:
                arcname = f.name
            zf.write(f, str(arcname))
    print(f"[ci_local] 诊断包已生成: {zip_name}", flush=True)
    return zip_name


# ────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────
def _append_record(record_path: Path, row: dict) -> None:
    """把单次结果追加到 evidence/log，保留完整历史（验收留痕）。"""
    record_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if record_path.exists():
        try:
            rows = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            rows = []
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    record_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="xuexitong 本地权威运行入口 (M0 / ACCEPTANCE L2)")
    ap.add_argument("--action", choices=["scheduler", "run", "initialize", "switch"],
                    default="scheduler")
    ap.add_argument("--course-url", default="")
    ap.add_argument("--chapter-id", default="")
    ap.add_argument("--max-chapters", type=int, default=1,
                    help="scheduler 一次最多推进的视频任务点数（默认 1）")
    ap.add_argument("--trigger", choices=["manual", "schedule"], default="manual",
                    help="默认 manual=不受 BLOCKED cooldown 限制（L2 验收用）")
    ap.add_argument("--repeat", type=int, default=1,
                    help="连续运行次数（M0 稳定性建议 3）")
    ap.add_argument("--output", default="./evidence/run_<ts>.json")
    ap.add_argument("--collect-diagnostics", action="store_true",
                    help="如有失败，截图+状态打成 zip")
    args = ap.parse_args()

    # credentials 校验（run/scheduler 需要）
    if args.action in ("run", "scheduler"):
        missing = ensure_credentials(_REPO, os.environ)
        if missing:
            print(f"[ci_local] 缺少凭据: {missing}（export 或写入本地 .env，勿入仓库）",
                  flush=True)
            return 2

    all_summaries = []
    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n═══ ci_local 第 {i+1}/{args.repeat} 次 ═══", flush=True)
        run_id = f"local-{int(time.time())}"
        summary = (_run_scheduler(args, run_id) if args.action == "scheduler"
                   else _forward_to_app_run(args, run_id))

        # 留痕
        log_dir = _REPO / "evidence" / "_logs"
        _append_record(log_dir / "ci_local_runs.jsonl", summary)

        # 诊断打包：凡不满足验收口径者都算失败（含"表面 NOOP 实为探针空"）
        if args.collect_diagnostics and not counts_as_pass(summary):
            _collect_diagnostics(_REPO / "evidence" / "diag", summary)
        all_summaries.append(summary)

    # 汇总
    print("\n========== ci_local 汇总 ==========", flush=True)
    ok = True
    for s in all_summaries:
        line = {"run_id": s.get("run_id"), "action": args.action,
                "verdict": s.get("verdict"), "passed_count": s.get("passed_count"),
                "failure_stage": s.get("failure_stage"), "error": bool(s.get("error"))}
        print(f"  {json.dumps(line, ensure_ascii=False)}", flush=True)
        if not counts_as_pass(s):
            ok = False
    print(f"[ci_local] {len(all_summaries)} 次完成: "
          f"{'全部通过' if ok else '存在失败 → 见 evidence/_logs / diag包'}.", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())