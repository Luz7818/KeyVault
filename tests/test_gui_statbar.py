"""statbar 数据聚合测试：指标卡口径与平台分布的环形图切分。

聚合是纯函数（只读 status/name），不建窗口、不碰 DPAPI —— 渲染层有测试
预算之外的眼花缭乱，这里守的是「仪表盘不会说谎」。
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field


@dataclass
class _Row:
    """最小行替身：collect_metrics / platform_distribution 只读这两个字段。"""

    name: str
    platform: str
    status: str = "active"


class TestCollectMetrics(unittest.TestCase):
    def test_counts_by_status(self):
        from kv.gui.statbar import collect_metrics

        rows = [
            _Row("a", "deepseek"),
            _Row("b", "github"),
            _Row("c", "github", status="revoked"),
            _Row("d", "aws", status="expired"),
        ]
        m = collect_metrics(rows, expiring_names=["a"])
        self.assertEqual(m.total, 4)
        self.assertEqual(m.active, 2)
        self.assertEqual(m.expiring, 1)  # 只有 active 且在临期名单里的算
        self.assertEqual(m.flagged, 2)   # revoked + expired

    def test_expiring_only_counts_active(self):
        from kv.gui.statbar import collect_metrics

        rows = [_Row("gone", "x", status="expired")]
        m = collect_metrics(rows, expiring_names=["gone"])
        self.assertEqual(m.expiring, 0)


class TestPlatformDistribution(unittest.TestCase):
    def test_empty_returns_empty(self):
        from kv.gui.statbar import platform_distribution

        self.assertEqual(platform_distribution([]), [])

    def test_counts_active_only_sorted_desc(self):
        from kv.gui.statbar import platform_distribution

        rows = [
            _Row("a", "github"),
            _Row("b", "github"),
            _Row("c", "deepseek"),
            _Row("d", "dead-platform", status="revoked"),
        ]
        dist = platform_distribution(rows, top=4)
        self.assertEqual(dist, [("github", 2), ("deepseek", 1)])

    def test_top_n_merges_rest_as_other(self):
        from kv.gui.statbar import platform_distribution

        rows = [_Row(f"k{i}", f"p{i}") for i in range(6)]
        dist = platform_distribution(rows, top=4)
        self.assertEqual(len(dist), 5)
        self.assertEqual(dist[-1][0], "其他")
        self.assertEqual(dist[-1][1], 2)
        # 其余按数量降序
        counts = [n for _, n in dist[:-1]]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_same_count_sorts_by_name(self):
        from kv.gui.statbar import platform_distribution

        rows = [_Row("x", "zeta"), _Row("y", "alpha")]
        self.assertEqual(platform_distribution(rows), [("alpha", 1), ("zeta", 1)])


if __name__ == "__main__":
    unittest.main()
