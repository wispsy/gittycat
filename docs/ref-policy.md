# Branch protection and ref-change policy

Current source adds policy evaluation; the published 0.1.0a1 preview predates
this API. No receive-pack endpoint or ref-writing service is enabled by it.

Administrators store `BranchRule` records with
`catalog.set_branch_rule(actor, rule, expected_version=version, now=timestamp)`.
Use `catalog.branch_policy(actor, repository_id)` to read the revision and
ordered rules, and `delete_branch_rule` with that revision to remove one.
Schema 5 upgrades existing catalogs transactionally. Rule changes, revisions,
audit events and outbox notifications commit or roll back together. The catalog
does not store branch tips or duplicate Git object authority.

A pattern is an exact fully qualified branch/tag name or one trailing `/*`
prefix, for example `refs/heads/main` or `refs/heads/release/*`. At most 128 rules
of at most 1024 characters apply to a repository. All matching rules intersect;
an exact rule cannot relax a prefix rule. Rules restrict creation, deletion,
force updates, the minimum write/administrator permission and required review.
Owners and administrators have no implicit bypass. Required review refuses
direct changes until the checked review/merge workflow exists; a caller cannot
claim approval with a boolean. An unchanged tip remains a permitted no-op after
expected-old and object validation.

`RefPolicyService(catalog, backend).evaluate(actor, repository_id, commands,
now=timestamp)` accepts 1–256 `gitdx.PushCommand` records. The trusted caller must
obtain credential principals from `TokenAuth.request()`; arbitrary identity
records are not authentication. Evaluation refreshes token activity and scope,
repository grants and organization membership before any storage lookup. A
read-only token or public repository does not grant write access.

Decisions include a stable `RefReason`, matching patterns and a fast-forward
predicate where applicable. Every command has an explicit expected old ID
(`None` means create). The engine validates branch target kinds and bounded
complete parent graphs; graph failures and exhausted bounds are refusals.
Tag replacement counts as a force update. Unprotected branches/tags allow
authorized creation, deletion and force updates. Ref aliases and namespaces
outside `refs/heads/` and `refs/tags/` are refused. No configured hooks, drivers
or external executable runs. Full tree/blob connectivity and incoming pack
validation belong to the receive transaction.

`service.recheck(result, now=timestamp)` refreshes authentication, grants,
expected tips and policy. A changed policy revision refuses the old proposal
even if the new rules are less restrictive. Results contain no token secrets.
They are advisory snapshots, not reusable write capabilities: native Git may
change refs between evaluation and use. The future receive service must rerun
policy and expected-old validation inside its publication boundary. A catalog
transaction alone does not lock Git refs or establish cross-store atomicity.
