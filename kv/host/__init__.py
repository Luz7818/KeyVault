"""浏览器扩展的原生消息宿主（native messaging host）。

浏览器扩展拿到「网页上复制了 key」这个事件，但加密、存储、审计链都在
本机的 vault（DPAPI + SQLite）里——两者通过 Chrome/Edge 的 native
messaging 协议相连：stdin/stdout 上的 4 字节小端长度前缀 + UTF-8 JSON。

协议 I/O 在 protocol.py，请求分发在 service.py，宿主主循环在 app.py，
注册表/manifest 管理在 registry.py 与 install.py（`kv host install`）。

两层纪律：
- **stdout 是协议通道**——任何库/异常输出混进去都会腐蚀帧，宿主只经
  write_message 写，日志一律走 stderr。
- 复用而非旁路：检测走 pipeline.parse + resolve，入库走 saveops.commit，
  待审走 inbox 表（spool/pending_from_inbox），复制走 copy_secret——
  宿主只是把「文本从哪来」从剪贴板换成了浏览器，安全语义一个不少。
"""
