from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cts


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent


class KosmicKrispCtsTests(unittest.TestCase):
    def test_runner_uses_local_paths_and_manifest(self):
        self.assertEqual(Path(cts.__file__).resolve(), ROOT / "cts.py")
        self.assertEqual(cts.ROOT, ROOT)
        self.assertEqual(cts.CASE_MANIFEST, ROOT / "cts-cases.json")
        self.assertEqual(set(json.loads(cts.CASE_MANIFEST.read_text(encoding="utf-8"))),
                         {"api", "properties", "core", "operations"})
        with patch.object(cts, "run", return_value=7) as run:
            self.assertEqual(cts.main(["--debug"]), 7)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0].configuration, "Debug")

    def test_build_paths_are_independent_and_honor_override(self):
        for override in (None, "custom build", "/tmp/custom-kosmickrisp-build"):
            with self.subTest(override=override), patch.dict(os.environ, {}, clear=True):
                if override is not None:
                    os.environ["BUILD_DIR"] = override
                build, cts_root, source = cts.resolve_paths()
                expected = Path(override).resolve() if override else ROOT / "build"
                self.assertEqual(build, expected)
                self.assertEqual(cts_root, expected / "cts")
                self.assertEqual(source, expected / "src" / "VK-GL-CTS")

    def test_lock_extends_base_with_shared_tool_and_cts_pins(self):
        def lock(path):
            return dict(line.split() for line in path.read_text(encoding="ascii").splitlines() if line.strip())
        base = lock(ROOT.parent / "kosmickrisp_cooperative_matrix" / "dependencies.lock")
        additions = {
            "glslang": "79c91f375f7e278cf99d6925c2b8d3de83ef0f53",
            "VK-GL-CTS": "5c715f61311aaa60a78632091606de56efa220a1",
        }
        self.assertEqual(lock(ROOT / "dependencies.lock"), {**base, **additions})
        self.assertEqual(cts.locked_cts_revision(), additions["VK-GL-CTS"])

    def test_default_experimental_respects_unset_empty_and_user_values(self):
        for value in (None, "", "custom_border", "all", "cooperative_matrix", "cooperative_matrix_maintenance1"):
            with self.subTest(value=value):
                original = {} if value is None else {"MESA_KK_EXPERIMENTAL": value}
                environment = cts.experimental_environment(None, original)
                self.assertEqual(environment["MESA_KK_EXPERIMENTAL"],
                                 cts.KOSMICKRISP_DEFAULT_EXPERIMENTAL if value is None else value)
                self.assertEqual(set(environment), {"MESA_KK_EXPERIMENTAL"})
                self.assertEqual(original, {} if value is None else {"MESA_KK_EXPERIMENTAL": value})

    def test_explicit_baseline_keeps_khr_and_other_flags(self):
        for value in ("", "custom_border", "cooperative_matrix_maintenance1", "custom_border,cooperative_matrix,cooperative_matrix_maintenance1"):
            with self.subTest(value=value):
                environment = cts.experimental_environment("0", {"MESA_KK_EXPERIMENTAL": value})
                flags = environment["MESA_KK_EXPERIMENTAL"].split(",")
                self.assertIn("cooperative_matrix", flags)
                self.assertNotIn("cooperative_matrix_maintenance1", flags)
                if "custom_border" in value:
                    self.assertIn("custom_border", flags)

    def test_baseline_all_preserves_pinned_nonmaintenance_flags(self):
        environment = cts.experimental_environment("0", {"MESA_KK_EXPERIMENTAL": "all"})
        self.assertEqual(set(environment["MESA_KK_EXPERIMENTAL"].split(",")), set(cts.KOSMICKRISP_BASE_EXPERIMENTAL))

    def test_explicit_opt_in_ensures_both_flags_without_losing_others(self):
        for value in ("", "custom_border image_view_min_lod", "cooperative_matrix", "all,custom_border"):
            with self.subTest(value=value):
                environment = cts.experimental_environment("1", {"MESA_KK_EXPERIMENTAL": value})
                flags = environment["MESA_KK_EXPERIMENTAL"].split(",")
                self.assertIn("cooperative_matrix", flags)
                self.assertIn("cooperative_matrix_maintenance1", flags)
                self.assertEqual(len(flags), len(set(flags)))
                if "custom_border" in value:
                    self.assertIn("custom_border", flags)
        self.assertEqual(cts.experimental_environment("1", {"MESA_KK_EXPERIMENTAL": "all"})["MESA_KK_EXPERIMENTAL"], "all")

    def test_cli_experimental_switch_overrides_environment(self):
        with patch.dict(os.environ, {"MESA_KK_EXPERIMENTAL": ""}, clear=True):
            self.assertIsNone(cts.parse_args([]).experimental)
            self.assertEqual(cts.parse_args(["--experimental", "0"]).experimental, "0")
            self.assertEqual(cts.parse_args(["--experimental", "1"]).experimental, "1")


class RuntimeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kk-cmm-cts-")
        self.addCleanup(self.temporary.cleanup)
        self.build = Path(self.temporary.name).resolve() / "custom build"
        host = patch.object(cts.platform, "platform", return_value="fixture-host")
        host.start()
        self.addCleanup(host.stop)
        self.loader = self.build / "Vulkan-Loader/Release/build/loader/libvulkan.dylib"
        self.library = self.build / "mesa/Release/build/src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib"
        self.manifest = self.library.with_name("kosmickrisp_mesa_devenv_icd.aarch64.json")
        self.binary = self.build / "cts/Release/external/vulkancts/modules/vulkan/deqp-vk"
        for path, content in ((self.loader, b"loader"), (self.library, b"driver"), (self.binary, b"CTS fixture")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.binary.chmod(0o755)
        self.manifest.write_text(json.dumps({"ICD": {"library_path": self.library.name}}), encoding="utf-8")

    def test_driver_loads_loader_and_hashes_relative_icd_library(self):
        library, metadata = cts.driver_library(self.build, "Release")
        self.assertEqual(library, self.loader)
        self.assertEqual(metadata["kosmickrisp_library"], str(self.library))
        self.assertEqual(metadata["vulkan_loader_sha256"], hashlib.sha256(b"loader").hexdigest())
        self.assertEqual(metadata["kosmickrisp_sha256"], hashlib.sha256(b"driver").hexdigest())
        self.assertEqual(metadata["icd_sha256"], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual(set(metadata), {
            "driver", "vulkan_loader", "vulkan_loader_sha256", "kosmickrisp_library",
            "kosmickrisp_sha256", "icd_manifest", "icd_sha256",
        })

    def test_driver_accepts_absolute_icd_library(self):
        self.manifest.write_text(json.dumps({"ICD": {"library_path": str(self.library)}}), encoding="utf-8")
        self.assertEqual(cts.driver_library(self.build, "Release")[1]["kosmickrisp_library"], str(self.library))

    def test_missing_loader_is_actionable(self):
        self.loader.unlink()
        with self.assertRaisesRegex(ValueError, "Private Vulkan loader missing"):
            cts.driver_library(self.build, "Release")

    def test_missing_driver_is_actionable(self):
        self.library.unlink()
        with self.assertRaisesRegex(ValueError, "Private KosmicKrisp missing"):
            cts.driver_library(self.build, "Release")

    def test_manifest_must_be_unique(self):
        extra = self.manifest.with_name("kosmickrisp_mesa_devenv_icd.extra.json")
        extra.write_text(self.manifest.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Expected one KosmicKrisp ICD manifest"):
            cts.driver_library(self.build, "Release")
        extra.unlink()
        self.manifest.unlink()
        with self.assertRaisesRegex(ValueError, "Expected one KosmicKrisp ICD manifest"):
            cts.driver_library(self.build, "Release")

    def test_invalid_manifest_is_actionable(self):
        for content in ("not JSON", "{}", '{"ICD": {"library_path": null}}', '{"ICD": {"library_path": ""}}'):
            with self.subTest(content=content):
                self.manifest.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Invalid KosmicKrisp ICD manifest"):
                    cts.driver_library(self.build, "Release")

    def test_run_uses_loader_records_environment_and_selects_private_parser(self):
        case = "dEQP-VK.info.device_extension_dependencies"
        def execute(command, **kwargs):
            environment = kwargs["env"]
            self.assertEqual(environment["VK_DRIVER_FILES"], str(self.manifest))
            self.assertEqual(environment["VK_ICD_FILENAMES"], str(self.manifest))
            self.assertEqual(environment["MESA_KK_EXPERIMENTAL"], "custom_border,cooperative_matrix")
            self.assertEqual(environment["DYLD_LIBRARY_PATH"], str(self.loader.parent) + ":/fixture-existing-lib")
            self.assertIn("--deqp-vk-library-path=" + str(self.loader), command)
            self.assertNotIn("--deqp-vk-library-path=" + str(self.library), command)
            if "--deqp-runmode=stdout-caselist" in command:
                kwargs["stdout"].write("TEST: " + case + "\n")
            return subprocess.CompletedProcess(command, 0)
        revision = cts.locked_cts_revision()
        results = [{"case": case, "status": "Pass", "detail": "fixture"}]
        with patch.dict(os.environ, {"BUILD_DIR": str(self.build), "MESA_KK_EXPERIMENTAL": "custom_border,cooperative_matrix_maintenance1", "DYLD_LIBRARY_PATH": "/fixture-existing-lib"}), \
            patch.object(cts.subprocess, "check_output", return_value=revision + "\n") as git, \
            patch.object(cts.subprocess, "run", side_effect=execute) as run, \
            patch.object(cts, "read_results", return_value=results) as parser, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cts.main(["--case", case, "--experimental", "0", "--color", "never"]), 0)
        source = self.build / "src/VK-GL-CTS"
        git.assert_called_once_with(["git", "-C", str(source), "rev-parse", "HEAD"], text=True)
        output = next((self.build / "cts/runs").iterdir())
        parser.assert_called_once_with(output / "results.qpa", source)
        self.assertEqual(run.call_count, 2)
        invocation = json.loads((output / "invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(invocation["driver"], "kosmickrisp")
        self.assertEqual(invocation["experimental"], "0")
        self.assertEqual(invocation["environment"]["MESA_KK_EXPERIMENTAL"], "custom_border,cooperative_matrix")
        self.assertEqual(invocation["icd_sha256"], cts.digest(self.manifest))
        self.assertEqual(invocation["kosmickrisp_sha256"], cts.digest(self.library))
        self.assertEqual(invocation["vulkan_loader_sha256"], cts.digest(self.loader))
        self.assertEqual(invocation["vulkan_loader"], str(self.loader))
        self.assertEqual(json.loads((output / "summary.json").read_text(encoding="utf-8"))["pass"], 1)

    def test_runner_is_self_contained(self):
        standalone = self.build.parent / "standalone"
        standalone.mkdir()
        for name in ("cts.py", "cts-cases.json", "dependencies.lock"):
            shutil.copyfile(ROOT / name, standalone / name)
        environment = dict(os.environ, BUILD_DIR=str(standalone / "unbuilt"))
        completed = subprocess.run([sys.executable, str(standalone / "cts.py"), "--group", "properties", "--list-only"],
                                   cwd=standalone, env=environment, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("CTS binary missing", completed.stderr)
        self.assertFalse((standalone / "unbuilt").exists())

    def test_unpinned_cts_is_rejected_before_execution(self):
        with patch.dict(os.environ, {"BUILD_DIR": str(self.build)}), \
                patch.object(cts.subprocess, "check_output", return_value="wrong-revision\n"), \
                patch.object(cts.subprocess, "run") as run, redirect_stderr(io.StringIO()) as error:
            self.assertEqual(cts.main(["--group", "properties"]), 2)
        self.assertIn("Private CTS does not match dependencies.lock", error.getvalue())
        run.assert_not_called()


class ScriptWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kk-cmm-scripts-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.build = self.directory / "custom build"
        self.environment = os.environ.copy()
        self.environment["BUILD_DIR"] = str(self.build)
        self.environment.pop("MESA_KK_EXPERIMENTAL", None)

    def write_executable(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def prepare_smoke(self, configuration):
        self.write_executable(self.build / configuration / "kosmickrisp_cmm",
                              'import json, os, sys\nprint(json.dumps({"args": sys.argv[1:], "environment": dict(os.environ)}))\n')
        for relative in (f"Vulkan-Loader/{configuration}/build/loader/libvulkan.dylib",
                         f"mesa/{configuration}/build/src/kosmickrisp/vulkan/libvulkan_kosmickrisp.dylib",
                         f"mesa/{configuration}/build/src/kosmickrisp/vulkan/kosmickrisp_mesa_devenv_icd.aarch64.json"):
            path = self.build / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

    def test_smoke_forwards_configuration_arguments_and_experimental_values(self):
        for configuration in ("Release", "Debug"):
            self.prepare_smoke(configuration)
            for value in (None, "", "all", "custom_border"):
                with self.subTest(configuration=configuration, value=value):
                    environment = dict(self.environment)
                    if value is not None:
                        environment["MESA_KK_EXPERIMENTAL"] = value
                    completed = subprocess.run(["sh", str(ROOT / "run.sh"), "--" + configuration.lower(), "--query-only"],
                                               env=environment, text=True, capture_output=True, check=True)
                    result = json.loads(completed.stdout)
                    self.assertEqual(result["args"], ["--query-only"])
                    runtime = result["environment"]
                    self.assertEqual(runtime["MESA_KK_EXPERIMENTAL"], cts.KOSMICKRISP_DEFAULT_EXPERIMENTAL if value is None else value)
                    self.assertEqual(runtime["VK_DRIVER_FILES"], runtime["VK_ICD_FILENAMES"])
                    self.assertIn(f"/mesa/{configuration}/build/", runtime["VK_DRIVER_FILES"])

    def test_help_requires_no_build_or_driver(self):
        for script in ("build.sh", "run.sh", "run-cts.sh"):
            with self.subTest(script=script):
                arguments = ["--help"] if script == "build.sh" else ["--debug", "--help"]
                completed = subprocess.run(["sh", str(ROOT / script), *arguments],
                                           env=self.environment, text=True, capture_output=True, check=True)
                self.assertIn("usage:", completed.stdout.lower())
                if script == "run.sh":
                    self.assertIn("--correctness-only", completed.stdout)
        self.assertFalse(self.build.exists())

    def test_smoke_rejects_missing_or_ambiguous_icd(self):
        self.prepare_smoke("Release")
        manifest = next(self.build.glob("mesa/Release/build/src/kosmickrisp/vulkan/*.json"))
        extra = manifest.with_name("kosmickrisp_mesa_devenv_icd.extra.json")
        extra.touch()
        for state in ("ambiguous", "missing"):
            with self.subTest(state=state):
                completed = subprocess.run(["sh", str(ROOT / "run.sh")], env=self.environment, text=True, capture_output=True)
                self.assertEqual(completed.returncode, 1)
                self.assertIn("Expected one KosmicKrisp ICD manifest", completed.stderr)
                if state == "ambiguous":
                    manifest.unlink()
                    extra.unlink()

    def test_build_override_is_used_by_driver_tools_smoke_and_cts(self):
        lab = self.directory / "repo/lab/kosmickrisp_cooperative_matrix_maintenance1"
        base = lab.parent / "kosmickrisp_cooperative_matrix"
        (lab / "patch/mesa").mkdir(parents=True)
        base.mkdir()
        shutil.copyfile(ROOT / "build.sh", lab / "build.sh")
        base_config = REPO / "lab/kosmickrisp_cooperative_matrix/config.sh"
        shutil.copyfile(base_config, base / base_config.name)
        (lab / "patch/mesa/fixture.patch").touch()
        commands = self.directory / "commands"
        trace = self.directory / "trace.jsonl"
        body = ('import json, os, pathlib, sys\n'
                'with open(os.environ["TRACE"], "a", encoding="utf-8") as stream:\n'
                '    stream.write(json.dumps({"tool": pathlib.Path(sys.argv[0]).name, "args": sys.argv[1:], "environment": dict(os.environ)}) + "\\n")\n')
        for command in ("sh", "cmake", "git", "python3"):
            self.write_executable(commands / command, body)
        environment = dict(self.environment, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                           TRACE=str(trace), BUILD_DIR="relative build", JOBS="2")
        subprocess.run(["/bin/sh", str(lab / "build.sh"), "--debug"], cwd=self.directory,
                       env=environment, text=True, capture_output=True, check=True)
        calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        build = self.directory / "relative build"
        driver = calls[0]
        self.assertEqual(driver["args"][1], "--debug")
        self.assertEqual(driver["environment"]["BUILD_DIR"], str(build))
        self.assertEqual(driver["environment"]["KOSMICKRISP_LAB_DIR"], str(lab))
        self.assertEqual(driver["environment"]["KOSMICKRISP_EXTRA_PATCH_DIR"], str(lab / "patch/mesa"))
        self.assertEqual(driver["environment"]["KOSMICKRISP_SKIP_BENCHMARKS"], "1")
        configure = [call["args"] for call in calls if call["tool"] == "cmake" and "-S" in call["args"]]
        self.assertEqual(configure[0][1], str(build / "src/glslang"))
        self.assertEqual(configure[1][1], str(build / "src/SPIRV-Tools"))
        self.assertIn("-DVulkan_LIBRARY=" + str(build / "Vulkan-Loader/Debug/build/loader/libvulkan.dylib"), configure[2])
        self.assertEqual(configure[3][1], str(build / "src/VK-GL-CTS"))
        self.assertEqual(configure[3][3], str(build / "cts/Debug"))
        self.assertIn("-DDEQP_TARGET=osx", configure[3])
        fetch = [call for call in calls if call["tool"] == "python3"]
        self.assertEqual(fetch[0]["args"], [str(build / "src/VK-GL-CTS/external/fetch_sources.py"), "--protocol", "https"])
        self.assertEqual(calls[-1]["args"], ["--build", str(build / "cts/Debug"), "--target", "deqp-vk", "--parallel", "2"])

    def test_driver_patch_series_preserves_index_for_fresh_partial_and_applied_states(self):
        base = self.directory / "repo/lab/kosmickrisp_cooperative_matrix"
        patches = base / "patch/mesa"
        patches.mkdir(parents=True)
        for name in ("build.sh", "config.sh"):
            shutil.copyfile(REPO / "lab/kosmickrisp_cooperative_matrix" / name, base / name)
        extra = self.directory / "extra patches"
        extra.mkdir()
        header = "diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n"
        (patches / "base.patch").write_text(header + "-one\n+two\n", encoding="ascii")
        (extra / "extra.patch").write_text(header + "-two\n+three\n", encoding="ascii")
        source = self.build / "src/mesa"
        source.mkdir(parents=True)
        value = source / "value.txt"
        value.write_text("one\n", encoding="ascii")
        git = ["git", "-C", str(source)]
        subprocess.run([*git, "init", "--quiet"], check=True)
        subprocess.run([*git, "add", "value.txt"], check=True)
        subprocess.run([*git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture"], check=True)
        index = subprocess.check_output([*git, "ls-files", "--stage"])
        revision = subprocess.check_output([*git, "rev-parse", "HEAD"])
        commands = self.directory / "commands"
        for command in ("sh", "uv", "ninja", "llvm-config"):
            self.write_executable(commands / command, "pass\n")
        self.write_executable(commands / "cmake", "import sys\nsys.exit(73)\n")
        environment = dict(self.environment, PATH=str(commands) + os.pathsep + os.environ["PATH"],
                           LLVM_PREFIX=str(self.directory / "unused-llvm"), KOSMICKRISP_EXTRA_PATCH_DIR=str(extra))
        for state in ("one", "two", "three"):
            with self.subTest(state=state):
                value.write_text(state + "\n", encoding="ascii")
                completed = subprocess.run(["/bin/sh", str(base / "build.sh")], env=environment,
                                           text=True, capture_output=True)
                self.assertEqual(completed.returncode, 73, completed.stderr)
                self.assertEqual(value.read_text(encoding="ascii"), "three\n")
                self.assertEqual(subprocess.check_output([*git, "ls-files", "--stage"]), index)
                self.assertEqual(subprocess.check_output([*git, "rev-parse", "HEAD"]), revision)


if __name__ == "__main__":
    unittest.main()