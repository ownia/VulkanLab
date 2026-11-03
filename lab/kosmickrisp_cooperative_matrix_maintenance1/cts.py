import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
CASE_MANIFEST = ROOT / "cts-cases.json"
KOSMICKRISP_DEFAULT_EXPERIMENTAL = "cooperative_matrix,cooperative_matrix_maintenance1"
KOSMICKRISP_BASE_EXPERIMENTAL = (
    "custom_border", "image_view_min_lod", "m5_optimize_e5b9g9r9", "cooperative_matrix",
)
MATRIX_ROOT = "dEQP-VK.compute.pipeline.cooperative_matrix"
CORE_OPERATIONS = (
    "length", "constant", "composite", "composite_rvalue", "add", "sub", "div",
    "mul", "negate", "matrixtimesscalar", "func", "func_const_in", "composite_array",
    "matrixmuladd", "matrixmuladd_min", "matrixmuladd_max", "matrixmuladd_array",
    "matrixmuladd_saturated", "matrixmuladd_wrapping", "matrixmuladd_stride0",
    "matrixmuladd_cross", "matrixmuladd_push", "matrixmuladd_split_barrier",
    "matrixmuladd_split_barrier_loop", "matrixmuladd_split_barrier_skew",
    "convert_base_dst_khr_*", "multicomponent",
)
GOOD_STATUSES = {"Pass", "NotSupported", "QualityWarning", "CompatibilityWarning"}
COLORS = {"bold": "1", "green": "32", "red": "31", "yellow": "33"}


def colorize(text, style, mode="auto", stream=None):
    stream = sys.stdout if stream is None else stream
    enabled = mode == "always" or (
        mode == "auto" and stream.isatty()
        and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
    )
    return f"\033[{COLORS[style]}m{text}\033[0m" if enabled else text


def print_results(summary, results, color, max_failures=10):
    print(colorize("\nCTS results", "bold", color))
    print(f"  Total     {summary['total']}/{summary['selected']}")
    for key, label, style in (
        ("pass", "Pass", "green"), ("fail", "Fail", "red"),
        ("skip", "Skip", "yellow"), ("warnings", "Warnings", "yellow"),
    ):
        line = f"  {label:<9} {summary[key]}"
        print(colorize(line, style, color) if summary[key] else line)
    exits = f"  Exit      CTS {summary['cts_exit_code']}, runner {summary['exit_code']}"
    print(colorize(exits, "red", color) if summary["exit_code"] else exits)

    failures = [result for result in results if result["status"] not in GOOD_STATUSES]
    if failures:
        displayed = failures[:max_failures] if max_failures else failures
        print(colorize(f"\nFailures ({len(displayed)} of {len(failures)})", "red", color))
        for result in displayed:
            status = colorize(result["status"], "red", color)
            print(f"  {status}: {result['case']}\n    {result['detail']}")
    if summary["missing"] or summary["unexpected"] or summary["duplicates"] or summary.get("parse_error"):
        print(colorize("Incomplete or invalid result set; see summary.json and console.log", "red", color))


def relevant_case(case):
    parts = case.split(".")
    if "cooperative_matrix" not in parts:
        return True
    tail = parts[parts.index("cooperative_matrix") + 1:]
    if tail == ["maintenance1", "required_type_combinations"]:
        return True
    if len(tail) < 3 or tail[0] not in {"khr_a", "khr_b", "khr_c", "khr_r"}:
        return False
    if tail[1] != "subgroupscope":
        return False
    operation = tail[2]
    if not (any(fnmatch.fnmatchcase(operation, pattern) for pattern in CORE_OPERATIONS)
            or "maint1" in operation or operation == "getcoordinate"):
        return False
    return not any(part in {"tensorlayout", "blocksize", "decode"}
                   or "e5m2" in part or "e4m3" in part for part in tail)


