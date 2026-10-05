"""后端核心逻辑测试：直接调用函数并用 FastAPI TestClient 测接口。

运行：pip install -r requirements.txt && python -m pytest -q
（无 pytest 时也可：python tests/test_core.py）
"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.main import (
    format_offset,
    parse_local,
    solve,
    utc_candidates,
    validate_payload,
)

NY = "America/New_York"
SH = "Asia/Shanghai"
LON = "Europe/London"


def rec(local, tz, lo=None, hi=None):
    r = {"local": local, "timezone": tz}
    if lo is not None:
        r["min_seconds"] = lo
        r["max_seconds"] = hi
    return r


class CandidateTests(unittest.TestCase):
    def test_normal_unique(self):
        tz = ZoneInfo(SH)
        c = utc_candidates(parse_local("2024-06-01 12:00"), tz)
        self.assertEqual(c, [datetime(2024, 6, 1, 4, 0)])

    def test_fallback_two_candidates_sorted(self):
        tz = ZoneInfo(NY)
        c = utc_candidates(parse_local("2024-11-03 01:30"), tz)
        self.assertEqual(c, [datetime(2024, 11, 3, 5, 30), datetime(2024, 11, 3, 6, 30)])

    def test_spring_forward_gap_empty(self):
        tz = ZoneInfo(NY)
        c = utc_candidates(parse_local("2024-03-10 02:30"), tz)
        self.assertEqual(c, [])

    def test_invalid_format(self):
        for bad in ["2024-13-01 00:00", "2024/06/01 12:00", "2024-06-01 25:00",
                    "2024-06-01 12:00:00", "", "24-06-01 12:00"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_local(bad)

    def test_offset_format(self):
        from datetime import timedelta
        self.assertEqual(format_offset(timedelta(hours=8)), "UTC+08:00")
        self.assertEqual(format_offset(timedelta(hours=-5)), "UTC-05:00")
        self.assertEqual(format_offset(timedelta(hours=5, minutes=30)), "UTC+05:30")


class ValidationTests(unittest.TestCase):
    def base(self, n=2):
        return [rec("2024-06-01 12:00", SH)] + [
            rec("2024-06-01 13:00", SH, 0, 86400) for _ in range(n - 1)
        ]

    def test_ok(self):
        records, errors = validate_payload({"records": self.base()})
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 2)

    def test_wrong_count(self):
        _, errors = validate_payload({"records": self.base(1)})
        self.assertTrue(any(e["field"] == "records" for e in errors))
        _, errors = validate_payload({"records": self.base(13)})
        self.assertTrue(any(e["field"] == "records" for e in errors))

    def test_row_format_error_located(self):
        data = self.base()
        data[1]["local"] = "2024-02-30 13:00"
        _, errors = validate_payload({"records": data})
        self.assertEqual(errors[0]["row"], 2)
        self.assertEqual(errors[0]["field"], "local")

    def test_bad_timezone_located(self):
        data = self.base()
        data[0]["timezone"] = "Asia/NoSuchCity"
        _, errors = validate_payload({"records": data})
        self.assertEqual(errors[0]["row"], 1)
        self.assertEqual(errors[0]["field"], "timezone")

    def test_seconds_range_and_order(self):
        data = self.base()
        data[1]["min_seconds"] = -1
        data[1]["max_seconds"] = 86401
        _, errors = validate_payload({"records": data})
        rows = {(e["row"], e["field"]) for e in errors}
        self.assertIn((2, "min_seconds"), rows)
        self.assertIn((2, "max_seconds"), rows)

        data = self.base()
        data[1]["min_seconds"] = 100
        data[1]["max_seconds"] = 50
        _, errors = validate_payload({"records": data})
        self.assertTrue(any(e["field"] == "max_seconds" for e in errors))

    def test_bool_rejected(self):
        data = self.base()
        data[1]["min_seconds"] = True
        _, errors = validate_payload({"records": data})
        self.assertTrue(any(e["field"] == "min_seconds" for e in errors))

    def test_float_rejected(self):
        data = self.base()
        data[1]["min_seconds"] = 1.5
        _, errors = validate_payload({"records": data})
        self.assertTrue(any(e["field"] == "min_seconds" for e in errors))

    def test_missing_interval_fields(self):
        data = [rec("2024-06-01 12:00", SH), rec("2024-06-01 13:00", SH)]
        _, errors = validate_payload({"records": data})
        self.assertTrue(any(e["field"] == "min_seconds" for e in errors))
        self.assertTrue(any(e["field"] == "max_seconds" for e in errors))


class SolverTests(unittest.TestCase):
    def run_solve(self, records):
        tzs = [ZoneInfo(r["timezone"]) for r in records]
        return solve(records, tzs)

    def utcs(self, result):
        return [o["utc"] for o in result["path"]]

    def test_simple_feasible(self):
        records = [
            rec("2024-06-01 12:00", SH),
            rec("2024-06-01 13:00", SH, 3600, 3600),
        ]
        r = self.run_solve(records)
        self.assertTrue(r["feasible"])
        self.assertEqual(self.utcs(r),
                         [datetime(2024, 6, 1, 4, 0), datetime(2024, 6, 1, 5, 0)])

    def test_nongreedy_must_backtrack(self):
        # 第1行 01:30 NY 歧义 05:30/06:30；贪心选 05:30 在第2行 [0,3600] 即死，
        # 必须回退选 06:30，最终 06:30→07:30→08:30→08:30。
        records = [
            rec("2024-11-03 01:30", NY),
            rec("2024-11-03 02:30", NY, 0, 3600),
            rec("2024-11-03 03:30", NY, 0, 3600),
            rec("2024-11-03 08:30", "UTC", 0, 3600),
        ]
        r = self.run_solve(records)
        self.assertTrue(r["feasible"])
        self.assertEqual(self.utcs(r), [
            datetime(2024, 11, 3, 6, 30),
            datetime(2024, 11, 3, 7, 30),
            datetime(2024, 11, 3, 8, 30),
            datetime(2024, 11, 3, 8, 30),
        ])
        self.assertEqual(r["path"][0]["fold"], 1)

    def test_lexicographically_earliest(self):
        # 两行都歧义：伦敦 2024-10-27 01:00 → 00:00Z/01:00Z
        records = [
            rec("2024-10-27 01:00", LON),
            rec("2024-10-27 01:30", LON, 0, 7200),
        ]
        r = self.run_solve(records)
        self.assertTrue(r["feasible"])
        # 字典序最早：00:00 -> 00:30（同 fold=0 分支）
        self.assertEqual(self.utcs(r),
                         [datetime(2024, 10, 27, 0, 0), datetime(2024, 10, 27, 0, 30)])

    def test_infeasible_first_bad_row(self):
        records = [
            rec("2024-06-01 12:00", SH),
            rec("2024-06-01 13:00", SH, 0, 100),       # 实际 3600s，前缀2不可行
            rec("2024-06-01 14:00", SH, 0, 86400),
        ]
        r = self.run_solve(records)
        self.assertFalse(r["feasible"])
        self.assertEqual(r["first_bad_row"], 2)

    def test_infeasible_later_prefix_with_ambiguous_first(self):
        # 第1行歧义、第2行两种都能接上，第3行才断 -> first_bad=3
        records = [
            rec("2024-11-03 01:30", NY),
            rec("2024-11-03 03:30", NY, 3600, 10800),
            rec("2024-06-01 00:00", "UTC", 0, 10),
        ]
        r = self.run_solve(records)
        self.assertFalse(r["feasible"])
        self.assertEqual(r["first_bad_row"], 3)

    def test_gap_row_has_no_options(self):
        records = [
            rec("2024-03-10 02:30", NY),
            rec("2024-03-10 03:30", NY, 0, 86400),
        ]
        r = self.run_solve(records)
        self.assertFalse(r["feasible"])
        self.assertEqual(r["options_count"][0], 0)
        self.assertEqual(r["first_bad_row"], 1)

    def test_actual_deltas_are_inclusive_bounds(self):
        records = [
            rec("2024-11-03 01:30", NY),
            rec("2024-11-03 02:30", NY, 7200, 7200),  # 只接受 fold=0 分支
        ]
        r = self.run_solve(records)
        self.assertTrue(r["feasible"])
        self.assertEqual(r["path"][0]["fold"], 0)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.main import app
        cls.client = TestClient(app)

    def test_index_served(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("跨时区", resp.text)

    def test_health(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")

    def test_verify_ok(self):
        resp = self.client.post("/api/verify", json={"records": [
            rec("2024-06-01 12:00", SH),
            rec("2024-06-01 13:00", SH, 3600, 3600),
        ]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["feasible"])
        self.assertEqual(body["rows"][0]["offset"], "UTC+08:00")
        self.assertEqual(body["rows"][1]["actual_seconds"], 3600)

    def test_verify_gap_422(self):
        resp = self.client.post("/api/verify", json={"records": [
            rec("2024-03-10 02:30", NY),
            rec("2024-03-10 04:30", NY, 0, 86400),
        ]})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"][0]["row"], 1)

    def test_verify_bad_timezone_422(self):
        resp = self.client.post("/api/verify", json={"records": [
            rec("2024-06-01 12:00", "Foo/Bar"),
            rec("2024-06-01 13:00", SH, 0, 86400),
        ]})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["errors"][0]["row"], 1)

    def test_verify_infeasible_payload(self):
        resp = self.client.post("/api/verify", json={"records": [
            rec("2024-06-01 12:00", SH),
            rec("2024-06-01 13:00", SH, 0, 10),
        ]})
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["feasible"])
        self.assertEqual(body["first_bad_row"], 2)

    def test_bad_json(self):
        resp = self.client.post("/api/verify", content=b"{not json",
                                headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
