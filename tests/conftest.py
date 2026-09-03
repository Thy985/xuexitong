"""pytest configuration for xuexitong tests."""
import sys
from pathlib import Path

# 确保模块可导入
REPO_ROOT = Path(__file__).parent.parent
for p in [REPO_ROOT / "resolvers", REPO_ROOT / "state", REPO_ROOT / "e2"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
