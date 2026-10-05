"""FB1/FB2/FB3: all official renderers, strict boundaries and preserved artifacts."""

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.archify import logic


def engine_dir():
    """Use an explicit test prerequisite, never a developer path."""
    value = os.environ.get("ARCHIFY_TEST_ENGINE")
    assert value, "Set ARCHIFY_TEST_ENGINE to official Archify 3.0.1 archify/ directory."
    path = Path(value).resolve()
    assert path.is_dir()
    return path


def fails(code, call):
    """Expected errors are typed and leave no implied successful artifact."""
    try:
        call()
    except logic.ArchifyError as exc:
        assert exc.code == code, (code, exc.code, str(exc))
        return exc
    raise AssertionError("Invalid request accepted: " + code)


def main():
    """Run real offline Node renderers on original upstream MIT example JSON."""
    engine = engine_dir()
    example_root = Path(__file__).resolve().parents[1] / "examples"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        storage = root / "storage"
        storage.mkdir()
        config = {"engine_directory": str(engine)}
        fixture = None
        for kind in sorted(logic.TYPES):
            path = next(example_root.glob("*." + kind + ".json"))
            diagram = json.loads(path.read_text(encoding="utf-8"))
            result = logic.process(diagram, config, root, storage)
            assert result["issues"]["status"] == "ready", result["issues"]
            assert result["issues"]["diagram_type"] == kind
            rendered = Path(result["file_path"])
            assert rendered.read_text(encoding="utf-8") == result["html"] and "<svg" in result["html"]
            assert rendered.stat().st_mode & 0o777 == 0o600
            assert rendered.parent == storage / "diagrams"
            assert result["issues"]["engine_commit"] == logic.ENGINE["commit"]
            if kind == "architecture":
                fixture, earlier = diagram, rendered
        before = earlier.read_bytes()
        bad = copy.deepcopy(fixture)
        bad["components"][0]["unsupported"] = "x"
        exc = fails("render", lambda: logic.process(bad, config, root, storage))
        assert any(x["code"].startswith("schema/") for x in exc.issues), exc.issues
        assert earlier.read_bytes() == before
        assert len(list((storage / "diagrams").glob("archify-*.html"))) == 5
        assert not list((storage / "diagrams").glob(".archify-work-*"))
        malicious = copy.deepcopy(fixture)
        malicious["meta"]["output"] = str(root / "outside.html")
        result = logic.process(malicious, {**config, "visual_preset": "blueprint"}, root, storage)
        assert 'data-preset="blueprint"' in result["html"]
        assert not (root / "outside.html").exists()
        assert result["issues"]["authored_output_ignored"]
        destination = root / "export"
        destination.mkdir()
        result = logic.process(fixture, {**config, "output_directory": str(destination), "max_artifacts": 1}, root, storage)
        assert Path(result["file_path"]).parent == destination
        fails("storage_limit", lambda: logic.process(fixture, {**config, "output_directory": str(destination),
                                                       "max_artifacts": 1}, root, storage))
        fails("cancelled", lambda: logic.process(fixture, config, root, storage, cancel=lambda: True))
        fails("type_mismatch", lambda: logic.process(fixture, {**config, "diagram_type": "sequence"}, root, storage))
        for key in ("brand", "sources", "repository"):
            external = copy.deepcopy(fixture)
            external["components"][0][key] = "https://example.invalid/"
            fails("external_access", lambda: logic.process(external, config, root, storage))
        fails("json", lambda: logic.diagram_input('{"x":1,"x":2}', logic.configuration({})))
        fails("json", lambda: logic.diagram_input('{"x":NaN}', logic.configuration({})))
        fails("input_limit", lambda: logic.diagram_input(" " * 2048, logic.configuration({"max_input_bytes": 1024})))
        deep = {}; current = deep
        for _ in range(30):
            current["x"] = {}; current = current["x"]
        fails("input_limit", lambda: logic.diagram_input(deep, logic.configuration({})))
        fails("engine_missing", lambda: logic.process(fixture, {}, root, storage))
        wrong = root / "wrong-engine"
        wrong.mkdir()
        first = next(iter(logic.ENGINE["files"]))
        source = wrong / first
        source.parent.mkdir(parents=True)
        source.write_text("changed", encoding="utf-8")
        fails("engine_integrity", lambda: logic.process(fixture, {"engine_directory": str(wrong)}, root, storage))
        linked = root / "linked"
        linked.symlink_to(engine, target_is_directory=True)
        fails("path", lambda: logic.process(fixture, {"engine_directory": str(linked)}, root, storage))
        lock_destination = root / "lock-test"
        lock_destination.mkdir()
        sentinel = root / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        (lock_destination / ".archify.lock").symlink_to(sentinel)
        try:
            logic.process(fixture, {**config, "output_directory": str(lock_destination)}, root, storage)
        except OSError:
            pass
        else:
            raise AssertionError("Symlinked lock accepted")
        assert sentinel.read_text(encoding="utf-8") == "preserve"
        # Child cancellation after launch and environment isolation, using harmless local scripts.
        script = root / "wait.mjs"
        script.write_text("setTimeout(()=>{},10000)", encoding="utf-8")
        started = time.monotonic()
        fails("cancelled", lambda: logic.renderer_command(shutil.which("node"), script, root / "in.json",
              root / "out.html", started + 2, lambda: time.monotonic() - started > 0.15, 1048576))
        assert time.monotonic() - started < 2
        script.write_text("if(process.env.NODE_OPTIONS||process.env.ARCHIFY_REPO_ROOT)process.exit(9)", encoding="utf-8")
        with patch.dict(os.environ, {"NODE_OPTIONS": "--not-a-real-option", "ARCHIFY_REPO_ROOT": str(root)}):
            logic.renderer_command(shutil.which("node"), script, root / "in.json", root / "out.html",
                                   time.monotonic() + 2, lambda: False, 1048576)
        for invalid in ({"timeout_sec": True}, {"timeout_sec": 121}, {"unknown": 1}, {"visual_preset": "other"}):
            fails("config", lambda: logic.configuration(invalid))
    print("[ok] Archify real five-type rendering, offline boundaries, integrity, persistence and cancellation")


if __name__ == "__main__":
    main()
