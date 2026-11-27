# Local two-user hosting acceptance

Install the selected preview profile with `uv sync --locked --all-extras`.
The published Gitdx 0.1.0a2 artifacts, source commit and released Expose API
source/version are recorded in [backend-sources.json](backend-sources.json).
The runner verifies the installed engine version and package-index provenance,
refusing direct Git/archive installations. Its source commit is linked through
the published release manifest. No sibling checkout is selected.

On qualified Linux storage, run:

```sh
uv run --frozen --all-extras python scripts/accept_hosting.py --output /tmp/hosting-acceptance.json
```

The runner creates a temporary catalog, two private repositories, two authorized
accounts and an outsider, and binds only an ephemeral loopback listener. Real
stock Git clients clone an empty SHA-256 repository, create/commit/push main,
push a feature branch, and fetch it as the second user. A new app/catalog/backend
lifecycle on the same durable data accepts another feature push; the second
client fetches the resulting tip. Protected-main and outsider pushes fail while
the checked refs stay unchanged. Outsiders cannot discover or clone private
repositories. Native fsck verifies server and client storage after the workflow.

SHA-1 is qualified as a trusted-local fixture plus read serving and early hosted
write refusal. It is never passed to trusted intake as a fallback. Both formats
also run explicit read/fetch/restart checks. Tokens are created only for the
temporary fixture and injected into native client environments, never command
arguments, URLs or repository config. The catalog stores only verifiers. The
runner checks client configs and database bytes for raw credential persistence,
retains a sanitized JSON receipt, and removes all temporary repositories/accounts.
It does not change a personal repository or start a persistent/public server.

On macOS, Windows or another unqualified write platform, explicitly select:

```sh
uv run --frozen --all-extras python scripts/accept_hosting.py --read-only
```

That receipt says `read-only-native` and contains no hosted-write acceptance
claim. The full runner refuses an unqualified platform. POSIX mechanism tests
can explicitly exercise descriptor code without changing this application gate.
Linux CI runs the actual public untrusted profile; other platforms retain write
refusal and independently qualify both read formats.

Source, wheel and sdist CI retain JSON receipts for 14 days. Receipts identify
the installed engine version and published source provenance, framework source/version, Git version, selected
profile, completed checks, request/command/deadline budgets and unsupported gates.
Installed receipts also bind the Gittycat artifact hash and run outside the
source checkout. The installed runner executes from the qualification bundle
and imports only its selected environment's packages.

This prototype does not qualify untrusted SHA-1, unqualified-platform writes,
atomic files-ref visibility, global quotas or public deployment. Production
TLS/proxy selection, complete catalog/Git backup, destructive retention and
future publication remain separately selected gates. This developer preview
leaves production qualification unfinished. [Hosted receive](receive.md)
documents the per-ref/unknown outcome and recovery contracts.
