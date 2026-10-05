"""crypto —— DPAPI 与导出容器。

本包不得 import kv.core（依赖方向是 core → crypto，反过来会成环）。
全项目只有本包允许出现 ctypes。
"""
