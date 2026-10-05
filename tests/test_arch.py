"""架构不变量。

这些断言编码的是设计本身依赖的性质，不是风格偏好。每一条都对应一个具体的
失败模式 —— 大多是现有工程踩过的坑：

  * 导入方向单向     加一个平台不该需要碰存储代码
  * SQL 只在 repo.py 数据访问面才能被一次阅读审完
  * 不 shell 出去做正则匹配   (?i) 内联进 git grep -E 会静默失效，
                              大小写不敏感规则全部作废而扫描器仍报「全仓干净」
  * ctypes 只在 crypto/  FFI 崩的是整个进程（退出码 127、无输出），必须集中
  * open() 必带 encoding  本机控制台 CP 是 936，靠默认编码读写中文必乱码
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

import tests  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
KV_DIR = ROOT / "kv"
TESTS_DIR = ROOT / "tests"

SQL_ALLOWED = {"kv.core.repo"}
PRAGMA_ALLOWED = {"kv.core.repo", "kv.core.db"}

IMPORT_RULES = (
    ("kv.parse", "kv.detect", "parse 只抽信号，永不给平台命名"),
    ("kv.detect", "kv.core.repo", "用户纠正规则是注入进来的，不是查库查出来的"),
    ("kv.crypto", "kv.core", "依赖方向是 core -> crypto，反过来会成环"),
    ("kv.core", "kv.ops", "core 是最底层，不该知道编排层的存在"),
    ("kv.core", "kv.parse", "core 不该知道解析器的存在"),
    ("kv.core", "kv.detect", "core 不该知道检测器的存在"),
    ("kv.core", "kv.capture", "core 不该知道剪切板的存在"),
    ("kv.parse", "kv.core.repo", "解析器是纯函数，不碰数据库"),
    ("kv.parse", "kv.core.vault", "解析器是纯函数，不碰数据库"),
    ("kv.detect", "kv.core.vault", "检测器是纯函数，不碰数据库"),
)

DML_HINT = re.compile(
    r"(?i)\b(SELECT\s+.+\s+FROM|INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE\s+\w+\s+SET"
    r"|DELETE\s+FROM|CREATE\s+(TABLE|INDEX|TRIGGER|VIEW))\b"
)
PRAGMA_HINT = re.compile(r"(?i)\bPRAGMA\s")

OPENERS = {"open", "read_text", "write_text", "read_bytes", "write_bytes"}
BYTES_OPENERS = {"read_bytes", "write_bytes"}


def python_files(directory: Path):
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_modules(tree: ast.AST) -> set[str]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.add(node.module)
                found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def starts_with(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


class TestImportDirection(unittest.TestCase):
    def setUp(self):
        self.modules = {p: module_name(p) for p in python_files(KV_DIR)}
        self.imports = {
            self.modules[p]: imported_modules(ast.parse(p.read_text(encoding="utf-8"), str(p)))
            for p in self.modules
        }

    def test_declared_rules_hold(self):
        for source, target, reason in IMPORT_RULES:
            for module, deps in self.imports.items():
                if not starts_with(module, source):
                    continue
                for dep in deps:
                    if starts_with(dep, target):
                        self.fail(f"{module} 不得 import {dep}（{source} ↛ {target}）：{reason}")

    def test_nothing_imports_gui(self):
        for module, deps in self.imports.items():
            if starts_with(module, "kv.gui"):
                continue
            for dep in deps:
                if starts_with(dep, "kv.gui"):
                    self.fail(f"{module} 不得 import {dep}：gui 是叶子，没人依赖它")

    def test_nothing_imports_cli(self):
        """cli 是入口，被 kv.py / __main__.py 调用，但 kv/ 内部没人依赖它。
        否则命令实现会反过来 import 解析器，参数树就没法独立测了。"""
        for module, deps in self.imports.items():
            if module == "kv.__main__":
                continue
            for dep in deps:
                if dep == "kv.cli" or starts_with(dep, "kv.cli."):
                    self.fail(f"{module} 不得 import {dep}")

    def test_commands_do_not_import_parser(self):
        for module, deps in self.imports.items():
            if not starts_with(module, "kv.commands"):
                continue
            self.assertNotIn("argparse", deps, f"{module} 不该碰 argparse，那是 cli.py 的事")

    def test_no_relative_imports_inside_kv(self):
        """相对导入会绕过上面的前缀检查，也让 python -I 下的路径解析变脆。"""
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    self.fail(f"{module_name(path)} 用了相对导入（level={node.level}）")


class TestSqlIsolation(unittest.TestCase):
    def offenders(self, pattern, allowed):
        found = []
        for path in python_files(KV_DIR):
            module = module_name(path)
            if module in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if pattern.search(node.value):
                        found.append(f"{module}:{node.lineno}")
        return found

    def test_dml_only_in_repo(self):
        """整个数据访问面要能被一次阅读审完，所以读写行的 SQL 只在一个文件里。"""
        self.assertEqual(self.offenders(DML_HINT, SQL_ALLOWED), [])

    def test_pragma_only_in_db_and_repo(self):
        """PRAGMA 是连接配置不是数据访问，db.py 需要它 —— 但也只许这两个文件。"""
        self.assertEqual(self.offenders(PRAGMA_HINT, PRAGMA_ALLOWED), [])

    def test_schema_file_exists(self):
        self.assertTrue((KV_DIR / "core" / "schema.sql").exists())

    def test_rules_are_not_vacuous(self):
        """反向确认：两个模式都真的能在它们该在的文件里命中。"""
        repo_text = (KV_DIR / "core" / "repo.py").read_text(encoding="utf-8")
        db_text = (KV_DIR / "core" / "db.py").read_text(encoding="utf-8")
        self.assertTrue(DML_HINT.search(repo_text))
        self.assertTrue(PRAGMA_HINT.search(db_text))
        self.assertFalse(DML_HINT.search(db_text), "db.py 里不该有 DML")


class TestNoShellRegex(unittest.TestCase):
    def code_strings(self, path: Path) -> list[str]:
        """只取**代码里的**字符串常量，跳过文档字符串。

        用原始文本会误报：rules.py 的模块文档字符串里写着「曾经把 (?i) 内联进
        git grep -E」—— 那是一条告诫，不是一次调用。
        """
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        return [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_git_grep_literal(self):
        """现有工程踩过：(?i) 内联进 git grep -E，git 不支持该语法，
        大小写不敏感规则全部静默失效，而扫描器仍然报「全仓干净」。"""
        offenders = []
        for path in python_files(KV_DIR):
            for text in self.code_strings(path):
                if "git grep" in text:
                    offenders.append(module_name(path))
        self.assertEqual(offenders, [])

    def test_no_subprocess_grep(self):
        offenders = []
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or "subprocess" not in ast.dump(node.func):
                    continue
                rendered = " ".join(
                    a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
                )
                if "grep" in rendered:
                    offenders.append(f"{module_name(path)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_subprocess_passes_a_regex_flag(self):
        """-E / -P / --perl-regexp 传进 shell 的 grep 或 git 是那个 bug 的入口。"""
        offenders = []
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or "subprocess" not in ast.dump(node.func):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value in ("-E", "-P", "--perl-regexp"):
                        offenders.append(f"{module_name(path)}:{node.lineno} {arg.value}")
        self.assertEqual(offenders, [])


class TestCtypesIsolation(unittest.TestCase):
    def test_ctypes_only_in_crypto_and_capture(self):
        """ctypes 出错的失败模式是整个进程崩掉（退出码 127、无输出），
        所以 FFI 必须集中在少数几个文件里，便于审计。"""
        allowed = ("kv.crypto", "kv.capture")
        for path in python_files(KV_DIR):
            module = module_name(path)
            if any(starts_with(module, a) for a in allowed):
                continue
            deps = imported_modules(ast.parse(path.read_text(encoding="utf-8"), str(path)))
            for dep in deps:
                self.assertNotEqual(dep, "ctypes", f"{module} 不得直接 import ctypes")


class TestEncodingDiscipline(unittest.TestCase):
    def test_every_text_open_passes_encoding(self):
        offenders = []
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if name not in OPENERS or name in BYTES_OPENERS:
                    continue
                keywords = {kw.arg for kw in node.keywords}
                if "encoding" not in keywords:
                    offenders.append(f"{module_name(path)}:{node.lineno} {name}()")
        self.assertEqual(offenders, [], "文本读写没带 encoding=（本机 CP 是 936）")

    def test_rule_is_not_vacuous(self):
        """反向确认：kv/ 里确实有文本读写，上面的断言不是空的。"""
        found = False
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                    if name in OPENERS - BYTES_OPENERS:
                        found = True
        self.assertTrue(found)


class TestTestDoubleIsolation(unittest.TestCase):
    DOUBLES = ("PlaintextProtector", "FakeClipboard", "FakeClock")

    def referenced_names(self, path: Path) -> set[str]:
        """用 AST 而不是原始文本：文档字符串里提到替身名字是**该做的**
        （它解释了这个接缝为什么存在），只有真的引用才算违规。"""
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        return names

    def test_test_doubles_never_referenced_by_product_code(self):
        """PlaintextProtector 溜进产品路径会让「静息加密」这条保证悄悄失效，
        而所有测试仍然全绿 —— 这正是它必须被机械检查的原因。"""
        for path in python_files(KV_DIR):
            names = self.referenced_names(path)
            for double in self.DOUBLES:
                self.assertNotIn(double, names, f"{module_name(path)} 引用了测试替身 {double}")
            self.assertNotIn("tests.fakes", names, module_name(path))
            self.assertNotIn("tests", names, f"{module_name(path)} import 了 tests 包")

    def test_fakes_module_lives_under_tests(self):
        self.assertTrue((TESTS_DIR / "fakes.py").exists())
        self.assertFalse((KV_DIR / "fakes.py").exists())

    def test_rule_is_not_vacuous(self):
        """反向确认：PlaintextProtector 确实被测试引用着，上面的断言不是空的。

        只查这一个 —— 它是唯一关系到「静息加密」这条安全保证的替身。
        FakeClipboard / FakeClock 在 M2 之前还没有调用方，不该在这里被要求。
        """
        seen = set()
        for path in python_files(TESTS_DIR):
            seen |= self.referenced_names(path) & set(self.DOUBLES)
        self.assertIn("PlaintextProtector", seen)
        self.assertIn("FakeClock", seen)


class TestFileBudgets(unittest.TestCase):
    LIMIT = 460
    FUNCTION_LIMIT = 60
    # repo.py 是一份按表分节的函数目录：45 个各自 5~20 行的小函数，一次阅读就能
    # 审完整个数据访问面 —— 这正是「SQL 只在一个文件」这条规则的价值所在。
    # 把它按行数拆开会用导航成本换掉这个性质。所以它豁免总行数，但每个函数
    # 仍然必须短 —— 真正要保证的是「一次能装进脑子里」的粒度是函数，不是文件。
    CATALOG_FILES = {"kv.core.repo"}

    def test_no_file_exceeds_the_budget(self):
        over = []
        for path in python_files(KV_DIR):
            module = module_name(path)
            if module in self.CATALOG_FILES:
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > self.LIMIT:
                over.append(f"{module}={lines}")
        self.assertEqual(over, [], f"超过 {self.LIMIT} 行：{over}")

    def test_no_function_exceeds_the_budget(self):
        over = []
        for path in python_files(KV_DIR):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                length = (node.end_lineno or node.lineno) - node.lineno + 1
                if length > self.FUNCTION_LIMIT:
                    over.append(f"{module_name(path)}.{node.name}={length}")
        self.assertEqual(over, [], f"函数超过 {self.FUNCTION_LIMIT} 行：{over}")

    def test_catalog_file_is_actually_a_catalog(self):
        """反向确认豁免的理由成立：repo.py 的函数确实都很短。"""
        path = KV_DIR / "core" / "repo.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        self.assertGreater(len(functions), 20, "repo.py 应该是一份函数目录")
        longest = max((n.end_lineno or n.lineno) - n.lineno + 1 for n in functions)
        self.assertLessEqual(longest, self.FUNCTION_LIMIT)


if __name__ == "__main__":
    unittest.main()
