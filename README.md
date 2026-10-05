# Archify

![Archify](media/cover.png)

Turn **typed Archify JSON** into a standalone interactive diagram. An upstream
Codex, Claude Code or other producer explains the project and authors the JSON;
this block renders it. It does not scan a repository, generate analysis with an
LLM, install a skill or draw the BloxSmith blueprint.

<!-- block-metadata:start -->
[![Block version: 0.1.0](https://img.shields.io/badge/block-0.1.0-blue)](model.json)
[![BloxSmith compatibility: 1.0.9](https://img.shields.io/badge/BloxSmith-1.0.9-brightgreen)](compatibility.json)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Verified BloxSmith versions: **1.0.9** (bundled-block tests; see [test evidence](compatibility.json)).
<!-- block-metadata:end -->

## Setup

On the machine running the block, install Node **18 or newer** and obtain
[official Archify 3.0.1](https://github.com/tt-a1i/archify/tree/v3.0.1), commit
`2ab3cae7ac2c2a55d7386ca789d03c4fcd31816c`. For example, in an operator-chosen directory:

```sh
git clone --depth 1 --branch v3.0.1 https://github.com/tt-a1i/archify.git archify-engine
```

Set **engine_directory** to the checkout's **archify/** child directory, not its
repository root. No npm installation is needed for the checked-in renderers.
The block verifies the exact runtime file hashes in `engine.json`, copies those
verified bytes into private scratch storage, then invokes the typed renderer
directly. A different or modified engine is rejected. Updating this supported
engine is a reviewed block change, not an automatic update at Run.

This initial adapter targets POSIX/Linux (file locks and exclusive publication).
Node is discovered through the runtime's PATH; the operator controls that trusted
executable. Node options and Archify environment variables are not inherited.
The renderer gets a 256 MiB JavaScript heap bound and a cancellable deadline;
this is not an OS memory limit or a hostile-code sandbox.

## Ports

| Port | Direction | Value |
| --- | --- | --- |
| `diagram` | Input | JSON object or strict JSON text using the selected Archify schema |
| `html` | Output | Complete standalone HTML document, on successful rendering only |
| `file_path` | Output | Absolute path to the same completed UTF-8 HTML file |
| `issues` | Output | JSON status (`ready` or `error`), bounded issue list and render provenance |

Connect `html` to **HTML viewer**. Enable that viewer's **allow_scripts** option
for theme, focus, zoom and other interactive controls in its separate preview tab.
The existing viewer opens a blob document, not a security-sandboxed iframe.
Use that option only for trusted generated content; keep scripts disabled if a
static display is preferred. This adapter adds a no-network content security policy
to the document (local scripts/styles and embedded fonts remain allowed), but that
policy is not origin isolation.
A file path is an artifact on the runtime host, not a public HTTP URL.

Schema or rendering failure emits **issues only**, never partial HTML or an error
page on `html`. The task completes with a diagnostic result so an upstream agent
can repair its JSON. Existing valid files are not modified. A downstream consumer
must route on `issues.status`, not infer success merely from task completion.
The issues can quote authored labels/schema values; they are not written to logs.

## JSON contract

Supported types are **architecture**, **workflow**, **sequence**, **dataflow**
and **lifecycle**, with `schema_version: 1` (workflow also accepts version 2). Supply the corresponding
[3.0.1 typed schema](https://github.com/tt-a1i/archify/tree/v3.0.1/archify/schemas).
Unknown properties, malformed relationships and invalid layout are rejected by
the official renderer. This is not a free-form JSON-to-diagram converter.

The [examples](examples/) are official MIT-licensed fixtures suitable for trying
all five renderers. Send the contents of `examples/web-app.architecture.json`
to `diagram` for a first render. Configure the upstream agent to use the same
version and return JSON alone, without Markdown fences.

This offline adapter deliberately rejects **brand**, **sources** and **repository**
properties: upstream can otherwise fetch logos or verify a local Git checkout.
No source evidence is silently erased and no repository validation is claimed.
Generic icons and authored descriptions remain available.
The input's `meta.output` is replaced with a private scratch name; it never
controls a writable server path. The original input's SHA-256 remains in issues.
A configured visual preset overrides `meta.visual_preset`; otherwise it is preserved.
The viewer's light/dark switch is independent of its visual preset.

## Settings and limits

- **diagram_type**: `auto` follows the input; a fixed type requires a match.
- **visual_preset**: keep `input`, or choose `classic`, `signal-flow`,
  `blueprint`, `editorial`.
- **output_directory**: empty uses persistent node storage via the public service.
  Otherwise it must be an existing directory selected in settings. Symlink
  components are refused. Inputs cannot override it.
- **timeout_sec**: 1–120 seconds, default 30, including preparation and rendering.
- **max_input_bytes**: 1 KiB–2 MiB, default 1 MiB. Depth ≤24, 30,000 JSON
  values, 512 items per array, 16,000 characters per string, finite numbers
  within ±1e9. Duplicate textual keys and non-UTF-8 values are refused.
- **max_output_bytes**: 1–16 MiB, default 8 MiB. The official embedded viewer
  includes fonts and normally already occupies roughly 1 MB.
- **max_artifacts**: 1–1,000, default 100, in the selected destination.
  A 256 MiB destination quota also applies to `archify-*.html` files.
  No automatic deletion: archive artifacts explicitly when a quota is reached.

Each accepted input produces a new UUID-named, private-mode HTML artifact.
There is no input deduplication. A destination lock serializes publication, which
is exclusive and flushed before outputs are emitted. Failed private scratch jobs
are removed; completed artifacts survive Run/Stop/Reset according to the framework
storage contract. Keep storage and engine directories operator-controlled.

## Tests

The portable suites use the actual pinned engine, both runtime modes and
bundled/managed/linked origins. UI tests open real modals and inspectors at desktop
and narrow sizes. Set `ARCHIFY_TEST_ENGINE` to the official `archify/` directory
when running the package suites in the private test workspace. Tests require
Node and the workspace browser harness; they neither download dependencies nor
use a live project. Missing engine prerequisites fail explicitly.

## License

The block is Apache-2.0. The separately installed Archify engine and copied JSON
examples are MIT; the examples' license is included. Engine assets retain their
upstream notices and font licenses in generated HTML. This block does not claim
to relicense Archify or third-party marks.
