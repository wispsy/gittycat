# Developer previews

## 0.1.0a2

This developer preview selects published `gitdx==0.1.0a2` and Expose API 0.2.0
for the optional HTTP extra. Production qualification is unfinished. Hosted
writes remain SHA-256-only on qualified Linux descriptor storage. Both native
hash formats support trusted local provisioning and authorized read serving.

- SQLite catalog, opaque managed bare storage, scoped expiring/revocable tokens
  and versioned branch/tag policy.
- Authorized discovery/fetch over smart HTTP protocols 0/1/2.
- Bounded untrusted SHA-256 receive, publication-time credential/grant/policy
  checks, durable sanitized receipts and explicit interruption recovery.
- Repeatable two-user native-Git clone/push/fetch, access refusal and restart
  acceptance with temporary state and sanitized provenance receipts.

The wheel/sdist checks resolve the backend from the package index and reject
direct-source installations. [backend-sources.json](backend-sources.json)
records the selected published source, qualification and exact artifact hashes.
Installed version checks establish the package selection; the receipt's source
commit is linked through the published release manifest.

Linux runs the hosted-write acceptance profile. Other platforms explicitly run
read/fetch/restart acceptance and preserve hosted-write refusal. Each release
artifact must pass installed qualification before the publication workflow
uploads it. See [installation](installation.md) and
[hosting acceptance](hosting-acceptance.md) for profile-specific evidence.

Repository browsing, collaboration/reviews and durable jobs can follow this
preview. Untrusted SHA-1, other-platform hosted writes, atomic files-ref
visibility, global quotas, complete catalog/Git backup and public deployment
remain outside the selected scope. TLS/proxy and production operations require
further qualification.

## 0.1.0a1

Initial catalog/authentication/management preview paired with Gitdx 0.1.0a1.
The immutable [release](https://github.com/wispsy/gittycat/releases/tag/v0.1.0a1)
retains its original artifact history.
