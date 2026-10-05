# kv 代码风格

> 用途：给改 kv 代码的人与 AI。约定全部来自本仓既有实践；无 lint 配置（stdlib 零依赖约束），
> 违反靠 review 打回。

## 代码风格

- 每个 `.py` 文件第一行（docstring 之后）写 `from __future__ import annotations`——
  缺了 `str | None` 这类注解在旧解释器上会报错。
- **函数不超过 60 行**，超过就拆。
- 零外部依赖：stdlib only。确需引入第三方库，先讨论再动手。

## 命名与结构约定

- `kv/ops/` 一个命令一个文件；`kv/commands/` 里的函数只做"参数 → 调 ops → 输出"，
  业务逻辑不写在 commands 层。
- 测试文件 `tests/test_*.py`，类继承 `unittest.TestCase`；按模块对应命名（如 `test_cli_m*.py`）。

## 错误处理

- 自定义异常与退出码单源在 `kv/errors.py`：`EXIT_OK=0`、`EXIT_USAGE=1`、`EXIT_NOT_FOUND=2`、
  `EXIT_LEAKS=3`、`EXIT_BINDING=4`、`EXIT_GATE=5`。**不要硬编码数字退出码**。
- 所有 `open()` 必须带 `encoding=`——Windows 默认 cp936，不带编码在中文路径/内容上直接
  `UnicodeDecodeError`。
- 不裸 `except:`；捕获后要给出行下文的处理或带上下文重新抛出。

## 控制台与编码

- stdio 设置单源：`kv/console.py` 的 `setup_stdio()`；`kv.py` shim 负责强制 UTF-8。
  不要绕过 shim 直接 `python -m kv` 跑交互命令。

## 日志

无通用日志设施；溯源靠 `kv/core` 的 audit 链（append-only，见 `docs/ARCHITECTURE.md`）。
不要在业务代码里另建 print 日志通道。
