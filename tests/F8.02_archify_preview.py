"""FB1/FB2/FB4: actual downstream HTML preview, interactive controls and escaped labels."""

import copy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from playwright.sync_api import sync_playwright, expect
from blocs.archify.block import ArchifyBlock
from blocs.html_display.block import HtmlDisplayBlock
from block_test_artifacts import artifact_path
from block_test_packages import install_test_package
from ui_smoke_common import create_project_api, create_run_api, data_edge, graph_payload, isolated_server, project_editor_url, text_node, wait_for_run_terminal


def main():
    """Render once through Active Runtime and open the existing viewer's real preview tab."""
    engine = Path(os.environ["ARCHIFY_TEST_ENGINE"]).resolve()
    fixture = json.loads((Path(__file__).resolve().parents[1] / "examples" / "web-app.architecture.json").read_text(encoding="utf-8"))
    fixture["cards"][0]["items"].append('<script>window.injectedByLabel=true</script>')
    with isolated_server() as server, sync_playwright() as playwright:
        archify = ArchifyBlock()
        node = archify.build_node_payload(node_id="diagram", position={"x": 340, "y": 100})
        model = install_test_package(server, "archify", origin="managed")
        node["block_version"] = model["version"]
        node["config"] = {**archify.default_config(), "engine_directory": str(engine)}
        viewer = HtmlDisplayBlock().build_node_payload(node_id="viewer", position={"x": 620, "y": 100})
        viewer_model = install_test_package(server, "html_display", origin="managed")
        viewer["block_version"] = viewer_model["version"]
        viewer["config"] = {"allow_scripts": True}
        document = graph_payload("Archify preview", [text_node("source", "JSON", json.dumps(fixture), 40, 100), node, viewer], [
            data_edge("input", "source", 1, "diagram", 1), data_edge("html", "diagram", 1, "viewer", 1)])
        project = create_project_api(server, document=document)["project"]
        run = create_run_api(server, document, runtime_mode="zeromq_active", project_id=project["graph_id"])
        result = wait_for_run_terminal(server, run["run_id"], timeout_sec=45)
        assert result["status"] == "success", result.get("logs")
        assert json.loads(result["output_values"]["diagram:3"]["value"])["status"] == "ready"
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            page.goto(project_editor_url(server.base_url, project["project_id"], workspace_project_id=project["workspace_project_id"]))
            page.locator('.canvas-node[data-node-id="viewer"] h3').dblclick()
            button = page.locator('[data-html-display-open-preview]')
            expect(button).to_be_enabled()
            external = []
            context.on("request", lambda request: external.append(request.url) if request.url.startswith(("http:", "https:")) and not request.url.startswith(server.base_url) else None)
            with context.expect_page() as opened:
                button.click()
            preview = opened.value
            preview.wait_for_load_state()
            expect(preview.locator("#btn-theme")).to_be_visible()
            assert preview.locator("svg [data-node-id]").count() >= 10
            assert preview.evaluate("!window.injectedByLabel")
            theme = preview.locator("html").get_attribute("data-theme")
            preview.locator("#btn-theme").click()
            expect(preview.locator("html")).not_to_have_attribute("data-theme", theme)
            assert preview.locator('meta[http-equiv="Content-Security-Policy"]').count() == 1
            preview.screenshot(path=artifact_path("archify-rendered-preview.png"), full_page=True)
            assert not external, external
        finally:
            browser.close()
    print("[ok] Archify actual HTML viewer preview, theme control, escaped labels and no remote assets")


if __name__ == "__main__":
    main()
