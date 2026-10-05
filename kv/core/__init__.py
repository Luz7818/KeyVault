"""core —— 存储层。本包的 __init__ 刻意是空的：kv.core.vault 会 import
kv.model，而 kv.model 会 import kv.core.masking。这里若提前 import 任何子模块
就会造出循环导入。
"""