def expand_patterns(patterns, cases):
    selected = set()
    coverage = {}
    for pattern in patterns:
        matches = {case for case in cases
                   if fnmatch.fnmatchcase(case, pattern) and relevant_case(case)}
        if not matches:
            raise ValueError("No eligible CTS cases match: " + pattern)
        selected.update(matches)
        coverage[pattern] = len(matches)
    return sorted(selected), coverage


def read_results(qpa, source=None):
    source = resolve_paths()[2] if source is None else source
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source / "scripts" / "log"))
    from log_parser import BatchResultParser

    parser = BatchResultParser()
    parser.init(str(qpa))
    results = []
    with qpa.open("rb") as stream:
        while True:
            result = parser.getNextTestCaseResult(stream)
            if result is None:
                break
            results.append({"case": result.name, "status": result.statusCode,
                            "detail": result.statusDetails})
    return results


def summarize(results, selected, cts_exit_code):
    counts = Counter(result["status"] for result in results)
    names = Counter(result["case"] for result in results)
    missing = sorted(set(selected) - names.keys())
    unexpected = sorted(names.keys() - set(selected))
    duplicates = sorted(name for name, count in names.items() if count != 1)
    failed = sum(count for status, count in counts.items() if status not in GOOD_STATUSES)
    exit_code = cts_exit_code if cts_exit_code >= 0 else 128 - cts_exit_code
    if not exit_code and (not results or failed or missing or unexpected or duplicates):
        exit_code = 1
    return {
        "selected": len(selected), "total": len(results), "pass": counts["Pass"],
        "fail": failed, "skip": counts["NotSupported"],
        "warnings": counts["QualityWarning"] + counts["CompatibilityWarning"],
        "statuses": dict(sorted(counts.items())), "missing": missing,
        "unexpected": unexpected, "duplicates": duplicates,
        "cts_exit_code": cts_exit_code, "exit_code": exit_code,
    }


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def locked_cts_revision():
    lines = (ROOT / "dependencies.lock").read_text(encoding="ascii").splitlines()
    dependencies = dict(line.split() for line in lines if line.strip())
    if "VK-GL-CTS" not in dependencies:
        raise ValueError("VK-GL-CTS is missing from dependencies.lock")
    return dependencies["VK-GL-CTS"]


def resolve_paths():
    build_dir = Path(os.environ.get("BUILD_DIR") or str(ROOT / "build")).resolve()
    return build_dir, build_dir / "cts", build_dir / "src" / "VK-GL-CTS"


def experimental_environment(experimental, environment=None):
    environment = dict(os.environ if environment is None else environment)
    value = environment.get("MESA_KK_EXPERIMENTAL", KOSMICKRISP_DEFAULT_EXPERIMENTAL)
    if experimental is not None:
        if experimental == "1" and value == "all":
            environment["MESA_KK_EXPERIMENTAL"] = value
            return environment
        flags = re.findall(r"[A-Za-z0-9_]+", value)
        if "all" in flags:
            flags = [flag for flag in flags if flag != "all"] + list(KOSMICKRISP_BASE_EXPERIMENTAL)
        flags = [flag for flag in flags if flag != "cooperative_matrix_maintenance1"]
        flags.append("cooperative_matrix")
        if experimental == "1":
            flags.append("cooperative_matrix_maintenance1")
        value = ",".join(dict.fromkeys(flags))
    environment["MESA_KK_EXPERIMENTAL"] = value
    return environment


