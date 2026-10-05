"""Archify JSON-to-HTML adapter using only the public block API."""

import json
from bloxsmith_app.block_api import APPLICATION_JSON, TEXT_PLAIN, BlockDefinition, BlockRuntimeOutput, BlockRuntimeResult
from . import logic, ui


# FB1 - Render five typed diagrams with the actual pinned official engine.
# FB2 - Emit complete unique HTML/file outputs only; preserve earlier valid artifacts on failure.
# FB3 - Bound JSON, processes, storage and diagnostics; reject implicit external access and engine drift.
# FB4 - Execute in both runtimes and all package origins using node-scoped storage.
# FB5 - Offer translated responsive modal/inspector settings with explicit Apply and draft preservation.
class ArchifyBlock(BlockDefinition):
    """Rendering only: upstream agents remain responsible for project analysis."""

    kind = "archify"

    def ui_assets(self, surface="modal"):
        """Use only release-declared assets."""
        return list(self.model.get("ui_assets", {}).get(surface, []))

    def execute_runtime(self, context):
        """Return actionable issues instead of replacing the last valid diagram with an error page."""
        try:
            logic.configuration(context.config)
            storage = context.services.get("get_block_storage_dir")
            if not callable(storage):
                raise logic.ArchifyError("storage", "Instance-scoped block storage is required.")
            values = logic.process(context.input_value("diagram", "1"), context.config, context.root_dir, storage(),
                                   context.services.get("cancel_requested", lambda: False))
        except (ValueError, TypeError, OSError, RuntimeError, RecursionError, UnicodeError) as exc:
            detail = str(exc) if isinstance(exc, logic.ArchifyError) else "Rendering or persistent storage is unavailable."
            items = exc.issues if isinstance(exc, logic.ArchifyError) else [{"code": "render_unavailable", "severity": "error", "message": detail}]
            values = {"issues": {"status": "error", "items": items, "engine_version": logic.ENGINE["version"]}}
        types = {"html": "text/html", "file_path": "file/path", "issues": APPLICATION_JSON}
        outputs = [BlockRuntimeOutput(port_id=int(port.id), port_name=port.name,
                   value=json.dumps(values[port.name], ensure_ascii=False) if port.name == "issues" else values[port.name],
                   content_type=types[port.name]) for port in context.output_ports if port.name in values]
        ready = values["issues"]["status"] == "ready"
        return BlockRuntimeResult(status="success", outputs=outputs, content_type=TEXT_PLAIN,
            metadata={self.kind: {"status": values["issues"]["status"], "engine_version": logic.ENGINE["version"]}},
            logs=["[archify] Diagram ready." if ready else "[archify] No diagram published; inspect the issues output."])

    def handle_ui_action(self, *, node, action, values, payload=None):
        """Validate configuration without opening a path or starting Node."""
        if action in {"save_settings", "modal_update_fields", "inspector_update_fields"}:
            patch = (values or {}).get("node_patch") or {}
            if "config" in patch:
                try:
                    logic.configuration({**self.default_config(), **(node.get("config") or {}), **patch["config"]})
                except (ValueError, TypeError) as exc:
                    return {"error": self.translate("block.archify.error", {"detail": str(exc)}, fallback="Archify: {detail}")}
        return ui.replace_config(super().handle_ui_action(node=node,
            action="modal_update_fields" if action == "save_settings" else action, values=values, payload=payload), node)

    def render_modal(self, *, node, payload=None):
        """Opaque, internally scrollable modal with reachable footer actions."""
        return ui.modal(self, node, payload)

    def render_inspector_panel(self, *, node, payload=None):
        """Same settings in a narrow inspector with standard port controls."""
        return ui.inspector(self, node, payload)

    def render_node_card(self, *, node, payload=None):
        """Show the selected type and fixed engine version, never input content."""
        return ui.card(self, node, "Archify 3.0.1 · " + str((node.get("config") or {}).get("diagram_type", "auto")))
