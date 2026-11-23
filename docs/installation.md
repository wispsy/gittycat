# Qualified installation profile

The mandatory backend is the immutable Gitdx revision in `pyproject.toml`,
`uv.lock` and [backend-sources.json](backend-sources.json). It is selected by
repository and commit, independent of any similarly named package-index entry.
Use `uv sync --locked` for development; no absolute sibling paths are resolved.
Package/release publication remains a separate owner-selected action.

Managed bare storage requires native SHA-1/SHA-256 init/open and the capabilities
`objects.read`, `objects.write`, `refs.read`, `refs.update`. `RepositoryService`
checks format, bare status and all required capabilities after creation and on
every open. Mismatches raise `BackendMismatch`; a failed create stays pending
and hidden for authorized recovery, rather than advertising an unusable backend.
This profile does not imply transport, reftable, hosting hardening or newer
engine features beyond the selected revision's independent evidence.

`scripts/check_distribution.py` downloads the selected immutable source archive
from GitHub, builds Gitdx and Gittycat wheel/sdist artifacts independently, and
installs matching artifact pairs into clean environments outside both source
trees. The explicit backend artifacts satisfy the mandatory VCS dependency;
`--no-deps` applies only to the subsequent Gittycat artifact installation so it
cannot replace that selected backend with another source. These are source
qualification artifacts, not releases or uploads. Imports must resolve inside
the environment; no editable/source-tree imports are accepted.

The installed suites exercise both object formats, backend mismatch refusal,
catalog recovery and token permissions. Capability execution tests block all
subprocess/shell creation and clear PATH while writing/reading objects, updating
refs, creating and inspecting repositories. Package and CLI startup also run
with Git absent from PATH. Native Git is used only by separate oracle tests;
building a VCS dependency may require Git during installation.

CI runs source tests on Linux/macOS/Windows, plus independent artifact pairing
on all three platforms. The trusted local directory and durability limits in
[provisioning](provisioning.md) still apply. No external service starts during
import, installation or qualification.

The optional HTTP profile installs the declared `http` extra only after proving
core package imports do not load Expose API. The complete loopback HTTP suite
then runs against the installed artifact pair. The selected framework source,
version and release evidence are recorded alongside the backend; wheel hashes
remain in `uv.lock`. See [management API](management-api.md).
