from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/gate58-people-crossing-v2.json"
QUERY_START = datetime(2026, 1, 1, 8, 0, 0)
QUERY_END = datetime(2026, 1, 1, 9, 0, 0)
QUERY_ID = "gate58-fixture-query"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = load_module("gate58_review_contract", ROOT / "scripts/gate58_review_contract.py")
COMPARE = load_module("compare_gate58_results", ROOT / "scripts/compare_gate58_results.py")
COMMON = load_module("gate58_common_test", ROOT / "scripts/gate58_common.py")
PENDING = load_module("gate58_pending_manifest", ROOT / "scripts/gate58_pending_manifest.py")
APPLY_PENDING = load_module(
    "gate58_apply_pending_reviews", ROOT / "scripts/gate58_apply_pending_reviews.py"
)


class Gate58ReviewContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.evidence = []
        for index in range(3):
            path = self.work / f"evidence-{index}.jpg"
            path.write_bytes(b"fixture")
            self.evidence.append(str(path))

    def tearDown(self):
        self.temp.cleanup()

    def row(
        self,
        candidate_id: str,
        second: int,
        start_side: str,
        crossed: str,
        end_side: str,
        occluded: str = "no",
    ):
        event = QUERY_START + timedelta(seconds=second)
        return {
            "candidate_id": candidate_id,
            "event_time": event.isoformat(timespec="seconds"),
            "evidence_start": (event - timedelta(seconds=20)).isoformat(timespec="seconds"),
            "evidence_end": (event + timedelta(seconds=20)).isoformat(timespec="seconds"),
            "start_side": start_side,
            "boundary_crossed": crossed,
            "end_side": end_side,
            "occluded": occluded,
            "evidence_paths": " | ".join(self.evidence),
            "review_note": "synthetic fixture",
        }

    def write_input(self, rows, name="review.csv"):
        path = self.work / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CONTRACT.INPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def finalize(self, source: Path, output_name: str) -> tuple[Path, dict]:
        output = self.work / output_name
        summary = CONTRACT.finalize(
            source,
            output,
            self.work / f"{output.stem}.json",
            CONFIG,
            query_start=QUERY_START,
            query_end=QUERY_END,
            query_id=QUERY_ID,
        )
        return output, summary

    def test_clear_crossings_foldback_and_exclusions_are_deterministic(self):
        rows = [
            self.row("clear-enter", 20, "outside", "yes", "inside"),
            self.row("clear-exit", 40, "inside", "yes", "outside"),
            self.row("outside-passer", 60, "outside", "no", "outside"),
            self.row("doorway-stay", 80, "inside", "no", "inside"),
            self.row("occluded", 100, "outside", "unknown", "unknown", "yes"),
            self.row("foldback-enter", 120, "outside", "yes", "inside"),
            self.row("foldback-exit", 126, "inside", "yes", "outside"),
        ]
        source = self.write_input(rows)
        output, summary = self.finalize(source, "final.csv")
        second_output, second_summary = self.finalize(source, "second-final.csv")
        self.assertEqual(summary["enter"], 2)
        self.assertEqual(summary["exit"], 2)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["pending"], 1)
        self.assertTrue(summary["official_result"])
        self.assertEqual(summary["normalized_result_sha256"], second_summary["normalized_result_sha256"])
        self.assertEqual(output.read_bytes(), second_output.read_bytes())
        self.assertTrue(COMPARE.compare(output, second_output)["identical"])

    def test_completed_same_side_foldback_must_be_split(self):
        source = self.write_input([self.row("unsplit-foldback", 20, "outside", "yes", "outside")])
        with self.assertRaises(CONTRACT.ContractError):
            self.finalize(source, "final.csv")

    def test_short_evidence_window_fails_closed(self):
        row = self.row("short-window", 20, "outside", "yes", "inside")
        event = datetime.fromisoformat(row["event_time"])
        row["evidence_start"] = (event - timedelta(seconds=19)).isoformat(timespec="seconds")
        source = self.write_input([row])
        with self.assertRaises(CONTRACT.ContractError):
            self.finalize(source, "final.csv")

    def test_pending_row_still_requires_saved_evidence(self):
        row = self.row("pending-without-proof", 20, "unknown", "unknown", "unknown", "yes")
        row["evidence_paths"] = ""
        source = self.write_input([row])
        with self.assertRaisesRegex(CONTRACT.ContractError, "待复核也不得留空"):
            self.finalize(source, "final.csv")

    def test_opposite_sides_cannot_be_marked_as_not_crossed(self):
        source = self.write_input([self.row("contradictory", 20, "outside", "no", "inside")])
        with self.assertRaisesRegex(CONTRACT.ContractError, "起终点位于门界两侧"):
            self.finalize(source, "final.csv")

    def test_event_outside_query_is_rejected(self):
        row = self.row("outside-query", 20, "outside", "yes", "inside")
        row["event_time"] = QUERY_END.isoformat(timespec="seconds")
        source = self.write_input([row])
        with self.assertRaisesRegex(CONTRACT.ContractError, "超出查询时段"):
            self.finalize(source, "final.csv")

    def test_more_than_two_pending_rows_cannot_be_official(self):
        source = self.write_input(
            [self.row(f"pending-{index}", 20 + index, "unknown", "unknown", "unknown", "yes") for index in range(3)]
        )
        _, summary = self.finalize(source, "pending-final.csv")
        self.assertEqual(summary["quality_gate"], "needs_review")
        self.assertFalse(summary["official_result"])
        self.assertEqual(summary["minimum_possible_total"], 0)
        self.assertEqual(summary["maximum_possible_total"], 3)

    def test_compare_accepts_event_time_jitter_within_three_seconds(self):
        left_source = self.write_input(
            [
                self.row("left-enter", 20, "outside", "yes", "inside"),
                self.row("left-exit", 40, "inside", "yes", "outside"),
            ],
            "left-review.csv",
        )
        right_source = self.write_input(
            [
                self.row("right-enter", 21, "outside", "yes", "inside"),
                self.row("right-exit", 40, "inside", "yes", "outside"),
            ],
            "right-review.csv",
        )
        left, _ = self.finalize(left_source, "left.csv")
        right, _ = self.finalize(right_source, "right.csv")
        result = COMPARE.compare(left, right)
        self.assertFalse(result["identical"])
        self.assertTrue(result["accepted_for_user_goal"])
        self.assertEqual(result["matched_events_within_seconds"], 2)
        self.assertEqual(result["left_only"], [])
        self.assertEqual(result["right_only"], [])

    def test_compare_rejects_count_difference_above_target(self):
        left_source = self.write_input(
            [self.row(f"left-{index}", 20 + index * 10, "outside", "yes", "inside") for index in range(4)],
            "left-many.csv",
        )
        right_source = self.write_input(
            [self.row("right-one", 20, "outside", "yes", "inside")],
            "right-one.csv",
        )
        left, _ = self.finalize(left_source, "left-many-final.csv")
        right, _ = self.finalize(right_source, "right-one-final.csv")
        result = COMPARE.compare(left, right)
        self.assertFalse(result["accepted_for_user_goal"])
        self.assertFalse(result["count_tolerance_ok"])
        self.assertEqual(result["absolute_differences"], {"enter": 3, "exit": 0, "total": 3})

    def test_compare_rejects_different_contract_hashes_even_when_events_match(self):
        source = self.write_input([self.row("same-enter", 20, "outside", "yes", "inside")])
        left, _ = self.finalize(source, "left.csv")
        right, _ = self.finalize(source, "right.csv")
        with right.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[0]["config_sha256"] = "different-contract"
        with right.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CONTRACT.OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        result = COMPARE.compare(left, right)
        self.assertFalse(result["accepted_for_user_goal"])
        self.assertFalse(result["policy_match"])
        self.assertFalse(result["expected_rule_match"])
        self.assertEqual(result["left_only"], [])
        self.assertEqual(result["right_only"], [])

    def test_identical_detects_different_noncounted_decisions(self):
        source = self.write_input(
            [
                self.row("enter", 20, "outside", "yes", "inside"),
                self.row("not-crossing", 40, "outside", "no", "outside"),
            ]
        )
        left, _ = self.finalize(source, "left.csv")
        right, _ = self.finalize(source, "right.csv")
        with right.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        rows[1]["final_decision"] = "待复核"
        with right.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CONTRACT.OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        result = COMPARE.compare(left, right)
        self.assertFalse(result["identical"])
        self.assertTrue(result["accepted_for_user_goal"])

    def test_compare_fails_closed_when_empty_ledger_has_no_contract_metadata(self):
        empty = self.work / "empty.csv"
        with empty.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CONTRACT.OUTPUT_FIELDS)
            writer.writeheader()
        with self.assertRaisesRegex(ValueError, "空清单无法核验规则与查询指纹"):
            COMPARE.compare(empty, empty)

    def test_contract_generated_zero_candidate_results_can_be_compared(self):
        source = self.write_input([])
        left, left_summary = self.finalize(source, "zero-left.csv")
        right, right_summary = self.finalize(source, "zero-right.csv")
        self.assertEqual(left_summary["rows"], 0)
        self.assertEqual(right_summary["total"], 0)
        with left.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["candidate_id"], CONTRACT.NO_CANDIDATE_ID)
        result = COMPARE.compare(left, right)
        self.assertTrue(result["identical"])
        self.assertTrue(result["accepted_for_user_goal"])

    def test_compare_preserves_duplicate_same_second_crossings(self):
        left_source = self.write_input(
            [
                self.row("person-a", 20, "outside", "yes", "inside"),
                self.row("person-b", 20, "outside", "yes", "inside"),
            ],
            "left-duplicates.csv",
        )
        right_source = self.write_input(
            [self.row("person-a", 20, "outside", "yes", "inside")],
            "right-single.csv",
        )
        left, _ = self.finalize(left_source, "left-duplicates-final.csv")
        right, _ = self.finalize(right_source, "right-single-final.csv")
        result = COMPARE.compare(left, right)
        self.assertFalse(result["identical"])
        self.assertTrue(result["accepted_for_user_goal"])
        self.assertEqual(result["left_only"], [{"event_time": "2026-01-01T08:00:20", "direction": "进入"}])

    def test_canonical_config_hash_ignores_windows_line_endings(self):
        value = {"b": [2, 1], "a": "中文"}
        lf = self.work / "lf.json"
        crlf = self.work / "crlf.json"
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        lf.write_bytes(content.encode("utf-8"))
        crlf.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(COMMON.load_json_with_fingerprint(lf), COMMON.load_json_with_fingerprint(crlf))

    def test_pending_manifest_uses_only_pending_rows(self):
        source = self.write_input(
            [
                self.row("clear", 20, "outside", "yes", "inside"),
                self.row("pending", 40, "unknown", "unknown", "unknown", "yes"),
            ]
        )
        final, _ = self.finalize(source, "pending-source.csv")
        manifest = self.work / "pending-episodes.csv"
        result = PENDING.build(final, manifest, "nvr-main-02", 1)
        self.assertEqual(result["candidate_ids"], ["pending"])
        with manifest.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["episode_id"], "pending-pending")

    def test_versioned_second_pass_replaces_only_pending_rows(self):
        source = self.write_input(
            [
                self.row("clear", 20, "outside", "yes", "inside"),
                self.row("pending", 40, "unknown", "unknown", "unknown", "yes"),
            ]
        )
        final, _ = self.finalize(source, "round1-final.csv")
        manifest = self.work / "pending-episodes.csv"
        decisions = self.work / "pending-decisions.csv"
        PENDING.build(final, manifest, "nvr-main-02", 1, decisions)
        with decisions.open(encoding="utf-8", newline="") as handle:
            decision_rows = list(csv.DictReader(handle))
        decision_rows[0].update(
            {
                "start_side": "inside",
                "boundary_crossed": "yes",
                "end_side": "outside",
                "occluded": "no",
                "evidence_paths": " | ".join(self.evidence),
                "review_note": "second pass",
            }
        )
        with decisions.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PENDING.DECISION_FIELDS)
            writer.writeheader()
            writer.writerows(decision_rows)
        round2 = self.work / "reviewed-round2.csv"
        result = APPLY_PENDING.apply(source, final, decisions, round2, CONFIG)
        self.assertEqual(result["candidate_ids"], ["pending"])
        with round2.open(encoding="utf-8", newline="") as handle:
            rows = {row["candidate_id"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["clear"]["start_side"], "outside")
        self.assertEqual(rows["pending"]["start_side"], "inside")
        self.assertEqual(rows["pending"]["evidence_start"], "2026-01-01T07:59:55")
        _, summary = self.finalize(round2, "round2-final.csv")
        self.assertEqual(summary["enter"], 1)
        self.assertEqual(summary["exit"], 1)
        self.assertEqual(summary["pending"], 0)

    def test_second_pass_cannot_skip_a_pending_candidate(self):
        source = self.write_input(
            [
                self.row("pending-a", 20, "unknown", "unknown", "unknown", "yes"),
                self.row("pending-b", 40, "unknown", "unknown", "unknown", "yes"),
            ]
        )
        final, _ = self.finalize(source, "two-pending-final.csv")
        decisions = self.work / "incomplete-decisions.csv"
        with decisions.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PENDING.DECISION_FIELDS)
            writer.writeheader()
            writer.writerow({"candidate_id": "pending-a"})
        with self.assertRaisesRegex(ValueError, "完整覆盖当前待复核项"):
            APPLY_PENDING.apply(source, final, decisions, self.work / "round2.csv", CONFIG)


if __name__ == "__main__":
    unittest.main()
