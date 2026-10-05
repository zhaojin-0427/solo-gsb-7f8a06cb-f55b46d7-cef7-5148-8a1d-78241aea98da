"""求解器行为测试（不依赖第三方库）：

    python3 tests/test_solver.py
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.solver import (  # noqa: E402
    MAX_RECORDS,
    RowError,
    utc_candidates,
    verify_records,
)


def rec(local, tz, mn=None, mx=None):
    d = {"local": local, "timezone": tz}
    if mn is not None:
        d["min_seconds"] = mn
        d["max_seconds"] = mx
    return d


class CandidateTests(unittest.TestCase):
    def test_normal_time_single_candidate(self):
        # 2024-07-01 纽约处于 EDT(UTC-4)
        ny = __import__("zoneinfo").ZoneInfo("America/New_York")
        c = utc_candidates(datetime(2024, 7, 1, 12, 0), ny)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0], datetime(2024, 7, 1, 16, 0, tzinfo=timezone.utc))

    def test_fall_back_two_candidates(self):
        # 2024-11-03 01:30 在纽约秋拨中：EDT(-4)->05:30Z 与 EST(-5)->06:30Z
        ny = __import__("zoneinfo").ZoneInfo("America/New_York")
        c = utc_candidates(datetime(2024, 11, 3, 1, 30), ny)
        self.assertEqual(
            c,
            [
                datetime(2024, 11, 3, 5, 30, tzinfo=timezone.utc),
                datetime(2024, 11, 3, 6, 30, tzinfo=timezone.utc),
            ],
        )

    def test_spring_forward_no_candidate(self):
        # 2024-03-10 02:30 在纽约春播中不存在
        ny = __import__("zoneinfo").ZoneInfo("America/New_York")
        c = utc_candidates(datetime(2024, 3, 10, 2, 30), ny)
        self.assertEqual(c, [])

    def test_fixed_offset_single(self):
        sh = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
        c = utc_candidates(datetime(2024, 11, 3, 22, 0), sh)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0], datetime(2024, 11, 3, 14, 0, tzinfo=timezone.utc))


class VerifyInputTests(unittest.TestCase):
    def test_bad_count(self):
        with self.assertRaisesRegex(RowError, "条数") as cm:
            verify_records([rec("2024-01-01 00:00", "UTC")])
        self.assertEqual(cm.exception.row, 1)

    def test_too_many(self):
        records = [rec("2024-01-01 00:00", "UTC")] + [
            rec("2024-01-01 01:00", "UTC", 0, 86400) for _ in range(MAX_RECORDS)
        ]
        with self.assertRaises(RowError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.row, 1)

    def test_bad_format_localized_to_row(self):
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024/01/02 01:00", "UTC", 0, 86400),
        ]
        with self.assertRaises(RowError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.row, 2)
        self.assertIn("格式错误", cm.exception.message)

    def test_illegal_clock_time_localized(self):
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024-01-02 25:00", "UTC", 0, 86400),
        ]
        with self.assertRaises(RowError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.row, 2)

    def test_strict_shape_single_digits_rejected(self):
        for bad in ("2024-1-2 01:00", "2024-01-02 1:00", "2024-01-02T01:00",
                    "2024-01-02  01:00", "24-01-02 01:00", "2024-01-02 01:00:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(RowError) as cm:
                    verify_records([
                        rec("2024-01-01 00:00", "UTC"),
                        rec(bad, "UTC", 0, 86400),
                    ])
                self.assertEqual(cm.exception.row, 2)
                self.assertIn("格式错误", cm.exception.message)

    def test_bad_timezone_localized(self):
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024-01-02 01:00", "Mars/Olympus", 0, 86400),
        ]
        with self.assertRaises(RowError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.row, 2)
        self.assertIn("时区", cm.exception.message)

    def test_nonexistent_time_localized(self):
        records = [
            rec("2024-03-10 03:00", "America/New_York"),
            rec("2024-03-10 04:00", "America/New_York", 0, 86400),
        ]
        # 让第 2 行成为不存在的时刻
        records[1]["local"] = "2024-03-10 02:30"
        with self.assertRaises(RowError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.row, 2)
        self.assertIn("不存在", cm.exception.message)

    def test_bounds_validation(self):
        base = [rec("2024-01-01 00:00", "UTC")]
        for mn, mx, needle in (
            (-1, 10, "范围"),
            (0, 86401, "范围"),
            (1.5, 10, "整数"),
            (100, 50, "不能大于"),
        ):
            with self.subTest(mn=mn, mx=mx):
                with self.assertRaises(RowError) as cm:
                    verify_records(base + [rec("2024-01-01 01:00", "UTC", mn, mx)])
                self.assertEqual(cm.exception.row, 2)
                self.assertIn(needle, cm.exception.message)


class PathTests(unittest.TestCase):
    def test_ambiguity_picks_earliest_utc(self):
        # 01:30 纽约(秋拨) -> 22:00 上海(14:00Z)
        # fold0 = 05:30Z -> 差 30600s；fold1 = 06:30Z -> 差 27000s
        records = [
            rec("2024-11-03 01:30", "America/New_York"),
            rec("2024-11-03 22:00", "Asia/Shanghai", 27000, 30600),
        ]
        result = verify_records(records)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows"][0]["utc"], "2024-11-03 05:30:00")
        self.assertEqual(result["rows"][0]["candidate_count"], 2)
        self.assertEqual(result["rows"][0]["fold"], 0)
        self.assertEqual(result["rows"][0]["offset"], "UTC-04:00")
        self.assertEqual(result["rows"][0]["offset_abbrev"], "EDT")
        self.assertEqual(result["rows"][1]["gap_seconds"], 30600)
        self.assertEqual(result["rows"][1]["offset"], "UTC+08:00")

    def test_constraint_forces_later_candidate(self):
        # 只允许 27000s -> 必须选 fold1（06:30Z）
        records = [
            rec("2024-11-03 01:30", "America/New_York"),
            rec("2024-11-03 22:00", "Asia/Shanghai", 27000, 27000),
        ]
        result = verify_records(records)
        self.assertEqual(result["rows"][0]["utc"], "2024-11-03 06:30:00")
        self.assertEqual(result["rows"][0]["fold"], 1)
        self.assertEqual(result["rows"][0]["offset_abbrev"], "EST")
        self.assertEqual(result["rows"][1]["gap_seconds"], 27000)

    def test_non_greedy_backtracking_required(self):
        # 三行均为纽约 2024-11-03 01:30（候选 05:30Z / 06:30Z）
        # 区间：row2 [0, 3600] -> 相邻差只能 0 或 3600（同候选差0合法）
        #       row3 [3600, 3600] -> 必须差恰好 3600
        # 逐行贪心会在 row2 选 05:30（字典序最早），row3 无论选什么
        # 与 05:30 的差为 0 或 3600：选 06:30 差 3600 其实可行……
        # 因此构造：row2 区间 [0, 0] 贪心选 05:30/05:30，
        # 但 row3 要求 [3600,3600]，从 05:30 出发到 06:30 差 3600 也可行。
        # 需要让贪心真正失败：row2 上只有 05:30 可走而该选法导致 row3 无解，
        # 而选 06:30 时 row2 也可走且 row3 有解。
        records = [
            rec("2024-11-03 01:30", "America/New_York"),
            # row2：与 row1 的差须为 0（同行同时刻），row1 选什么 row2 就得选什么
            rec("2024-11-03 01:30", "America/New_York", 0, 0),
            # row3：与 row2 差须为 3600
            rec("2024-11-03 02:30", "America/New_York", 3600, 3600),
        ]
        # 02:30 纽约不在歧义段（已 EST, 07:30Z），唯一候选 07:30Z。
        # row2 选 06:30Z -> 到 07:30Z 差 3600 ✓
        # row2 选 05:30Z -> 到 07:30Z 差 7200 ✗
        # 故 row1 必须回溯选 06:30Z（字典序更晚的候选）。
        result = verify_records(records)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["rows"][0]["utc"], "2024-11-03 06:30:00")
        self.assertEqual(result["rows"][1]["utc"], "2024-11-03 06:30:00")
        self.assertEqual(result["rows"][2]["utc"], "2024-11-03 07:30:00")
        self.assertEqual(result["rows"][1]["gap_seconds"], 0)
        self.assertEqual(result["rows"][2]["gap_seconds"], 3600)

    def test_infeasible_points_first_bad_prefix(self):
        # row1: 00:00 UTC = 00:00Z；row2 要求相差恰好 100000（超范围不可能）
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024-01-02 00:00", "UTC", 100, 200),
            rec("2024-01-02 01:00", "UTC", 0, 86400),
        ]
        # 不能构造（100000 超上限被录入校验拦截），改用实际差 86400 但区间 [100,200]
        from app.solver import InfeasibleError

        with self.assertRaises(InfeasibleError) as cm:
            verify_records(records)
        payload = cm.exception.payload
        self.assertEqual(payload["status"], "infeasible")
        self.assertEqual(payload["first_bad_row"], 2)
        self.assertEqual(payload["details"][0]["actual_seconds"], 86400)
        self.assertEqual(payload["details"][0]["required"], [100, 200])

    def test_infeasible_at_row3_not_row2(self):
        # row1、row2 均为 2024-11-03 01:30 纽约（各两个候选 05:30Z/06:30Z）
        # row2 区间 [0,7200] -> 两个候选都可达
        # row3: 2024-11-04 01:30 纽约 = 唯一 06:30Z；row2 到 row3 要求 [100,200]：
        #   从 05:30Z 差 3600，从 06:30Z 差 0，均落空 -> 断点在第 3 行
        records = [
            rec("2024-11-03 01:30", "America/New_York"),
            rec("2024-11-03 01:30", "America/New_York", 0, 7200),
            rec("2024-11-04 01:30", "America/New_York", 100, 200),
        ]
        from app.solver import InfeasibleError

        with self.assertRaises(InfeasibleError) as cm:
            verify_records(records)
        self.assertEqual(cm.exception.payload["first_bad_row"], 3)
        # 诊断应覆盖 row2 的两个可达候选
        self.assertEqual(len(cm.exception.payload["details"]), 2)

    def test_exact_one_day_gap_allowed(self):
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024-01-02 00:00", "UTC", 86400, 86400),
        ]
        result = verify_records(records)
        self.assertEqual(result["rows"][1]["gap_seconds"], 86400)

    def test_offset_non_whole_hour(self):
        # 印度 +05:30
        records = [
            rec("2024-01-01 00:00", "UTC"),
            rec("2024-01-01 11:00", "Asia/Kolkata", 19800, 19800),
        ]
        result = verify_records(records)
        self.assertEqual(result["rows"][1]["offset"], "UTC+05:30")
        self.assertEqual(result["rows"][1]["gap_seconds"], 19800)


if __name__ == "__main__":
    unittest.main(verbosity=2)
