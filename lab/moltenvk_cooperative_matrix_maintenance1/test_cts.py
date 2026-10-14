from contextlib import redirect_stderr, redirect_stdout
import io
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

    def test_color_auto_requires_supported_terminal(self):
        stream = io.StringIO()
        with patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
            self.assertEqual(cts.colorize("Pass", "green", stream=stream), "Pass")
            with patch.object(stream, "isatty", return_value=True):
                self.assertEqual(cts.colorize("Pass", "green", stream=stream), "\033[32mPass\033[0m")
                with patch.dict("os.environ", {"NO_COLOR": ""}):
                    self.assertEqual(cts.colorize("Pass", "green", stream=stream), "Pass")
                with patch.dict("os.environ", {"TERM": "dumb"}):
                    self.assertEqual(cts.colorize("Pass", "green", stream=stream), "Pass")

    def test_color_cli_overrides_automatic_detection(self):
        stream = io.StringIO()
        self.assertEqual(cts.parse_args([]).color, "auto")
        with patch.dict("os.environ", {"NO_COLOR": "1", "TERM": "dumb"}):
            arguments = cts.parse_args(["--color", "always"])
            self.assertEqual(cts.colorize("Fail", "red", arguments.color, stream), "\033[31mFail\033[0m")
        with patch.object(stream, "isatty", return_value=True):
            self.assertEqual(cts.colorize("Fail", "red", "never", stream), "Fail")

    def test_result_display_colors_do_not_change_summary(self):
        results = [{"case": status, "status": status, "detail": "result detail"}
                   for status in ("Pass", "Fail", "NotSupported", "QualityWarning")]
        summary = cts.summarize(results, [result["case"] for result in results], 1)
        original = json.dumps(summary, sort_keys=True)
        output = io.StringIO()
        with redirect_stdout(output):
            cts.print_results(summary, results, "always")
        text = output.getvalue()
        self.assertIn("\033[32m  Pass      1\033[0m", text)
        self.assertIn("\033[31m  Fail      1\033[0m", text)
        self.assertIn("\033[33m  Skip      1\033[0m", text)
        self.assertIn("\033[33m  Warnings  1\033[0m", text)
        self.assertIn("\033[31mFail\033[0m: Fail\n    result detail", text)
        self.assertEqual(json.dumps(summary, sort_keys=True), original)
        with redirect_stdout(io.StringIO()) as plain:
            cts.print_results(summary, results, "never")
        self.assertNotIn("\033[", plain.getvalue())

    def test_incomplete_results_are_highlighted(self):
        summary = cts.summarize([], ["missing"], 0)
        with redirect_stdout(io.StringIO()) as output:
            cts.print_results(summary, [], "always")
        self.assertIn("\033[31mIncomplete or invalid result set", output.getvalue())

    def test_max_failures_cli(self):
        self.assertEqual(cts.parse_args([]).max_failures, 10)
        for limit in (1, 25, 0):
            with self.subTest(limit=limit):
                self.assertEqual(cts.parse_args(["--max-failures", str(limit)]).max_failures, limit)
        for invalid in ("-1", "many"):
            with self.subTest(invalid=invalid), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cts.parse_args(["--max-failures", invalid])
                self.assertEqual(error.exception.code, 2)

    def test_failure_display_limit_preserves_all_results(self):
        results = [{"case": f"case{index:02d}", "status": "Fail", "detail": "result detail"}
                   for index in range(12)]
        summary = cts.summarize(results, [result["case"] for result in results], 1)
        original = json.dumps([summary, results], sort_keys=True)
        for options, expected in (({}, 10), ({"max_failures": 3}, 3),
                                  ({"max_failures": 25}, 12), ({"max_failures": 0}, 12)):
            with self.subTest(options=options), redirect_stdout(io.StringIO()) as output:
                cts.print_results(summary, results, "never", **options)
            text = output.getvalue()
            self.assertIn(f"Failures ({expected} of 12)", text)
            self.assertIn("  Fail      12", text)
            self.assertEqual(text.count("  Fail: "), expected)
            self.assertIn(f"case{expected - 1:02d}", text)
            if expected < len(results):
                self.assertNotIn(f"case{expected:02d}", text)
            self.assertEqual(json.dumps([summary, results], sort_keys=True), original)

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