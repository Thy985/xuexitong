# tests/ — 测试布局

```
tests/
├── unit/          # 纯函数、数据结构、parser、状态机（state transition）
├── integration/   # 模块间协作：scheduler+registry、persistence、queue
├── regression/    # （规划中）历史真实事故 / 真实 run 号锚定复现测试
└── fixtures/      # （规划中）真实 DOM snapshot / HTML / JSON / state 快照 / 历史运行数据
```

约定：
- 每个子目录含 `__init__.py`，`tests/` 仍作为 pytest 包（`tests/conftest.py` 负责把
  `resolvers` / `state` / `e2` 加入 `sys.path`，保证扁平+package import 都能工作）。
- 运行：在仓库根 `python -m pytest tests/`。
- 现有约展开/变更已保持语义不变；回归与普通 unit 不混目录。