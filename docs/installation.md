# Qualified installation profile

The mandatory backend is the published `gitdx==0.1.0a1` distribution selected in
`pyproject.toml` and `uv.lock`. [backend-sources.json](backend-sources.json) records
the corresponding immutable source revision and release evidence.
Use `uv sync --locked` for development; no absolute sibling paths are resolved.
Package/release publication remains a separate owner-selected action.

Managed bare storage requires native SHA-1/SHA-256 init/open and the capabilities
`objects.read`, `objects.write`, `refs.read`, `refs.update`. `RepositoryService`
checks format, bare status and all required capabilities after creation and on
every open. Mismatches raise `BackendMismatch`; a failed create stays pending
and hidden for authorized recovery, rather than advertising an unusable backend.
This profile does not imply transport, reftable, hosting hardening or newer
engine features beyond the selected revision's independent evidence.

`scripts/check_distribution.py` builds Gittycat wheel/sdist artifacts and
installs each into a clean environment outside the source tree, resolving its
published Gitdx dependency normally. `--artifacts` selects prebuilt files for
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
Gitdx is installed from its published distribution.

CI runs source tests on Linux/macOS/Windows, plus independent artifact checks
on all three platforms. The trusted local directory and durability limits in
[provisioning](provisioning.md) still apply. No external service starts during
import, installation or qualification.

The optional HTTP profile installs the declared `http` extra only after proving
core package imports do not load Expose API. The complete loopback HTTP suite
then runs against the installed artifact pair. The selected framework source,
version and release evidence are recorded alongside the backend; wheel hashes
remain in `uv.lock`. See [management API](management-api.md).
