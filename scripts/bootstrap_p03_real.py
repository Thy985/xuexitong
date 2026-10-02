"""P0-3 L3 真机验收运行器：对真实 .env 账号的某课程执行 account-first server bootstrap。

用法：
    python scripts/bootstrap_p03_real.py --course-url "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=...&courseId=...&clazzid=...&cpi=...&enc=...&mooc2=1"

副作用：带 CX_USER/CX_PASS 登录超星一次、打开真实课程页读 catalog，写入该账号命名空间
（state/accounts/<account>/...）的 registry 与 progress。可能触发验证码（XUE_CAPTCHA_MODE，
auto_then_manual 时如自动滑块失败会等待人工在可见浏览器里完成）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_credentials() -> dict:
    """优先 shell 环境，其次仓库 .env —— 得到 CX_USER / CX_PASS。"""
    values = dict(os.environ)
    try:
        from utils.env_file import load_env_file
        load_env_file(ROOT, values)   # env_var 优先，文件只填空
    except Exception as e:
        print(f"[env] load_env_file skipped: {e}", file=sys.stderr)
    return {"CX_USER": values.get("CX_USER"), "CX_PASS": values.get("CX_PASS")}


def main() -> int:
    ap = argparse.ArgumentParser(description="P0-3 L3 server bootstrap (real account)")
    ap.add_argument("--course-url", required=True,
                    help="完整 studentstudy URL（含 courseId/clazzid/cpi/enc 等）")
    ap.add_argument("--course-key", default="",
                    help="课程级 key（course_id_clazz）；缺省由 URL 推导")
    ap.add_argument("--force", action="store_true",
                    help="清空当前账号本课程的 registry 后按服务端真源重建"
                         "（账号对齐，issue #4 建议 1）。用于账本疑似污染/陈旧的显式恢复。")
    ap.add_argument("--out", default="",
                    help="证据 JSON 文件路径（如 docs/evidence/p03_<ts>.json）")
    args = ap.parse_args()

    creds = load_credentials()
    if not creds["CX_USER"] or not creds["CX_PASS"]:
        print("[bootstrap] CX_USER/CX_PASS 缺失（请配置 .env）", file=sys.stderr)
        return 2
    os.environ.setdefault("XUE_CAPTCHA_MODE", "auto_then_manual")

    course_key = args.course_key
    if not course_key:
        try:
            from resolvers.course_resolver import _parse_url_params
            p = _parse_url_params(args.course_url)
            course_key = f"{p.get('course_id')}_{p.get('clazz_id')}"
        except Exception:
            course_key = "unnamed"

    from app.registry.bootstrap import bootstrap_registry_from_server
    rep = bootstrap_registry_from_server(
        course_key, args.course_url,
        cx_user=creds["CX_USER"], cx_pass=creds["CX_PASS"],
        force=args.force)
    dump = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "course_url": args.course_url,
        "course_key": course_key,
        "force": args.force,
        "report": rep.to_dict(),
        "account_state_dir_exists": (ROOT / "state" / "accounts").exists(),
        "action": "bootstrap",
    }
    print(json.dumps(dump, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dump, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"[bootstrap] evidence -> {out}", file=sys.stderr)
    return 0 if rep.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())