def driver_library(build_dir, configuration):
    loader = build_dir / "Vulkan-Loader" / configuration / "build" / "loader" / "libvulkan.dylib"
    if not loader.is_file():
        raise ValueError("Private Vulkan loader missing: " + str(loader) + "; run build.sh first")
    driver_dir = build_dir / "mesa" / configuration / "build" / "src" / "kosmickrisp" / "vulkan"
    manifests = sorted(driver_dir.glob("kosmickrisp_mesa_devenv_icd.*.json"))
    if len(manifests) != 1 or not manifests[0].is_file():
        raise ValueError("Expected one KosmicKrisp ICD manifest in " + str(driver_dir) + "; run build.sh first")
    manifest = manifests[0]
    try:
        library_path = json.loads(manifest.read_text(encoding="utf-8"))["ICD"]["library_path"]
        if not isinstance(library_path, str) or not library_path:
            raise ValueError("ICD library_path must be a nonempty string")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Invalid KosmicKrisp ICD manifest: " + str(manifest) + ": " + str(error)) from error
    library = Path(library_path)
    library = (library if library.is_absolute() else manifest.parent / library).resolve()
    if not library.is_file():
        raise ValueError("Private KosmicKrisp missing: " + str(library) + "; run build.sh first")
    return loader, {
        "driver": "kosmickrisp", "vulkan_loader": str(loader), "vulkan_loader_sha256": digest(loader),
        "kosmickrisp_library": str(library), "kosmickrisp_sha256": digest(library),
        "icd_manifest": str(manifest), "icd_sha256": digest(manifest),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="run-cts.sh", description="Run a bounded CTS audit using this lab's private KosmicKrisp.")
    configuration = parser.add_mutually_exclusive_group()
    configuration.add_argument("--release", dest="configuration", action="store_const", const="Release")
    configuration.add_argument("--debug", dest="configuration", action="store_const", const="Debug")
    parser.set_defaults(configuration="Release")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--group", action="append", choices=["api", "properties", "core", "operations", "audit"],
                           help="repeat to combine groups; default: audit")
    selection.add_argument("--case", action="append", help="repeat quoted CTS wildcard patterns or exact names")
    selection.add_argument("--full", action="store_true", help="all eligible subgroup KHR/maintenance1 pipeline cases")
    parser.add_argument("--experimental", choices=["0", "1"], default=None,
                        help="maintenance1 opt-in; default: preserve MESA_KK_EXPERIMENTAL, or enable KHR/maintenance1 when unset; 0 keeps KHR only")
    parser.add_argument("--list-only", action="store_true", help="expand and validate selection without executing tests")
    parser.add_argument("--max-cases", type=int, help="execution ceiling (default: 256, no default ceiling with --full)")
    parser.add_argument("--max-failures", type=int, default=10, metavar="N",
                        help="maximum failure details to print (default: 10; 0 shows all)")
    parser.add_argument("--color", choices=["auto", "always", "never"], default="auto",
                        help="terminal colors (default: auto; honors NO_COLOR)")
    arguments = parser.parse_args(argv)
    if arguments.max_cases is not None and arguments.max_cases < 1:
        parser.error("--max-cases must be positive")
    if arguments.max_failures < 0:
        parser.error("--max-failures must be nonnegative")
    return arguments


