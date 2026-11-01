"""宿主主循环：stdio 上的请求/响应会话。

生命周期 = 浏览器到本扩展的 native messaging 端口生命周期：扩展每次
connectNative 拉起一个宿主进程，端口断开（EOF）即退出。vault 在会话开始
打开一次；打不开（未初始化/绑定失效）不阻断会话——ping 如实报告
initialized=false，其余请求得到明确错误，扩展得以显示引导文案。
"""

from __future__ import annotations

import sys

from kv import errors
from kv.host import log, protocol, service


def main() -> int:
    if protocol.is_tty_stdin():
        # 浏览器拉起时 stdin 是管道；人在终端里手跑会挂死等输入
        log.warning("stdin 不是管道——宿主由浏览器拉起运行，不要交互执行")
        return errors.EXIT_USAGE
    store = _open_store()
    log.info("会话开始（vault %s）", "就绪" if store is not None else "未就绪")
    while True:
        try:
            request = protocol.read_message(sys.stdin.buffer)
        except protocol.ProtocolError as exc:
            log.warning("坏帧，终止会话：%s", exc)
            return errors.EXIT_USAGE
        if request is None:
            return 0  # 端口关闭（扩展断开/浏览器退出）
        response = service.handle(store, request)
        try:
            protocol.write_message(sys.stdout.buffer, response)
        except protocol.ProtocolError as exc:
            log.warning("响应无法发出，终止会话：%s", exc)
            return errors.EXIT_USAGE


def _open_store():
    from kv.core.vault import VaultStore
    from kv.paths import vault_db

    try:
        store = VaultStore(vault_db())
        store.require_initialized()
        store.verify_binding()
        return store
    except Exception as exc:  # noqa: BLE001 —— 未初始化/绑定失效都不阻断会话
        log.info("vault 未就绪（%s）", exc)
        return None
