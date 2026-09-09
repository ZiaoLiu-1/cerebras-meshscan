"""Mac-side preparation/timeout/error contracts; never runs the real SDK."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_compaction as runner
from tools.compaction_oracle_bridge import run_oracle


class RunnerTests(unittest.TestCase):
    binary = ROOT / "build/meshcompact-reference"

    def test_smallest_and_largest_contracts_use_real_cpp(self):
        for width, chunk in ((1, 1), (3, 2), (8, 64)):
            request, expected = runner.build_request(self.binary, width, chunk, 1)
            self.assertEqual(len(request["cases"]), len(expected))
            self.assertTrue(any(case["length"] == 0 for case in request["cases"]))
            self.assertTrue(all(len(case["values"]) == width * chunk for case in request["cases"]))

    def test_independent_cpp_hand_worked(self):
        result = run_oracle(self.binary, [2, -1, 5, 5, -4, 9, 0, 7], 4, 3, 5)
        self.assertEqual(result["output"], [5, 5, 9, 7])
        self.assertEqual(result["indices"], [2, 3, 5, 7])
        self.assertEqual([pe["forwarded_count"] for pe in result["pes"]], [0, 3, 2, 1])

    def test_cpp_failure_cannot_be_success(self):
        with self.assertRaises(ValueError):
            run_oracle(self.binary, [1, 2], 1, 1, 0)

    def test_oracle_schema_mutations_rejected(self):
        actual = run_oracle(self.binary, [4, 5], 2, 2, 4)
        mutations = []
        for field, value in (("width", True), ("selected_count", 1),
                             ("indices", [0, 0]), ("output", [4, 6]),
                             ("pes", []), ("schema", "other")):
            mutated = copy.deepcopy(actual)
            mutated[field] = value
            mutations.append(mutated)
        for mutated in mutations:
            completed = subprocess.CompletedProcess([], 0, json.dumps(mutated), "")
            with patch("tools.compaction_oracle_bridge.subprocess.run", return_value=completed):
                with self.assertRaises(ValueError):
                    run_oracle(self.binary, [4, 5], 2, 2, 4)

    def test_low_disk_blocks_next_sdk_command(self):
        usage = type("Usage", (), {"free": 3 * 1024**3})()
        with patch.object(runner.shutil, "disk_usage", return_value=usage):
            with self.assertRaisesRegex(ValueError, "4 GiB"):
                runner.headroom(ROOT)

    def test_prepare_only_has_no_success_launches_or_guest_calls(self):
        with tempfile.TemporaryDirectory(prefix="meshcompact-test-") as temporary:
            private = Path(temporary) / "runs"
            arguments = ["run_compaction", "--prepare-only", "--widths", "1", "3",
                         "--chunk-size", "3", "--random-cases", "0",
                         "--private-root", str(private), "--sdk", str(Path(temporary) / "no-sdk")]
            with patch.object(sys, "argv", arguments), \
                    patch.object(runner, "headroom", return_value=8 * 1024**3), \
                    patch.object(runner, "execute") as execute:
                self.assertEqual(runner.main(), 0)
                execute.assert_not_called()
            report_path = next(private.glob("*/validation.json"))
            report = json.loads(report_path.read_text())
            self.assertEqual(report["status"], "prepared-not-executed")
            self.assertEqual(report["device_launches_verified"], 0)
            self.assertEqual(len(report["layouts"]), 2)
            for layout in report["layouts"]:
                self.assertEqual(layout["host_fastpaths"], 0)
                self.assertEqual(layout["status"], "prepared")
                guest = report_path.parent / f"p{layout['width']}"
                self.assertFalse((guest / "oracle.json").exists())
                self.assertEqual(set(item.name for item in guest.iterdir()),
                                 {Path(name).name for name in runner.DEVICE_FILES} | {"input.json"})
                self.assertTrue((report_path.parent / layout["oracle_record"]).is_file())

    def test_guest_failure_leaves_only_failure_report(self):
        with tempfile.TemporaryDirectory(prefix="meshcompact-failure-") as temporary:
            private = Path(temporary) / "runs"
            sdk = Path(temporary) / "fake-sdk"
            sdk.mkdir()
            (sdk / "cslc").touch()
            (sdk / "unit-test.sif").write_bytes(b"inert unit-test fixture, never executed")
            arguments = ["run_compaction", "--widths", "1", "--random-cases", "0",
                         "--private-root", str(private), "--sdk", str(sdk)]
            with patch.object(sys, "argv", arguments), \
                    patch.object(runner.shutil, "which", return_value="mock-limactl"), \
                    patch.object(runner, "headroom", return_value=8 * 1024**3), \
                    patch.object(runner, "execute", side_effect=ValueError("simulated compile failure")):
                self.assertEqual(runner.main(), 1)
            self.assertEqual(len(list(private.glob("*/failure.json"))), 1)
            self.assertEqual(list(private.glob("*/validation.json")), [])


if __name__ == "__main__":
    unittest.main()