def run(arguments):
    build_dir, cts_root, source = resolve_paths()
    manifest = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))
    if arguments.case:
        patterns = [pattern for value in arguments.case for pattern in value.split(",")]
    elif arguments.full:
        patterns = manifest["api"] + manifest["properties"] + [MATRIX_ROOT + ".khr_*.subgroupscope.*"]
    else:
        groups = arguments.group or ["audit"]
        if "audit" in groups:
            groups = list(manifest)
        patterns = [pattern for group in groups for pattern in manifest[group]]
    if any(not pattern.startswith("dEQP-VK.") or any(character.isspace() for character in pattern)
           for pattern in patterns):
        raise ValueError("Each --case must be a nonempty dEQP-VK case name or wildcard pattern")

    build = cts_root / arguments.configuration
    binary = build / "external" / "vulkancts" / "modules" / "vulkan" / "deqp-vk"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("CTS binary missing; run sh build.sh --" + arguments.configuration.lower())
    loader, driver_metadata = driver_library(build_dir, arguments.configuration)
    expected_revision = locked_cts_revision()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != expected_revision:
        raise ValueError("Private CTS does not match dependencies.lock; rebuild it")

    runs = cts_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    output = Path(tempfile.mkdtemp(prefix=stamp, dir=runs))
    print("Results: " + str(output), flush=True)
    environment = experimental_environment(arguments.experimental)
    environment["VK_DRIVER_FILES"] = driver_metadata["icd_manifest"]
    environment["VK_ICD_FILENAMES"] = driver_metadata["icd_manifest"]
    environment["DYLD_LIBRARY_PATH"] = str(loader.parent) + (
        ":" + environment["DYLD_LIBRARY_PATH"] if environment.get("DYLD_LIBRARY_PATH") else "")
    driver_metadata["environment"] = {key: environment[key] for key in (
        "MESA_KK_EXPERIMENTAL", "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "DYLD_LIBRARY_PATH",
    )}
    base = [str(binary), "--deqp-vk-library-path=" + str(loader),
            "--deqp-archive-dir=" + str(binary.parent), "--deqp-shadercache=disable",
            "--deqp-log-images=disable", "--deqp-visibility=hidden"]
    enumerate_command = base + ["--deqp-runmode=stdout-caselist", "--deqp-case=" + ",".join(patterns),
                                "--deqp-log-filename=" + str(output / "selection.qpa")]
    invocation = {"configuration": arguments.configuration, "experimental": arguments.experimental,
                  "cts_revision": revision, "host": platform.platform(), "patterns": patterns,
                  "list_only": arguments.list_only, "enumerate_command": enumerate_command}
    invocation.update(driver_metadata)
    write_json(output / "invocation.json", invocation)
    with (output / "selection.log").open("w", encoding="utf-8") as console:
        completed = subprocess.run(enumerate_command, cwd=output, env=environment, stdout=console, stderr=subprocess.STDOUT)
    if completed.returncode:
        raise ValueError("CTS caselist export failed (exit " + str(completed.returncode) + "); see " + str(output / "selection.log"))
    registry = [line[6:].strip() for line in (output / "selection.log").read_text(encoding="utf-8").splitlines()
                if line.startswith("TEST: ")]
    selected, coverage = expand_patterns(patterns, registry)
    caselist = output / "selected-cases.txt"
    caselist.write_text("\n".join(selected) + "\n", encoding="ascii")
    invocation.update({"selected": len(selected), "pattern_counts": coverage})
    write_json(output / "invocation.json", invocation)
    print("Selected " + str(len(selected)) + " actual cases; MESA_KK_EXPERIMENTAL=" + environment["MESA_KK_EXPERIMENTAL"], flush=True)
    if arguments.list_only:
        return 0
    ceiling = arguments.max_cases if arguments.max_cases is not None else (None if arguments.full else 256)
    if ceiling is not None and len(selected) > ceiling:
        raise ValueError("Selection exceeds " + str(ceiling) + " cases; narrow --case/--group or explicitly raise --max-cases")

    qpa = output / "results.qpa"
    command = base + ["--deqp-caselist-file=" + str(caselist), "--deqp-log-filename=" + str(qpa)]
    invocation["execute_command"] = command
    write_json(output / "invocation.json", invocation)
    print("Running: " + shlex.join(command), flush=True)
    with (output / "console.log").open("w", encoding="utf-8") as console:
        completed = subprocess.run(command, cwd=output, env=environment, stdout=console, stderr=subprocess.STDOUT)
    parse_error = None
    try:
        results = read_results(qpa, source)
    except Exception as error:
        results = []
        parse_error = str(error)
    summary = summarize(results, selected, completed.returncode)
    if parse_error:
        summary["parse_error"] = parse_error
        summary["exit_code"] = summary["exit_code"] or 1
    with (output / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["case", "status", "detail"])
        writer.writeheader()
        writer.writerows(results)
    write_json(output / "summary.json", summary)
    message = "Total {total}/{selected}: pass={pass}, fail={fail}, skip={skip}, warnings={warnings}; CTS exit={cts_exit_code}, wrapper exit={exit_code}".format(**summary)
    (output / "summary.txt").write_text(message + "\n", encoding="utf-8")
    print_results(summary, results, arguments.color, arguments.max_failures)
    return summary["exit_code"]


def main(argv=None):
    arguments = parse_args(argv)
    try:
        return run(arguments)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(colorize("CTS: " + str(error), "red", arguments.color, sys.stderr), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())