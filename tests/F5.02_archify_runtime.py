"""FB2/FB4: node-scoped artifacts and downstream delivery in every package origin."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from blocs.archify.block import ArchifyBlock
from blocs.html_display.block import HtmlDisplayBlock
from block_test_packages import install_test_package, surface_payload
from ui_smoke_common import create_project_api, create_run_api, data_edge, display_node, graph_payload, isolated_server, text_node, wait_for_run_terminal


def main():
    """Actual renderer outputs travel through the framework, not an in-memory substitute."""
    engine = Path(os.environ["ARCHIFY_TEST_ENGINE"]).resolve()
    fixture = (Path(__file__).resolve().parents[1] / "examples" / "web-app.architecture.json").read_text(encoding="utf-8")
    block = ArchifyBlock()
    for mode in ("centralized", "zeromq_active"):
        for origin in (None, "managed", "linked"):
            with isolated_server() as server:
                node = block.build_node_payload(node_id="diagram", position={"x": 360, "y": 140})
                node["config"] = {**block.default_config(), "engine_directory": str(engine)}
                if origin:
                    model = install_test_package(server, "archify", origin=origin)
                    node["block_version"] = model["version"]
                    for surface in ("modal", "inspector_panel", "node_card"):
                        html = surface_payload(server, model, node, surface=surface)["html"]
                        assert "{{" not in html
                        if surface != "node_card":
                            assert 'data-block-config-field="engine_directory"' in html and "data-owned-apply" in html
                viewer = HtmlDisplayBlock().build_node_payload(node_id="viewer", position={"x": 680, "y": 140})
                viewer["config"] = {"allow_scripts": True}
                document = graph_payload("Archify diagram", [text_node("source", "JSON", fixture, 70, 140), node,
                    viewer, display_node("file", "HTML file", 680, 340), display_node("issues", "Issues", 680, 540)], [
                    data_edge("input", "source", 1, "diagram", 1), data_edge("html", "diagram", 1, "viewer", 1),
                    data_edge("file", "diagram", 2, "file", 1), data_edge("issues", "diagram", 3, "issues", 1)])
                project = create_project_api(server, document=document)["project"]
                run = create_run_api(server, document, runtime_mode=mode, project_id=project["graph_id"])
                try:
                    result = wait_for_run_terminal(server, run["run_id"], timeout_sec=45)
                except Exception:
                    print("Runtime failure context:", mode, origin, "\n".join(server.logs[-200:]), flush=True)
                    raise
                assert result["status"] == "success", (mode, origin, result.get("logs"))
                outputs = result["output_values"]
                issues = json.loads(outputs["diagram:3"]["value"])
                assert issues["status"] == "ready", (mode, origin, issues)
                artifact = Path(outputs["diagram:2"]["value"])
                assert artifact.is_relative_to(server.root_dir), artifact
                assert artifact.read_text(encoding="utf-8") == outputs["diagram:1"]["value"]
                assert "<svg" in outputs["diagram:1"]["value"]
                assert result["node_statuses"]["viewer"] == "success", result["node_statuses"]
            print("[ok] Archify runtime " + mode + " " + str(origin), flush=True)


if __name__ == "__main__":
    main()
