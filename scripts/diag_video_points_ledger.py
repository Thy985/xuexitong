"""只读核对：被 E6.2 误降为 `other` 的章，服务端到底有没有视频点。

用法：
    PYTHONPATH=. python scripts/diag_video_points_ledger.py            # 只核对，不改账
    PYTHONPATH=. python scripts/diag_video_points_ledger.py --apply    # 按核对结论改回 video

为什么需要它：修 `head_chapter_id` 只能阻止继续损坏，已经写坏的历史账要逐章对真源。
真源 = 服务端该章的 job 点（`read_chapter_job_points`），一次登录、一个浏览器、逐章读。
本脚本不播放、不碰 multimedia/log、不构造任何上报参数。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (_REPO, _REPO / "resolvers", _REPO / "state", _REPO / "e2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from utils.stdio_utf8 import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

RESTORE = "restore_video"
KEEP = "keep_other"
UNKNOWN = "unknown"

RESTORE_COMPLETED = "restore_completed"
KEEP_QUEUED = "keep_queued"
NEEDS_EVIDENCE = "needs_evidence"
REPAIRABLE = ("UNKNOWN", "STALE")


def plan_restorations(other_cids: list, server: dict) -> list[dict]:
    """把"服务端读到什么"映射成"账本改不改"。

    `points_seen == 0` 代表这一章根本没测到点（探测失败/页面无 cards 帧），
    与"测到了点且点里没有视频"是两件事 —— 前者不动账，只有后者才确认 other。
    """
    rows = []
    for cid in other_cids or []:
        obs = server.get(cid)
        if not obs:
            rows.append({"chapter_id": cid, "action": UNKNOWN,
                         "video_total": None, "points_seen": None})
            continue
        total = int(obs.get("video_total") or 0)
        seen = int(obs.get("points_seen") or 0)
        if total > 0:
            action = RESTORE
        elif seen > 0:
            action = KEEP
        else:
            action = UNKNOWN
        rows.append({"chapter_id": cid, "action": action,
                     "video_total": total, "points_seen": seen})
    return rows


def pick_pre_demotion_state(versions: list) -> "dict|None":
    """从"新 → 旧"的历史版本里取最后一次仍是 video 时的状态。

    取不到（从没当过 video / 当时就没有 status）时返回 None —— 宁可不改，
    也不替这条记录编一个状态出来。
    """
    for v in versions or []:
        if (v or {}).get("task_type") == "video" and (v or {}).get("status"):
            return {"task_type": "video", "status": v["status"]}
    return None


def plan_point_repair(registry: dict, points_map: dict) -> list[dict]:
    """把被章级计数打成 UNKNOWN/STALE 的**已完成视频点**规划回 COMPLETED。

    要求两件事同时成立，缺一就不动账：
      * 点级快照证明该章视频点已全部 finished（`video_finished >= video_total > 0`）；
      * 该点自身有服务端确认（`point_is_server_verified`）。
    没有快照 = 证据不足（`needs_evidence`）—— 宁可少修，不能把 4708「双视频只播 1 个」
    那类章假修成已完成。BLOCKED 归熔断逻辑管，不在此翻案。
    """
    from app.registry.reconcile import point_is_server_verified

    rows = []
    for tid, rec in (registry or {}).items():
        tid = str(tid)
        if ":" in tid or (getattr(rec, "task_type", "") or "video") != "video":
            continue
        status = getattr(rec, "status", "")
        if status not in REPAIRABLE:
            continue
        snap = (points_map or {}).get(getattr(rec, "chapter_id", "")) or {}
        total = int(snap.get("video_total") or 0)
        fin = int(snap.get("video_finished") or 0)
        if not snap.get("has_video") or total <= 0:
            action = NEEDS_EVIDENCE
        elif fin < total:
            action = KEEP_QUEUED
        elif point_is_server_verified(rec):
            action = RESTORE_COMPLETED
        else:
            action = KEEP_QUEUED
        rows.append({"task_id": tid, "action": action, "from": status,
                     "video_total": total, "video_finished": fin})
    return rows


def history_versions(course_key: str, cids: list, max_commits: int = 40) -> dict:
    """{cid: [该记录在各历史提交里的 {task_type,status}，新→旧]}（只读 git）。"""
    import subprocess

    path = f"state/registry/{course_key}/tasks.json"
    log = subprocess.run(["git", "log", "--format=%H", "-n", str(max_commits), "--", path],
                         capture_output=True, text=True, encoding="utf-8")
    out: dict = {c: [] for c in cids}
    for sha in (log.stdout or "").split():
        show = subprocess.run(["git", "show", f"{sha}:{path}"],
                              capture_output=True, text=True, encoding="utf-8")
        if show.returncode != 0:
            continue
        try:
            data = json.loads(show.stdout)
        except Exception:
            continue
        recs = data.get("tasks", data)
        for cid in cids:
            r = recs.get(cid)
            if r:
                out[cid].append({"task_type": r.get("task_type"),
                                 "status": r.get("status")})
    return out


def demoted_video_chapters(registry: dict) -> list:
    """被记成 other 的"纯章号"记录 —— 带 `:other` 后缀的是 discovery 正常产物。"""
    return sorted(
        str(tid) for tid, rec in (registry or {}).items()
        if getattr(rec, "task_type", "") == "other" and ":" not in str(tid)
    )


def _active_course():
    key = json.loads((Path("state/active_course.json")).read_text(encoding="utf-8"))[
        "active_identity"]
    course = json.loads(Path(f"state/courses/{key}.json").read_text(encoding="utf-8"))
    return key, (course.get("course_identity") or {}).get("raw_url", "")


def observe_points(job_points) -> dict:
    """把一章的 job 点压成读数：`{video_total, video_finished, points_seen}`。

    `video_finished` 是点级修复的必需输入；`points_seen == 0` 表示**没读到**，
    与"读到了、确实没有视频"是两件事 —— 下游据此判 `needs_evidence` 而不是"无视频"。
    """
    from tvdp.tdvp import chapter_video_summary

    pts = job_points or []
    total, finished = chapter_video_summary(pts)
    return {"video_total": total, "video_finished": finished, "points_seen": len(pts)}


def read_server_points(cids: list, raw_url: str,
                       course_key: str = "") -> dict:
    """一个浏览器 + 一次登录，逐章读 job 点。失败章记 None（= 没测到）。

    带 `course_key` 时顺带把点级快照写进 registry 缓存（`points_seen>0` 才写 ——
    没读到不覆盖已有快照）。
    """
    from playwright.sync_api import sync_playwright
    from resolvers.course_resolver import _parse_url_params
    from tvdp.tdvp import _tdvp_course_params, read_chapter_job_points
    from app.e2_headed_gha import build_base_url
    from utils.browser_factory import launch_kwargs
    from utils.cookie_store import ensure_login

    params = _parse_url_params(raw_url)
    cp = _tdvp_course_params(params)
    course_id = params.get("course_id", "")
    clazz_id = params.get("clazz_id", "")
    cpi = params.get("cpi", "")
    user, pw = os.environ.get("CX_USER"), os.environ.get("CX_PASS")
    display = os.environ.get("DISPLAY", ":99")
    out: dict = {}
    with sync_playwright() as pwc:
        browser = pwc.chromium.launch(
            headless=False, **launch_kwargs(),
            args=[f"--display={display}", "--no-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        ensure_login(page, ctx, build_base_url(cids[0], cp), user, pw)
        for cid in cids:
            try:
                pts = read_chapter_job_points(page, cid, course_id, clazz_id, cpi) or []
                obs = observe_points(pts)
                out[cid] = obs
                if course_key and obs["points_seen"] > 0:
                    # 顺带预热点级快照：reconcile 的"细真源覆盖粗读数"规则要吃这个缓存
                    from app.registry.task_registry import set_chapter_point_snapshot
                    set_chapter_point_snapshot(
                        course_key, cid,
                        video_total=obs["video_total"],
                        video_finished=obs["video_finished"],
                        has_video=obs["video_total"] > 0)
            except Exception as e:
                print(f"[ledger] {cid} 读取失败: {type(e).__name__}: {e}",
                      file=sys.stderr)
                out[cid] = None
            print(f"[ledger] {cid} -> {out[cid]}", flush=True)
        browser.close()
    return out


def _collect_server(args, cids: list, raw_url: str, course_key: str) -> dict:
    """真源读数：优先复用已存档 JSON，否则开一次浏览器逐章读（顺带预热快照）。"""
    if args.server_json:
        server = json.loads(
            Path(args.server_json).read_text(encoding="utf-8"))["server"]
        print(f"[ledger] 复用已存档的真源核对 {args.server_json}（不开浏览器）",
              flush=True)
        return server
    return read_server_points(cids, raw_url, course_key)


def _write_evidence(course_key: str, t0: float, server: dict, rows: list) -> str:
    Path("evidence").mkdir(exist_ok=True)
    path = Path(f"evidence/ledger_video_points_{time.strftime('%Y%m%d_%H%M%S')}.json")
    path.write_text(json.dumps(
        {"course_key": course_key, "elapsed_s": round(time.time() - t0, 1),
         "server": server, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return str(path)


def repairable_chapters(registry: dict) -> list:
    """状态被章级计数打成 UNKNOWN/STALE 的纯章号视频记录所在的章。"""
    return sorted({
        getattr(r, "chapter_id", "") for tid, r in (registry or {}).items()
        if ":" not in str(tid)
        and (getattr(r, "task_type", "") or "video") == "video"
        and getattr(r, "status", "") in REPAIRABLE
        and getattr(r, "chapter_id", "")
    })


def _repair_points(args, course_key: str, raw_url: str, registry: dict) -> int:
    """点级修复：把"视频点确实全部 finished"的记录恢复成 COMPLETED。"""
    from app.registry.task_registry import load_chapter_points, save_registry

    cands = repairable_chapters(registry)
    print(f"[ledger] 点级修复候选章 = {len(cands)}: {cands}", flush=True)
    if not cands:
        return 0

    t0 = time.time()
    server = _collect_server(args, cands, raw_url, course_key)
    rows = plan_point_repair(registry, load_chapter_points(course_key))
    by = {}
    for r in rows:
        by.setdefault(r["action"], []).append(r["task_id"])
    for action in (RESTORE_COMPLETED, KEEP_QUEUED, NEEDS_EVIDENCE):
        print(f"[ledger] {action}: {len(by.get(action, []))} {by.get(action, [])}",
              flush=True)
    print(f"[ledger] 证据: {_write_evidence(course_key, t0, server, rows)}", flush=True)

    if not args.apply:
        print("[ledger] 演练：未改账。加 --apply 才写回。", flush=True)
        return 0
    changed = 0
    for r in rows:
        if r["action"] != RESTORE_COMPLETED:
            continue
        rec = registry.get(r["task_id"])
        if rec is not None:
            rec.status = "COMPLETED"
            changed += 1
    save_registry(course_key, registry)
    print(f"[ledger] 已恢复 COMPLETED: {changed} 条；"
          f"keep_queued/needs_evidence 未动。", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="按核对结论写回账本（两种模式共用）")
    ap.add_argument("--server-json", default="",
                    help="复用先前核对产出的 evidence JSON，避免再开浏览器打真站")
    ap.add_argument("--repair-points", action="store_true",
                    help="点级修复模式：把被章级计数打成 UNKNOWN/STALE 的已完成视频点"
                         "恢复为 COMPLETED（需点级快照 + 点级服务端确认双证据）")
    args = ap.parse_args()

    from app.registry.task_registry import load_registry, save_registry

    # 在 main() 里加载而不是模块级：被 import 时改 os.environ 会外溢到别处
    # （本项目已经为此踩过一次）。
    from utils.env_file import load_env_file
    load_env_file(_REPO)

    course_key, raw_url = _active_course()
    registry = load_registry(course_key)

    if not (args.server_json or
            (os.environ.get("CX_USER") and os.environ.get("CX_PASS"))):
        missing = [k for k in ("CX_USER", "CX_PASS") if not os.environ.get(k)]
        print(f"[ledger] 缺少 {missing}，无法读真源", file=sys.stderr)
        return 2

    if args.repair_points:
        return _repair_points(args, course_key, raw_url, registry)

    cids = demoted_video_chapters(registry)
    print(f"[ledger] course={course_key} 待核对的 other 章 = {len(cids)}: {cids}",
          flush=True)
    if not cids:
        return 0

    t0 = time.time()
    server = _collect_server(args, cids, raw_url, course_key)
    rows = plan_restorations(cids, server)
    by_action = {a: [r["chapter_id"] for r in rows if r["action"] == a]
                 for a in (RESTORE, KEEP, UNKNOWN)}
    for a, ids in by_action.items():
        print(f"[ledger] {a}: {len(ids)} {ids}", flush=True)
    print(f"[ledger] 证据: {_write_evidence(course_key, t0, server, rows)}", flush=True)

    restore_cids = [r["chapter_id"] for r in rows if r["action"] == RESTORE]
    hist = history_versions(course_key, restore_cids) if restore_cids else {}
    for r in rows:
        r["pre_demotion"] = pick_pre_demotion_state(hist.get(r["chapter_id"]))
        print(f"[ledger] {r['chapter_id']} video_total={r['video_total']} "
              f"降级前={r['pre_demotion']}", flush=True)

    if not args.apply:
        print("[ledger] 演练：未改账。加 --apply 才写回。", flush=True)
        return 0

    changed = skipped = 0
    for r in rows:
        if r["action"] != RESTORE:
            continue
        rec = registry.get(r["chapter_id"])
        if rec is None or getattr(rec, "task_type", "") != "other":
            continue
        rec.task_type = "video"
        pre = r["pre_demotion"]
        if pre:
            rec.status = pre["status"]
        else:
            skipped += 1
            print(f"[ledger] ⚠ {r['chapter_id']} 历史里找不到 video 版本，"
                  f"只恢复 task_type，status 保持 {rec.status}", flush=True)
        changed += 1
    save_registry(course_key, registry)
    print(f"[ledger] 已恢复 video: {changed} 章（其中 {skipped} 章 status 未恢复）；"
          f"keep_other/unknown 未动。", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
