import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

import cts


class CtsWorkflowTests(unittest.TestCase):
    def test_manifest_is_grouped_and_bounded(self):
        manifest = json.loads((cts.ROOT / "cts-cases.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"api", "properties", "core", "operations"})
        self.assertIn("dEQP-VK.info.device_extension_dependencies", manifest["api"])
        self.assertIn("dEQP-VK.info.device_mandatory_features", manifest["api"])
        self.assertEqual(len(manifest["properties"]), 1)
        self.assertEqual(sum(".reduce_" in case for case in manifest["operations"]), 11)
        self.assertEqual(sum(".per_element_" in case for case in manifest["operations"]), 4)

    def test_empty_selection_is_error(self):
        with self.assertRaisesRegex(ValueError, "No eligible CTS cases"):
            cts.expand_patterns([cts.MATRIX_ROOT + ".not_a_case"], [])

    def test_nv_features_are_excluded_but_optional_ext_is_retained(self):
        prefix = cts.MATRIX_ROOT + ".khr_r.subgroupscope."
        self.assertTrue(cts.relevant_case(prefix + "getcoordinate.float32_float32.buffer.rowmajor.linear"))
        self.assertTrue(cts.relevant_case(prefix + "convert_maint1_dst_khr_a.input_float32_t_output_float16_t.buffer.rowmajor"))
        self.assertFalse(cts.relevant_case(prefix + "reduce_sum_row.float32_float32.buffer.rowmajor.linear"))
        self.assertFalse(cts.relevant_case(prefix + "matrixmuladd.float32_float32.buffer.rowmajor.tensorlayout"))
        self.assertFalse(cts.relevant_case(prefix + "convert_sat_maint1_dst_khr_r.input_float32_t_output_float_e5m2_t.buffer.rowmajor"))
        self.assertFalse(cts.relevant_case(cts.MATRIX_ROOT + ".khr_r.workgroupscope.add.float32_float32.buffer.rowmajor.linear"))

    def test_summary_preserves_skip_and_error_statuses(self):
        results = [{"case": "pass", "status": "Pass"}, {"case": "skip", "status": "NotSupported"},
                   {"case": "fail", "status": "InternalError"}]
        summary = cts.summarize(results, ["pass", "skip", "fail"], 0)
        self.assertEqual((summary["total"], summary["pass"], summary["fail"], summary["skip"]), (3, 1, 1, 1))
        self.assertEqual(summary["statuses"]["InternalError"], 1)
        self.assertEqual(summary["exit_code"], 1)

    def test_missing_duplicate_and_zero_results_cannot_pass(self):
        self.assertEqual(cts.summarize([], [], 0)["exit_code"], 1)
        self.assertEqual(cts.summarize([], ["case"], 0)["missing"], ["case"])
        duplicated = [{"case": "case", "status": "Pass"}] * 2
        self.assertEqual(cts.summarize(duplicated, ["case"], 0)["duplicates"], ["case"])

    def test_cts_exit_code_is_preserved(self):
        self.assertEqual(cts.summarize([], ["case"], 7)["exit_code"], 7)
        self.assertEqual(cts.summarize([], ["case"], -11)["exit_code"], 139)

    def test_configuration_and_baseline_cli(self):
        arguments = cts.parse_args(["--debug", "--group", "api", "--group", "properties", "--experimental", "0"])
        self.assertEqual(arguments.configuration, "Debug")
        self.assertEqual(arguments.group, ["api", "properties"])
        self.assertEqual(arguments.experimental, "0")
        with patch.dict("os.environ", {"VULKANLAB_EXPERIMENTAL_CMM1": "0"}):
            self.assertEqual(cts.parse_args([]).experimental, "0")
            self.assertEqual(cts.parse_args(["--experimental", "1"]).experimental, "1")
        with patch.dict("os.environ", {"VULKANLAB_EXPERIMENTAL_CMM1": ""}):
            self.assertEqual(cts.parse_args([]).experimental, "1")

    def test_build_dir_override_selects_cts_and_driver_root(self):
        with patch.dict("os.environ", {"BUILD_DIR": "custom-build"}):
            settings = runpy.run_path(str(cts.ROOT / "cts.py"))
        build_dir = Path("custom-build").resolve()
        self.assertEqual(settings["BUILD_DIR"], build_dir)
        self.assertEqual(settings["CTS_ROOT"], build_dir / "cts")
        self.assertEqual(settings["SOURCE"], build_dir / "cts" / "src" / "VK-GL-CTS")

    def test_missing_binary_points_to_shared_builder(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cts, "CTS_ROOT", Path(directory)):
            for configuration in ("release", "debug"):
                with self.subTest(configuration=configuration):
                    with self.assertRaisesRegex(ValueError, "CTS binary missing; run sh build.sh --" + configuration):
                        cts.run(cts.parse_args(["--" + configuration, "--group", "properties"]))

    def test_cts_revision_comes_from_shared_dependency_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cts, "ROOT", Path(directory)):
            (cts.ROOT / "dependencies.lock").write_text(
                "MoltenVK driver-revision\n\nVK-GL-CTS cts-revision\nSPIRV-Cross compiler-revision\n",
                encoding="ascii",
            )
            self.assertEqual(cts.locked_cts_revision(), "cts-revision")

    def test_missing_cts_pin_is_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cts, "ROOT", Path(directory)):
            (cts.ROOT / "dependencies.lock").write_text("MoltenVK driver-revision\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "VK-GL-CTS is missing from dependencies.lock"):
                cts.locked_cts_revision()

    def test_bundled_qpa_parser_reads_xml_and_truncated_crash(self):
        with tempfile.TemporaryDirectory(dir=cts.CTS_ROOT) as directory:
            qpa = Path(directory) / "fixture.qpa"
            qpa.write_text(
                '#beginSession\n#beginTestCaseResult case\n'
                '<TestCaseResult><Result StatusCode="NotSupported">optional feature disabled</Result></TestCaseResult>\n'
                '#endTestCaseResult\n#beginTestCaseResult crash\n<TestCaseResult>\n',
                encoding="utf-8",
            )
            results = cts.read_results(qpa)
        self.assertEqual([result["status"] for result in results], ["NotSupported", "Crash"])
        self.assertEqual(results[0]["detail"], "optional feature disabled")


if __name__ == "__main__":
    unittest.main()