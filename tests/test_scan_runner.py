"""Scan staging and failure tests; no VM or SDK execution."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_simulator as runner


class ScanRunnerTests(unittest.TestCase):
    def test_prepare_keeps_oracles_outside_sdk_directory(self):
        with tempfile.TemporaryDirectory(prefix="meshscan-staging-") as folder:
            private = Path(folder) / "runs"
            arguments = ["run_simulator", "--prepare-only", "--widths", "1", "3",
                         "--random-cases", "0", "--private-root", str(private),
                         "--sdk", str(Path(folder) / "no-sdk")]
            usage = type("Usage", (), {"free": 8 * 1024**3})()
            with patch.object(sys, "argv", arguments), \
                    patch.object(runner.shutil, "disk_usage", return_value=usage), \
                    patch.object(runner, "execute") as execute:
                self.assertEqual(runner.main(), 0)
                execute.assert_not_called()
            report_path = next(private.glob("*/validation.json"))
            report = json.loads(report_path.read_text())
            self.assertEqual(report["status"], "prepared-not-executed")
            self.assertEqual(report["device_launches_verified"], 0)
            self.assertEqual(report["host_fastpaths_verified"], 2)
            for layout in report["layouts"]:
                guest = report_path.parent / f"p{layout['width']}"
                self.assertEqual({p.name for p in guest.iterdir()},
                                 {Path(p).name for p in runner.DEVICE_SOURCE_FILES} | {"input.json"})
                oracle = report_path.parent / layout["oracle_record"]
                self.assertNotEqual(oracle.parent, guest)
                self.assertEqual(runner.digest(oracle), layout["oracle_sha256"])
                self.assertEqual(runner.digest(guest / "input.json"), layout["input_sha256"])

    def test_changed_records_cannot_produce_a_pass(self):
        for changed in ("input", "oracle"):
            with self.subTest(changed=changed), \
                    tempfile.TemporaryDirectory(prefix="meshscan-mutation-") as folder:
                private = Path(folder) / "runs"
                sdk = Path(folder) / "inert-sdk"
                sdk.mkdir()
                (sdk / "cslc").touch()
                arguments = ["run_simulator", "--widths", "1", "--random-cases", "0",
                             "--private-root", str(private), "--sdk", str(sdk)]
                usage = type("Usage", (), {"free": 8 * 1024**3})()

                def fake_execute(command, log, timeout):
                    if log.name == "compile.log":
                        out = log.parent / "out"
                        out.mkdir()
                        (out / "out.json").write_text("{}")
                    else:
                        target = log.parent / "input.json" if changed == "input" else \
                            log.parent.parent / "oracles/p1.json"
                        target.write_text("{}")
                    return {"exit_code": 0}

                with patch.object(sys, "argv", arguments), \
                        patch.object(runner.shutil, "disk_usage", return_value=usage), \
                        patch.object(runner.shutil, "which", return_value="mock-limactl"), \
                        patch.object(runner, "execute", side_effect=fake_execute):
                    self.assertEqual(runner.main(), 1)
                self.assertEqual(list(private.glob("*/validation.json")), [])
                failure = json.loads(next(private.glob("*/failure.json")).read_text())
                self.assertEqual(failure["status"], "failed")
                self.assertIn("input/oracle record changed", failure["error"])


if __name__ == "__main__":
    unittest.main()
