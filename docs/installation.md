# Qualified installation profile

Developer preview 0.1.0a2 selects published `gitdx==0.1.0a2` in `pyproject.toml`
and `uv.lock`. [backend-sources.json](backend-sources.json) records the published
version, source, release evidence and exact artifact hashes. Installed acceptance
requires the selected version from a package-index installation and refuses
direct Git/archive installs. The receipt's source commit is linked through the
published release manifest; it is not inferred from PEP 610 metadata.
Use `uv sync --locked` for development; no absolute sibling paths are resolved.
This is a developer preview; production qualification is unfinished.

Managed bare storage requires native SHA-1/SHA-256 init/open and the capabilities
`objects.read`, `objects.write`, `refs.read`, `refs.update`. `RepositoryService`
checks format, bare status and all required capabilities after creation and on
every open. Mismatches raise `BackendMismatch`; a failed create stays pending
and hidden for authorized recovery, rather than advertising an unusable backend.
Managed mutation capabilities require files refs; externally migrated
reftable storage is refused instead of advertising a qualified writer.
The selected engine's independent evidence and capability limits still apply.

`scripts/check_distribution.py` builds Gittycat wheel/sdist artifacts and
installs each into a clean environment outside the source tree, resolving its
declared package-index Gitdx dependency normally and verifies its selected
version and index provenance. `--artifacts` selects prebuilt files for
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
Gitdx is installed from the declared exact published version.

CI runs source tests on Linux/macOS/Windows, plus independent artifact checks
on all three platforms. The trusted local directory and durability limits in
[provisioning](provisioning.md) still apply. No external service starts during
import, installation or qualification.

The optional HTTP profile installs the declared `http` extra only after proving
core package imports do not load Expose API. The complete loopback HTTP suite
then runs against the installed artifact pair. The selected framework source,
version and release evidence are recorded alongside the backend; wheel hashes
remain in `uv.lock`. See [management API](management-api.md) and
[smart HTTP](smart-http.md). The paired 0.1.0a2 preview follows the engine's
publication and repeats installed acceptance against its actual PyPI package.
`check_release.py` refuses direct source dependencies in release metadata.

Hosted writes select `IngestionProfile.UNTRUSTED` for every request and require
native SHA-256 plus the engine's qualified Linux descriptor/allocation profile.
Linux suites run the actual public profile. Other POSIX fixtures explicitly
exercise descriptor mechanisms without enabling that public platform profile;
Windows qualifies early refusal. Both hashes retain trusted-local provisioning
and read serving. The receive suite checks real stock-Git initial/subsequent
pushes, policy/credential races, unknown outcomes and prepared/committed recovery.
