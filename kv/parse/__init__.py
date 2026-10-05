"""parse —— 文本 → 候选。

本包只抽**信号**（值、键名、主机名），**永不给平台命名**。判定是 detect 的事。
这条边界由 tests/test_arch.py 强制：parse 不得 import detect。

每个解析器都是纯函数 `parse_x(text) -> Iterator[Candidate]`。pipeline 按固定顺序
跑它们再按 (sha256, key_name) 去重 —— 顺序有讲究：结构化解析器必须在 bare 之前，
否则一条 curl 命令会变成一串垃圾「裸 key」。
"""
