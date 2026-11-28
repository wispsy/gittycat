# Migration to Gitcx

Gitcx is the renamed Python project previously published as `gittycat`. The
repository is now [wispsy/gitcx](https://github.com/wispsy/gitcx). This migration
preview uses **0.1.0a3** so the existing v0.1.0a1/v0.1.0a2 tags stay immutable.
It is a developer preview; production qualification is unfinished. Hosted writes
remain SHA-256-only on qualified Linux storage.

For an existing Python environment, replace the old distribution:

```sh
python -m pip uninstall gittycat
python -m pip install "gitcx[http]==0.1.0a3"
gitcx --version
```

Core-only installations can select `gitcx==0.1.0a3`. Change Python imports from
`gittycat` to `gitcx`, module execution to `python -m gitcx`, and shell/service
commands to `gitcx`. The old Python import/CLI is not installed by this package.
The unrelated npm/npx command remains independently owned.

Update a local clone's origin and directory:

```sh
git remote set-url origin https://github.com/wispsy/gitcx.git
```

The local project directory can be renamed to `gitcx`. Recreate or reinstall its
editable environment with `uv sync --locked --all-extras` after moving it;
virtual-environment entry points and editable imports contain absolute paths.
Test jobs use `GITCX_REQUIRE_GIT` and `GITCX_REQUIRE_HTTP`.

Continue serving the same `--data-root`. Existing `catalog.sqlite3`, SQLite
application ID/schema 6, opaque repository storage IDs, `gittycat-owner` ownership
receipts, Git refs/objects and `gtc_` credentials retain their existing formats.
Catalogs opened explicitly through Python keep their existing filesystem paths.
The namespace migration does not require rebuilding repositories or issuing new
tokens. Stop the old server before starting `gitcx` against the same data root.

Gitdx remains pinned to published `gitdx==0.1.0a2`; the optional HTTP extra keeps
Expose API 0.2.0. The release uses `release.yaml`, the `pypi` environment and the
Trusted Publisher for `wispsy/gitcx`. Earlier `gittycat` PyPI distributions and
GitHub tags retain their original package contents.
