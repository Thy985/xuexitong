# -*- coding: utf-8 -*-
"""绑定链路端到端验证（用户已授权）：app.run --video-index 2 章 1217304738。"""
import os
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))

from utils.env_file import load_env_file

env = dict(os.environ)
load_env_file(root, env)

out = root / "evidence" / "bind_e2e_4738_v3.json"

cmd = [
    str(root / ".venv" / "Scripts" / "python.exe"), "-m", "app.run",
    "--action", "run",
    "--course-url",
    "https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=1217304706"
    "&courseId=265997861&clazzid=151695658&cpi=506830460"
    "&enc=1bc1bd778f9e00d924fe97b3c63f76f4&mooc2=1&hidetype=0"
    "&openc=9b5661be6351e4d46bc29bfa2d69236a",
    "--chapter-id", "1217304738",
    "--video-index", "2",
    "--output", str(out),
    "--trigger", "manual",
]
print("RUN:", " ".join(cmd), flush=True)
proc = subprocess.run(cmd, cwd=root, env=env,
                      stdout=sys.stdout, stderr=sys.stderr)
print("EXIT:", proc.returncode, flush=True)
sys.exit(proc.returncode)
