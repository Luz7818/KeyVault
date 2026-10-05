"""用户纠正规则集。

detect 不碰数据库 —— 纠正规则由调用方查出来后注入进来。这样整张规则表能在
不建库的情况下被测完（tests/test_detect_resolve.py 就是这么做的）。

用户规则在**每一级都排在内置规则前面**，所以覆盖永远赢，且永远给 manual 置信度。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kv.detect import rules

KINDS = ("exact", "keyname", "hostname", "window", "group")


@dataclass(frozen=True)
class CorrectionSet:
    """全部用户纠正。空实例表示「没有任何覆盖」。"""

    exact: dict = field(default_factory=dict)        # sha256 -> (platform, created_at)
    keyname: tuple = ()                                # ((compiled_pattern, platform), ...)
    hostname: dict = field(default_factory=dict)      # host(小写) -> platform
    window: tuple = ()                                 # ((关键词小写, platform), ...)
    group: dict = field(default_factory=dict)          # group id -> platform

    @classmethod
    def empty(cls) -> "CorrectionSet":
        return cls()

    @classmethod
    def from_rows(cls, rows) -> "CorrectionSet":
        """从 correction 表的行构造。行可以是 sqlite3.Row 或 dict。"""
        exact, keyname, hostname, window, group = {}, [], {}, [], {}
        for row in rows:
            kind = row["kind"]
            pattern = row["pattern"]
            platform = row["platform"]
            if kind == "exact":
                exact[pattern] = (platform, row["created_at"] if "created_at" in row.keys() else "")
            elif kind == "keyname":
                try:
                    keyname.append((re.compile(pattern), platform))
                except re.error:
                    continue
            elif kind == "hostname":
                hostname[pattern.lower()] = platform
            elif kind == "window":
                window.append((pattern.lower(), platform))
            elif kind == "group":
                group[pattern] = platform
        return cls(exact=exact, keyname=tuple(keyname), hostname=hostname,
                   window=tuple(window), group=group)

    def is_empty(self) -> bool:
        return not (self.exact or self.keyname or self.hostname or self.window or self.group)

    def exact_platform(self, sha256: str) -> tuple[str, str] | None:
        """这个具体值被用户亲自指定过平台吗。返回 (platform, created_at)。"""
        return self.exact.get(sha256)

    def keyname_hits(self, key_name: str | None) -> list[str]:
        if not key_name:
            return []
        return [platform for pattern, platform in self.keyname if pattern.search(key_name)]

    def hostname_hits(self, hostnames) -> list[str]:
        found = []
        for host in hostnames or ():
            lowered = host.lower()
            if lowered in self.hostname:
                found.append(self.hostname[lowered])
                continue
            for known, platform in self.hostname.items():
                if lowered.endswith("." + known):
                    found.append(platform)
        return found

    def window_hits(self, title: str | None) -> list[str]:
        if not title:
            return []
        lowered = title.lower()
        return [platform for needle, platform in self.window if needle in lowered]

    def group_platform(self, group_id: str | None) -> str | None:
        if not group_id:
            return None
        return self.group.get(group_id)


def suggest_correction_scope(cand, verdict) -> tuple[str, str, str] | None:
    """为一次交互消歧选择「仍然有用的最窄范围」。

    返回 (kind, pattern, 说明) 或 None。优先级：
      键名 > 主机名 > 组默认

    组默认**不会**被静默选中 —— 它是唯一一条能悄悄给未来每一把 OpenAI key
    贴错标签的纠正，必须用户明确要求。
    """
    if cand.key_name:
        pattern = f"^{re.escape(cand.key_name)}$"
        return ("keyname", pattern, f"以后凡是键名 {cand.key_name} 都判成这个平台")
    for host in cand.hostnames:
        return ("hostname", host.lower(), f"以后凡是主机名 {host} 都判成这个平台")
    return None


def group_correction(group_id: str, platform: str) -> tuple[str, str, str]:
    """组级默认。只在用户明确要求时用，调用方必须打印警告。"""
    members = rules.group_members(group_id)
    return (
        "group", group_id,
        f"此后该组（{', '.join(members)}）的裸值一律默认 {platform}；"
        "个别记录可能是错的，用 kv set-platform 修",
    )
