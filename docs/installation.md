# Qualified installation profile

The published preview pairs with `gitdx==0.1.0a1`. Current source needs the newer
upload-pack API and selects an immutable Gitdx Git revision in `pyproject.toml`
and `uv.lock`. [backend-sources.json](backend-sources.json) records this selection
and retains the published version, source, release evidence and artifact hashes
under `published`. The package version alone does not identify the development
backend; its PEP 610 source commit must match.
Use `uv sync --locked` for development; no absolute sibling paths are resolved.
Package/release publication remains a separate owner-selected action.

Managed bare storage requires native SHA-1/SHA-256 init/open and the capabilities
`objects.read`, `objects.write`, `refs.read`, `refs.update`. `RepositoryService`
checks format, bare status and all required capabilities after creation and on
every open. Mismatches raise `BackendMismatch`; a failed create stays pending
and hidden for authorized recovery, rather than advertising an unusable backend.
Managed mutation capabilities require files refs; externally migrated read-only
reftable storage is refused instead of advertising a qualified writer.
This profile does not imply transport, reftable, hosting hardening or newer
engine features beyond the selected revision's independent evidence.

`scripts/check_distribution.py` builds Gittycat wheel/sdist artifacts and
installs each into a clean environment outside the source tree, resolving its
declared Gitdx source dependency normally and verifies its exact installed
PEP 610 commit. `--artifacts` selects prebuilt files for
the same checks. Receipts retain artifact hashes and backend version/source.
These are qualification artifacts, not uploads. Imports must resolve inside the
environment; no editable/source-tree imports are accepted.

The installed suites exercise both object formats, backend mismatch refusal,
catalog recovery, token permissions and branch policy. Policy checks include
both native hash formats, rule intersections, expected-old tips, graph limits,
credential revocation and SQLite migration/restart. Capability execution tests block all
subprocess/shell creation and clear PATH while writing/reading objects, updating
refs, creating and inspecting repositories. Package and CLI startup also run
with Git absent from PATH. Native Git is used only by separate oracle tests;
Gitdx is installed from the declared immutable source selection.

CI runs source tests on Linux/macOS/Windows, plus independent artifact checks
on all three platforms. The trusted local directory and durability limits in
[provisioning](provisioning.md) still apply. No external service starts during
import, installation or qualification.

The optional HTTP profile installs the declared `http` extra only after proving
core package imports do not load Expose API. The complete loopback HTTP suite
then runs against the installed artifact pair. The selected framework source,
version and release evidence are recorded alongside the backend; wheel hashes
remain in `uv.lock`. See [management API](management-api.md) and
[smart HTTP](smart-http.md). Before a subsequent PyPI publication, release the
qualified engine version, switch back to its package-index requirement and
repeat installed qualification. `check_release.py` deliberately refuses direct
source dependencies; this development selection does not authorize publication.
