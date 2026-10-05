"""Pinned, offline Archify rendering. No LLM, repository scan or remote assets."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

DEFAULTS = {"engine_directory": "", "diagram_type": "auto", "visual_preset": "input",
            "output_directory": "", "timeout_sec": 30, "max_input_bytes": 1048576,
            "max_output_bytes": 8388608, "max_artifacts": 100}
TYPES = {"architecture", "workflow", "sequence", "dataflow", "lifecycle"}
ENGINE = json.loads((Path(__file__).parent / "engine.json").read_text(encoding="utf-8"))


class ArchifyError(ValueError):
    """An expected failure with bounded, actionable issues and no partial HTML."""

    def __init__(self, code, message, issues=None):
        super().__init__(message)
        self.code = code
        self.issues = issues or [{"code": code, "message": message, "severity": "error"}]


def configuration(raw):
    """Validate saved settings, allowing only generic framework execution annotations."""
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS) - {"execution", "runtime_path", "runtime_path_label"}:
        raise ArchifyError("config", "Unknown setting.")
    result = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    for key in ("engine_directory", "output_directory"):
        if not isinstance(result[key], str) or len(result[key]) > 4096 or "\x00" in result[key]:
            raise ArchifyError("config", f"{key} must be a directory path.")
    if result["diagram_type"] not in TYPES | {"auto"}:
        raise ArchifyError("config", "Unknown diagram_type.")
    if result["visual_preset"] not in {"input", "classic", "signal-flow", "blueprint", "editorial"}:
        raise ArchifyError("config", "Unknown visual_preset.")
    for key, low, high in (("timeout_sec", 1, 120), ("max_input_bytes", 1024, 2097152),
                           ("max_output_bytes", 1048576, 16777216), ("max_artifacts", 1, 1000)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ArchifyError("config", f"{key} must be an integer between {low} and {high}.")
    return result


def check(deadline, cancel):
    """Cancellation and a total wall-clock deadline include filesystem preparation."""
    if cancel():
        raise ArchifyError("cancelled", "Rendering cancelled.")
    if time.monotonic() >= deadline:
        raise ArchifyError("timeout", "Rendering exceeded timeout_sec.")


def unique_pairs(pairs):
    """Refuse ambiguous duplicate keys in textual JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ArchifyError("json", "Duplicate JSON property.")
        result[key] = value
    return result


def diagram_input(value, config):
    """Accept bounded finite JSON, reject implicit network/repository capabilities."""
    if isinstance(value, str):
        if len(value.encode("utf-8")) > config["max_input_bytes"]:
            raise ArchifyError("input_limit", "Diagram exceeds max_input_bytes.")
        try:
            value = json.loads(value, object_pairs_hook=unique_pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ArchifyError):
                raise
            raise ArchifyError("json", "Input must be strict finite JSON, without Markdown fences.") from exc
    if not isinstance(value, dict):
        raise ArchifyError("json", "Input must be a typed Archify JSON object.")
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if depth > 24 or count > 30000:
            raise ArchifyError("input_limit", "Diagram nesting or element count exceeded.")
        if isinstance(item, dict):
            if any(not isinstance(key, str) or len(key) > 240 for key in item):
                raise ArchifyError("json", "JSON property names must be bounded strings.")
            # These keys opt into upstream network capture / Git evidence verification.
            # Reject, never silently erase authored evidence or trust remote URLs.
            if set(item) & {"brand", "sources", "repository"}:
                raise ArchifyError("external_access", "brand, sources and repository are not supported by the offline rendering block.")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 512:
                raise ArchifyError("input_limit", "A diagram collection exceeds 512 items.")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > 16000:
                raise ArchifyError("input_limit", "A diagram string exceeds 16000 characters.")
        elif item is not None and type(item) not in (bool, int, float):
            raise ArchifyError("json", "Diagram contains a non-JSON value.")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            try:
                if not math.isfinite(item) or abs(item) > 1000000000:
                    raise ValueError()
            except (ValueError, OverflowError):
                raise ArchifyError("json", "Diagram numbers must be finite and within ±1e9.")
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode("utf-8")) > config["max_input_bytes"]:
            raise ArchifyError("input_limit", "Diagram exceeds max_input_bytes.")
        copied = json.loads(serialized)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ArchifyError):
            raise
        raise ArchifyError("json", "Diagram cannot be serialized as strict UTF-8 JSON.") from exc
    kind = copied.get("diagram_type")
    versions = {1, 2} if kind == "workflow" else {1}
    if kind not in TYPES or type(copied.get("schema_version")) is not int or copied["schema_version"] not in versions:
        raise ArchifyError("schema", "Expected a supported diagram_type and schema_version (1; workflow also accepts 2).")
    if config["diagram_type"] != "auto" and config["diagram_type"] != kind:
        raise ArchifyError("type_mismatch", "Input diagram_type does not match the configured type.")
    if not isinstance(copied.get("meta"), dict):
        raise ArchifyError("schema", "A meta object is required.")
    # Output naming belongs only to this block. The official schema still validates
    # every other field and the renderer validates geometry/relationships.
    copied["meta"]["output"] = "diagram.html"
    if config["visual_preset"] != "input":
        copied["meta"]["visual_preset"] = config["visual_preset"]
    return copied, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def canonical_directory(raw, root):
    """Require an existing canonical directory, with no symlink path components."""
    path = Path(raw).expanduser()
    path = Path(os.path.abspath(path if path.is_absolute() else Path(root) / path))
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ArchifyError("path", "Directory paths must not contain symlinks.")
    if not path.is_dir():
        raise ArchifyError("path", "The configured directory does not exist.")
    return path


