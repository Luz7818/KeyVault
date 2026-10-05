"""命令行入口：argparse 树 + 派发 + 退出码映射。**这里没有业务逻辑。**

命令实现在 kv/commands/ 下按领域分文件。加一组命令是加一个文件，不是把一个
已经太长的文件再加长。

setup_stdio() 必须在任何输出之前跑 —— 本机控制台 CP 是 936、stdout 编码是 gbk，
而仓库路径本身是中文（D:\\东南大学\\...）。见 AGENTS.md 的「已知坑」。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from kv import PROGRAM, __version__, console, errors
from kv.commands import capture as capturecmd
from kv.commands import detect as detectcmd
from kv.commands import ingest as ingestcmd
from kv.commands import m4 as m4cmd
from kv.commands import store as storecmd
from kv.commands.common import db_path
from kv.core import db, masking, repo

DESCRIPTION = "KeyVault —— 本地优先的统一密钥保管工具"
EPILOG = (
    "密钥值默认用 Windows DPAPI 加密后存进 %LOCALAPPDATA%\\KeyVault\\vault.db；"
    "名称、平台、创建时间等元数据保持明文以便搜索。任何命令都不打印密钥值，"
    "kv reveal 除外。"
)

MASK_CHAR_KEY = "mask_char"


def _configured_mask_char(args) -> str:
    """从 vault 里读用户配置的掩码字符。读不到就用 '*'。

    init_mask_char 之后还会再探测一次可编码性 —— 配置成 '•' 但 stdout 不是
    UTF-8 时自动回退，这正是 R10 那个 UnicodeEncodeError 的防线。
    """
    try:
        target = db_path(args)
        if not db.is_initialized(target):
            return console.DEFAULT_MASK_CHAR
        with db.session(target) as conn:
            return repo.get_setting(conn, MASK_CHAR_KEY, console.DEFAULT_MASK_CHAR)
    except (errors.KvError, OSError, ValueError, sqlite3.Error):
        return console.DEFAULT_MASK_CHAR


def _common() -> argparse.ArgumentParser:
    """所有子命令共享的参数。

    default=SUPPRESS 要紧：普通默认值 "" 会让子命令层把顶层 --vault-dir 覆盖掉，
    于是 `kv --vault-dir X list` 静默用了默认目录。SUPPRESS 表示「没给就别设」，
    顶层的值才能活下来 —— 两个位置都支持。
    """
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--vault-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return parent


def _store_parsers(sub, common) -> None:
    """M0 的命令面：vault 生命周期与单条记录的增删查。"""
    p = sub.add_parser("init", parents=[common], help="建 vault 目录、数据库与 DPAPI 金丝雀")
    p.add_argument("--local-machine", action="store_true",
                   help="用机器级 DPAPI（本机任何账户都能解，默认关）")
    p.set_defaults(func=storecmd.cmd_init)

    p = sub.add_parser("doctor", parents=[common], help="体检：绑定、schema、自排除规则、云同步")
    p.set_defaults(func=storecmd.cmd_doctor)

    p = sub.add_parser("add", parents=[common], help="存一把 key")
    p.add_argument("--name", default="", help="记录名；不给就自动生成")
    p.add_argument("--value", default="", help="直接给值（会进 shell 历史，不推荐）")
    p.add_argument("--value-file", default="", help="从文件读值")
    p.add_argument("--stdin", action="store_true", help="从标准输入读值")
    p.add_argument("--platform", default="", help="平台 id；M1 起可自动识别")
    p.add_argument("--key-name", default="", help="原始环境变量名，如 DEEPSEEK_API_KEY")
    p.add_argument("--kind", default="", choices=["", *masking.KINDS], help="值的类型")
    p.add_argument("--tag", action="append", default=[], help="标签，可重复")
    p.add_argument("--note", default="", help="备注（明文列，别往里贴密钥）")
    p.add_argument("--source-url", default="", help="从哪个控制台页面拿到的")
    p.add_argument("--expires-at", default=None, help="过期时间，ISO 格式")
    p.add_argument("--window-title", default="",
                   help="复制来源的窗口标题，用作识别信号（watch 会自动提供）")
    p.add_argument("--force", action="store_true", help="识别结果不可信时也存")
    p.set_defaults(func=storecmd.cmd_add)

    p = sub.add_parser("list", parents=[common], aliases=["ls"], help="列出记录")
    p.add_argument("--query", default="", help="子串搜索：名称/平台/备注/键名/别名")
    p.add_argument("--platform", default="")
    p.add_argument("--tag", default="")
    p.add_argument("--status", default="", choices=["", "active", "revoked", "expired"])
    p.add_argument("--expiring", type=float, default=None, help="只看在 N 天内过期的")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=storecmd.cmd_list)

    p = sub.add_parser("show", parents=[common], help="看一条记录的全部元数据（不含值）")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=storecmd.cmd_show)

    p = sub.add_parser("reveal", parents=[common], help="把明文值打到 stdout（唯一会这么做的命令）")
    p.add_argument("name")
    p.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    p.add_argument("--newline", action="store_true", help="末尾补一个换行")
    p.set_defaults(func=storecmd.cmd_reveal)

    p = sub.add_parser("rm", parents=[common], help="删一条记录")
    p.add_argument("name")
    p.add_argument("--yes", "-y", action="store_true")
    p.set_defaults(func=storecmd.cmd_rm)

    p = sub.add_parser("config", parents=[common], help="读写配置项")
    p.add_argument("--get", default="")
    p.add_argument("--set", default="", help="KEY=VALUE")
    p.set_defaults(func=storecmd.cmd_config)


def _detect_parsers(sub, common) -> None:
    """M1 的命令面：检测大脑。detect 只读不写，selftest 是失败关闭闸门。"""
    p = sub.add_parser("detect", parents=[common],
                       help="识别一段文本里的凭据（只读，不写库）")
    p.add_argument("--text", default="", help="直接给文本")
    p.add_argument("--file", default="", help="从文件读")
    p.add_argument("--stdin", action="store_true", help="从标准输入读")
    p.add_argument("--window-title", default="",
                   help="模拟前台窗口标题（watch 会自动提供；这里给测试用）")
    p.add_argument("--no-vault", action="store_true", help="不加载用户纠正规则")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=detectcmd.cmd_detect)

    p = sub.add_parser("selftest", parents=[common],
                       help="检测表自检；--golden 跑语料并记录闸门")
    p.add_argument("--golden", action="store_true", help="跑 golden 语料")
    p.add_argument("--corpus", default="", help="语料目录（默认 tests/corpus）")
    p.add_argument("--no-record", action="store_true", help="通过后不记录 TABLE_HASH")
    p.set_defaults(func=detectcmd.cmd_selftest)


def _capture_parsers(sub, common) -> None:
    """M2 的命令面：剪切板与监听。"""
    p = sub.add_parser("copy", parents=[common],
                       help="把值放上剪切板，默认 30 秒后自动擦除")
    p.add_argument("name")
    p.add_argument("--ttl", type=float, default=capturecmd.DEFAULT_TTL,
                   help=f"多少秒后自动擦除（默认 {capturecmd.DEFAULT_TTL:.0f}）")
    p.add_argument("--no-wipe", action="store_true", help="不排定自动擦除")
    p.add_argument("--no-exclude-history", action="store_true",
                   help="不排除 Win+V 历史与云剪切板（不建议）")
    p.set_defaults(func=capturecmd.cmd_copy)

    p = sub.add_parser("watch", parents=[common],
                       help="前台监听剪切板，复制到凭据时自动识别")
    p.add_argument("--interval", type=float, default=capturecmd.DEFAULT_INTERVAL,
                   help="轮询间隔秒数")
    p.add_argument("--spool", action="store_true",
                   help="把候选 DPAPI 加密后落盘到 inbox，之后用 kv review 处理")
    p.add_argument("--auto", action="store_true",
                   help="自动接受 exact/high/manual 的候选（其余仍入队）")
    p.add_argument("--no-window-hint", action="store_true",
                   help="关掉前台窗口标题信号")
    p.set_defaults(func=capturecmd.cmd_watch)

    p = sub.add_parser("review", parents=[common], help="处理 --spool 落盘的待审队列")
    p.add_argument("--auto", action="store_true", help="不交互，只自动接受高置信的")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=capturecmd.cmd_review)

    p = sub.add_parser("sweep", parents=[common],
                       help="清扫过期未擦的剪切板（copy/watch/doctor 会自动做）")
    p.set_defaults(func=capturecmd.cmd_sweep)

    # 隐藏子命令：kv copy 派出的分离子进程跑的就是它。
    # argv 里只有一个行 id，**没有密钥** —— helper 靠比对指纹工作，根本不需要明文。
    p = sub.add_parser("_wipe", parents=[common], help=argparse.SUPPRESS)
    p.add_argument("wipe_id", type=int)
    p.set_defaults(func=capturecmd.cmd_wipe_helper)

    _curation_parsers(sub, common)


def _curation_parsers(sub, common) -> None:
    """M2 的命令面：事后修正与整理。"""
    p = sub.add_parser("fix", parents=[common],
                       help="逐条修正识别不出厂商的记录（ambiguous / none）")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=capturecmd.cmd_fix)

    p = sub.add_parser("set-platform", parents=[common], help="直接指定一条记录的平台")
    p.add_argument("name")
    p.add_argument("platform")
    p.add_argument("--remember", default="", choices=["", "keyname", "group"],
                   help="顺便持久化一条纠正规则；group 会影响未来所有同形态的值")
    p.add_argument("--allow-unknown-platform", action="store_true",
                   help="允许用检测表里没有的平台 id")
    p.set_defaults(func=capturecmd.cmd_set_platform)

    p = sub.add_parser("rename", parents=[common], help="改名，旧名保留为别名")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=capturecmd.cmd_rename)

    p = sub.add_parser("tag", parents=[common], help="增删标签")
    p.add_argument("name")
    p.add_argument("--add", action="append", default=[])
    p.add_argument("--remove", action="append", default=[])
    p.set_defaults(func=capturecmd.cmd_tag)

    p = sub.add_parser("corrections", parents=[common], help="查看/删除用户纠正规则")
    p.add_argument("--delete", type=int, default=0, help="按 id 删除一条")
    p.set_defaults(func=capturecmd.cmd_corrections)

    _ingest_parsers(sub, common)


def _ingest_parsers(sub, common) -> None:
    """M3 的命令面：批量摄入、.env 注入、轮换、吊销。"""
    p = sub.add_parser("import", parents=[common],
                       help="批量导入 .env 或 AWS credentials.csv")
    p.add_argument("file", help="要导入的文件路径")
    p.add_argument("--tag", action="append", default=[], help="标签，可重复")
    p.add_argument("--force", action="store_true", help="识别结果不可信时也存")
    p.set_defaults(func=ingestcmd.cmd_import)

    p = sub.add_parser("use", parents=[common],
                       help="把密钥值注入 .env 文件（原子写，保留注释）")
    p.add_argument("name", help="记录名")
    p.add_argument("--to", required=True, help="目标 .env 文件路径")
    p.add_argument("--key-name", default="", help="覆盖环境变量名（默认用记录的 key_name）")
    p.add_argument("--force", action="store_true", help="即使 .env 被 git 跟踪也写入")
    p.add_argument("--gitignore", action="store_true", help="自动把 .env 加进 .gitignore")
    p.set_defaults(func=ingestcmd.cmd_use)

    p = sub.add_parser("rotate", parents=[common],
                       help="轮换密钥值（旧指纹保留为 revoked）")
    p.add_argument("name", help="记录名")
    p.add_argument("--value", default="", help="直接给值（会进 shell 历史，不推荐）")
    p.add_argument("--value-file", default="", help="从文件读值")
    p.add_argument("--stdin", action="store_true", help="从标准输入读值")
    p.set_defaults(func=ingestcmd.cmd_rotate)

    p = sub.add_parser("revoke", parents=[common],
                       help="标记记录为已吊销（值不变，status 翻成 revoked）")
    p.add_argument("name", help="记录名")
    p.set_defaults(func=ingestcmd.cmd_revoke)


def _m4_parsers(sub, common) -> None:
    """M4 的命令面：审计、扫描、过期、清除。"""
    p = sub.add_parser("scan", parents=[common],
                       help="扫描目录树，查找泄露的密钥")
    p.add_argument("directory", nargs="?", default=".",
                   help="要扫描的目录（默认当前目录）")
    p.set_defaults(func=m4cmd.cmd_scan)

    p = sub.add_parser("audit", parents=[common],
                       help="审计链校验或事件列表")
    p.add_argument("--verify", action="store_true",
                   help="校验审计链完整性")
    p.add_argument("--event", default="", help="按事件类型过滤")
    p.add_argument("--secret-id", type=int, default=None, help="按记录 id 过滤")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=m4cmd.cmd_audit)

    p = sub.add_parser("expire", parents=[common],
                       help="标记已过期记录（expires_at 已过的翻成 expired）")
    p.set_defaults(func=m4cmd.cmd_expire)

    p = sub.add_parser("purge", parents=[common],
                       help="清除已吊销 / 已过期记录的数据")
    p.add_argument("--delete", action="store_true",
                   help="完全删除记录（默认只清零 value_blob）")
    p.add_argument("--dry-run", action="store_true",
                   help="只报告将清除多少条，不实际执行")
    p.set_defaults(func=m4cmd.cmd_purge)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM, description=DESCRIPTION, epilog=EPILOG)
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {__version__}")
    parser.add_argument("--vault-dir", default="", help="覆盖 vault 目录（主要给测试用）")
    sub = parser.add_subparsers(dest="command", required=True)
    common = _common()

    _store_parsers(sub, common)
    _detect_parsers(sub, common)
    _capture_parsers(sub, common)
    _m4_parsers(sub, common)
    return parser


def main(argv: list[str] | None = None) -> int:
    console.setup_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    console.init_mask_char(_configured_mask_char(args))

    try:
        return int(args.func(args) or 0)
    except errors.KvError as exc:
        console.error(str(exc))
        if exc.hint:
            console.hint(exc.hint)
        return exc.exit_code
    except KeyboardInterrupt:
        console.echo("")
        console.warn("已中断")
        return errors.EXIT_INTERRUPTED
    except FileNotFoundError as exc:
        console.error(f"文件不存在：{exc.filename}")
        return errors.EXIT_NOT_FOUND


if __name__ == "__main__":
    sys.exit(main())