def snapshot_engine(directory, target, deadline, cancel):
    """Execute exact verified bytes, not the potentially changing operator directory."""
    for relative, expected in ENGINE["files"].items():
        check(deadline, cancel)
        source = directory / relative
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ArchifyError("engine_integrity", "Archify engine files must not be symlinks.")
        try:
            with source.open("rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2097152:
                    raise ArchifyError("engine_integrity", "Invalid Archify engine file.")
                data = handle.read(2097153)
        except OSError as exc:
            raise ArchifyError("engine_missing", "Archify 3.0.1 runtime files are missing.") from exc
        if hashlib.sha256(data).hexdigest() != expected:
            raise ArchifyError("engine_integrity", "Archify engine differs from the supported 3.0.1 commit.")
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def renderer_command(node, renderer, source, output, deadline, cancel, max_bytes):
    """Own one cancellable Node process with clean environment and bounded captures."""
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT") if key in os.environ}
    environment.update({"ARCHIFY_DIAGNOSTIC_FORMAT": "json", "ARCHIFY_UPDATE_CHECK_DISABLED": "1"})
    check(deadline, cancel)
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen([node, "--max-old-space-size=256", str(renderer), str(source), str(output)],
            cwd=source.parent, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            while child.poll() is None:
                check(deadline, cancel)
                if os.fstat(stdout.fileno()).st_size > 65536 or os.fstat(stderr.fileno()).st_size > 65536:
                    raise ArchifyError("diagnostic_limit", "Renderer diagnostics exceeded 64 KiB.")
                if output.exists() and output.stat().st_size > max_bytes:
                    raise ArchifyError("output_limit", "Rendered HTML exceeds max_output_bytes.")
                try:
                    child.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
            check(deadline, cancel)
            if os.fstat(stdout.fileno()).st_size > 65536 or os.fstat(stderr.fileno()).st_size > 65536:
                raise ArchifyError("diagnostic_limit", "Renderer diagnostics exceeded 64 KiB.")
            stdout.seek(0)
            stderr.seek(0)
            captured = stdout.read(65536).decode("utf-8", "replace")
            err = stderr.read(65536).decode("utf-8", "replace")
            issues = []
            # Official standalone renderer prints a JSON diagnostic envelope on error.
            for stream in (captured, err):
                try:
                    diagnostic = json.loads(stream)
                except ValueError:
                    continue
                if not isinstance(diagnostic, dict):
                    continue
                for item in diagnostic.get("diagnostics", [])[:50]:
                    if isinstance(item, dict):
                        issues.append({"code": str(item.get("code", "render"))[:120],
                            "severity": "warning" if item.get("severity") == "warning" else "error",
                            "message": str(item.get("message", "Rendering failed."))[:1500],
                            "path": str(item.get("subject", {}).get("path", ""))[:300]})
            if child.returncode:
                raise ArchifyError("render", "Archify rejected the diagram; inspect issues for schema/layout details.",
                                   issues or None)
            if err.strip() and not issues:
                issues.append({"code": "renderer_warning", "severity": "warning",
                               "message": "The renderer reported a warning; check locale coverage or authored layout."})
            return issues
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)


@contextmanager
def output_lock(directory, deadline, cancel):
    """Serialize quota check and publication across processes using one destination."""
    fd = os.open(directory / ".archify.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArchifyError("path", "Artifact lock must be a private regular file.")
        while True:
            check(deadline, cancel)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


def process(value, raw_config, root_dir, storage_dir, cancel=lambda: False):
    """Publish a unique complete artifact, retaining earlier artifacts on every failure."""
    config = configuration(raw_config)
    deadline = time.monotonic() + config["timeout_sec"]
    check(deadline, cancel)
    diagram, digest = diagram_input(value, config)
    if not config["engine_directory"]:
        raise ArchifyError("engine_missing", "Configure the official Archify 3.0.1 engine_directory.")
    node = shutil.which("node")
    if not node:
        raise ArchifyError("engine_missing", "Node >=18 is required.")
    engine = canonical_directory(config["engine_directory"], root_dir)
    storage = canonical_directory(str(storage_dir), root_dir)
    if config["output_directory"]:
        output_dir = canonical_directory(config["output_directory"], root_dir)
    else:
        output_dir = storage / "diagrams"
        if output_dir.is_symlink():
            raise ArchifyError("path", "Artifact directory must not be a symlink.")
        output_dir.mkdir(mode=0o700, exist_ok=True)
        output_dir = canonical_directory(str(output_dir), root_dir)
    with output_lock(output_dir, deadline, cancel):
        total, size = 0, 0
        for entry in output_dir.iterdir():
            check(deadline, cancel)
            if entry.name.startswith("archify-") and entry.suffix == ".html":
                total += 1
                size += entry.lstat().st_size
        if total >= config["max_artifacts"] or size + config["max_output_bytes"] > 268435456:
            raise ArchifyError("storage_limit", "Artifact destination is full. Archive files explicitly; nothing was deleted.")
        with tempfile.TemporaryDirectory(prefix=".archify-work-", dir=output_dir) as scratch:
            work = Path(scratch)
            snapshot_engine(engine, work / "engine", deadline, cancel)
            source, output = work / "input.json", work / "diagram.html"
            source.write_text(json.dumps(diagram, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            renderer = work / "engine" / "renderers" / diagram["diagram_type"] / ("render-" + diagram["diagram_type"] + ".mjs")
            issues = renderer_command(node, renderer, source, output, deadline, cancel, config["max_output_bytes"])
            check(deadline, cancel)
            if output.is_symlink() or not output.is_file() or output.stat().st_size > config["max_output_bytes"]:
                raise ArchifyError("output_limit", "No bounded regular HTML artifact was produced.")
            content = output.read_bytes()
            if len(content) > config["max_output_bytes"]:
                raise ArchifyError("output_limit", "Rendered HTML exceeds max_output_bytes.")
            try:
                html = content.decode("utf-8")
            except UnicodeError as exc:
                raise ArchifyError("output", "Renderer output is not UTF-8 HTML.") from exc
            if "<!doctype html>" not in html.lower() or "<svg" not in html:
                raise ArchifyError("output", "Renderer output does not contain a standalone diagram.")
            policy = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                      "img-src data: blob:; font-src data:; media-src blob:; connect-src 'none'; "
                      "object-src 'none'; base-uri 'none'; form-action 'none'")
            html = html.replace("<head>", '<head><meta http-equiv="Content-Security-Policy" content="' + policy + '">', 1)
            content = html.encode("utf-8")
            if len(content) > config["max_output_bytes"]:
                raise ArchifyError("output_limit", "Rendered HTML exceeds max_output_bytes.")
            output.write_bytes(content)
            check(deadline, cancel)
            # Private UUID names, exclusive publication and fsync before announcing a path.
            artifact = output_dir / ("archify-" + uuid.uuid4().hex + ".html")
            os.chmod(output, 0o600)
            with output.open("rb") as handle:
                os.fsync(handle.fileno())
            os.link(output, artifact, follow_symlinks=False)
            directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return {"html": html, "file_path": str(artifact), "issues": {
                "status": "ready", "items": issues, "engine_version": ENGINE["version"],
                "engine_commit": ENGINE["commit"], "input_sha256": digest,
                "html_sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
                "diagram_type": diagram["diagram_type"],
                "authored_output_ignored": True, "repository_evidence_verified": False}}
