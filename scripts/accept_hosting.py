"""Repeatable local stock-Git acceptance; all state and credentials are ephemeral."""

import argparse
import asyncio
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from expose_api import serve
from gitdx import Blob, Commit, Repository, Tree, TreeEntry
import gitdx.confined_fs as storage_profile
from gittycat import (AccessGrant, Action, BranchRule, Catalog, GitdxBackend, Owner, Permission,
                     Principal, RepositoryRecord, RepositoryService, Slug, StorageId, TokenAuth,
                     Visibility, create_app)


NOW = 1_700_000_000
IDENTITY = b'Acceptance <acceptance@example.invalid> 1700000000 +0000'


def native(path, *arguments, token=None):
    # Never inherit tracing, credential helpers, configuration injection or
    # proxies. A selected token exists only in this client's environment.
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')
                   and k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy')}
    settings = [('protocol.version', '2'), ('credential.helper', ''), ('credential.interactive', 'false'),
                ('commit.gpgsign', 'false'), ('tag.gpgsign', 'false'), ('core.autocrlf', 'false')]
    if token is not None: settings.append(('http.extraHeader', 'Authorization: Bearer '+token))
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
        GIT_CONFIG_COUNT=str(len(settings)), GIT_AUTHOR_NAME='Acceptance', GIT_AUTHOR_EMAIL='acceptance@example.invalid',
        GIT_COMMITTER_NAME='Acceptance', GIT_COMMITTER_EMAIL='acceptance@example.invalid',
        GIT_AUTHOR_DATE='1700000000 +0000', GIT_COMMITTER_DATE='1700000000 +0000')
    for i, (key, value) in enumerate(settings):
        environment['GIT_CONFIG_KEY_'+str(i)] = key
        environment['GIT_CONFIG_VALUE_'+str(i)] = value
    return subprocess.run(['git', '-C', str(path), *arguments], capture_output=True, env=environment,
                          check=True, timeout=30).stdout


def fixture_commit(repo, data, parents=()):
    blob = repo.objects.write(Blob(data).to_raw())
    tree = repo.objects.write(Tree((TreeEntry(0o100644, b'file', blob),), repo.object_format).to_raw())
    return repo.objects.write(Commit.create(tree, author=IDENTITY, committer=IDENTITY,
        message=b'read-only fixture\n', parents=parents).to_raw())


def identities():
    engine = metadata.distribution('gitdx')
    direct = engine.read_text('direct_url.json')
    profile = json.loads((Path(__file__).resolve().parents[1]/'docs/backend-sources.json').read_text())
    backend = profile['gitdx']
    if engine.version != backend['version']:
        raise RuntimeError('installed engine version differs from the selected acceptance profile')
    if backend.get('source_kind') == 'git':
        selected = json.loads(direct) if direct else {}
        actual = selected.get('vcs_info', {}).get('commit_id')
        if actual != backend['commit']:
            raise RuntimeError('installed engine does not match the immutable acceptance profile')
        provenance = 'pep610-commit'
    elif backend.get('source_kind') == 'pypi':
        if direct is not None:
            raise RuntimeError('installed engine must resolve from the selected package index')
        actual = backend['commit']
        provenance = 'published-release-manifest'
    else:
        raise RuntimeError('unsupported engine source in the selected acceptance profile')
    if metadata.version('expose-api') != profile['expose_api']['version']:
        raise RuntimeError('installed HTTP framework differs from the selected profile')
    return {'gitdx': {'version': engine.version, 'commit': actual,
                      'source_kind': backend['source_kind'], 'provenance': provenance},
            'expose_api': {'version': metadata.version('expose-api'), 'source': profile['expose_api']['commit']},
            'gittycat': {'version': metadata.version('gittycat')}}


async def run_acceptance(*, read_only=False):
    if not read_only and not storage_profile.supported():
        raise RuntimeError('full hosted acceptance requires the qualified Linux profile; select read_only explicitly elsewhere')
    selected = identities();checks = []
    with tempfile.TemporaryDirectory(prefix='gittycat-acceptance-') as tmp:
        root = Path(tmp);data = root/'data';database = data/'catalog.sqlite3';backend = GitdxBackend(data/'git')
        alice, bob, outsider = (uuid.uuid4().hex for _ in range(3));tokens = {}
        with Catalog(database) as catalog:
            for user, slug in ((alice, 'alice'), (bob, 'bob'), (outsider, 'outsider')):
                catalog.create_account(user, Slug(slug), now=NOW)
                tokens[user] = TokenAuth(catalog).issue(Principal(user), scopes={Action.ADMIN}, expires_at=NOW+3600, now=NOW).reveal()
            records = []
            for format in ('sha1', 'sha256'):
                record = RepositoryRecord(uuid.uuid4().hex, Owner(alice, 'user'), Slug('native-'+format),
                    StorageId.new(), Visibility.PRIVATE, format)
                RepositoryService(catalog, backend).create(Principal(alice), record, now=NOW+1)
                catalog.grant(Principal(alice), AccessGrant(record.id, bob, Permission.WRITE), now=NOW+2)
                records.append(record)
        app = create_app(data, clock=lambda: NOW+10)

        async def git(path, *args, token=None):
            return await asyncio.to_thread(native, path, *args, token=token)

        async def denied(path, *args, token=None):
            try: await git(path, *args, token=token)
            except subprocess.CalledProcessError: return
            raise AssertionError('unauthorized or protected native operation was accepted')

        clients = {}
        async with serve(app, host='127.0.0.1', port=0, request_timeout=30) as server:
            host, port = server.sockets[0].getsockname()[:2]
            def url(record): return f'http://{host}:{port}/git/{record.id}.git'
            for record in records:
                repo = backend.open_repository(record.storage_id)
                if record.object_format == 'sha1' or read_only:
                    tip = fixture_commit(repo, b'trusted local fixture\n')
                    repo.refs.update('refs/heads/main', tip, expected=None)
                    repo.refs.update('refs/heads/feature', tip, expected=None)
                alice_path = root/(record.object_format+'-alice')
                await git(root, 'clone', url(record), str(alice_path), token=tokens[alice])
                if record.object_format == 'sha256' and not read_only:
                    (alice_path/'file').write_bytes(b'main from Alice\n')
                    await git(alice_path, 'add', 'file');await git(alice_path, 'commit', '-m', 'Create main')
                    await git(alice_path, 'push', 'origin', 'main', token=tokens[alice])
                    await git(alice_path, 'switch', '-c', 'feature')
                    (alice_path/'feature').write_bytes(b'feature from Alice\n')
                    await git(alice_path, 'add', 'feature');await git(alice_path, 'commit', '-m', 'Add feature')
                    await git(alice_path, 'push', 'origin', 'feature', token=tokens[alice])
                    checks.extend(('empty-sha256-clone', 'native-commit-push-main', 'native-feature-push'))
                bob_path = root/(record.object_format+'-bob')
                await git(root, 'clone', url(record), str(bob_path), token=tokens[bob])
                await git(bob_path, 'fetch', 'origin', 'feature:refs/heads/feature', token=tokens[bob])
                expected = repo.refs.resolve('refs/heads/feature').oid.hex
                if (await git(bob_path, 'rev-parse', 'feature')).strip().decode() != expected:
                    raise AssertionError('second authorized client fetched another tip')
                await denied(root, 'ls-remote', url(record), token=tokens[outsider])
                await denied(root, 'clone', url(record), str(root/(record.object_format+'-outsider')), token=tokens[outsider])
                if record.object_format == 'sha1':
                    await denied(alice_path, 'push', 'origin', 'HEAD:refs/heads/refused', token=tokens[alice])
                    if repo.refs.read('refs/heads/refused') is not None: raise AssertionError('SHA-1 hosted write was accepted')
                    checks.append('sha1-write-refusal')
                await git(repo.git_dir, 'fsck', '--strict', '--no-dangling')
                clients[record.id] = (alice_path, bob_path)
                checks.append(record.object_format+'-authorized-fetch-and-outsider-concealment')
        # A new app/backend/catalog lifecycle uses the same durable state. The
        # listener remains loopback-only and may receive another ephemeral port.
        app = create_app(data, clock=lambda: NOW+20)
        async with serve(app, host='127.0.0.1', port=0, request_timeout=30) as server:
            host, port = server.sockets[0].getsockname()[:2]
            for record in records:
                alice_path, bob_path = clients[record.id]
                address = f'http://{host}:{port}/git/{record.id}.git'
                for client in (alice_path, bob_path): await git(client, 'remote', 'set-url', 'origin', address)
                repo = backend.open_repository(record.storage_id)
                if record.object_format == 'sha256' and not read_only:
                    (alice_path/'feature').write_bytes(b'feature after restart\n')
                    await git(alice_path, 'add', 'feature');await git(alice_path, 'commit', '-m', 'Continue feature')
                    await git(alice_path, 'push', 'origin', 'feature', token=tokens[alice])
                    with Catalog(database) as catalog:
                        catalog.set_branch_rule(Principal(alice), BranchRule(record.id, 'refs/heads/main', require_review=True), expected_version=0, now=NOW+20)
                    original = repo.refs.resolve('refs/heads/main').oid
                    await denied(alice_path, 'push', 'origin', 'feature:main', token=tokens[alice])
                    await denied(alice_path, 'push', 'origin', 'feature:refs/heads/outsider', token=tokens[outsider])
                    if repo.refs.resolve('refs/heads/main').oid != original or repo.refs.read('refs/heads/outsider') is not None:
                        raise AssertionError('failed pushes changed protected refs')
                    checks.extend(('native-push-after-restart', 'protected-and-outsider-push-refusal'))
                await git(bob_path, 'fetch', 'origin', 'feature:refs/heads/feature', token=tokens[bob])
                if (await git(bob_path, 'rev-parse', 'feature')).strip().decode() != repo.refs.resolve('refs/heads/feature').oid.hex:
                    raise AssertionError('second user did not fetch the durable feature tip')
                for client in (alice_path, bob_path):
                    await git(client, 'fsck', '--strict', '--no-dangling')
                    config = (client/'.git/config').read_text()
                    if any(token in config for token in tokens.values()): raise AssertionError('credential persisted in client config')
                checks.append(record.object_format+'-restart-fetch-fsck')
        with Catalog(database) as catalog:
            for record in records:
                events = catalog.repository_events(Principal(alice), record.id)
                if record.object_format == 'sha256' and not read_only and not any(e.kind.value == 'receive.accepted' for e in events):
                    raise AssertionError('accepted push audit did not survive restart')
            if any(token.encode() in database.read_bytes() for token in tokens.values()):
                raise AssertionError('raw credential persisted in catalog')
        checks.extend(('credential-non-persistence', 'durable-catalog-audit'))
        return {'schema_version': 1, 'profile': 'read-only-native' if read_only else 'hosted-sha256-linux',
                'packages': selected, 'git': (await git(root, '--version')).decode().strip(),
                'checks': checks, 'limits': {'request_bytes': 16*1024*1024, 'commands': 128, 'engine_seconds': 30},
                'unsupported': ['untrusted-sha1', 'unqualified-platform-writes', 'atomic-files-ref-visibility',
                                'global-quotas', 'public-deployment']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only', action='store_true', help='Qualify both read formats and SHA-1 write refusal without enabling hosted writes')
    parser.add_argument('--output', type=Path, help='Write a secret-free JSON acceptance receipt')
    args = parser.parse_args()
    if not args.read_only and not storage_profile.supported():
        parser.error('full acceptance requires qualified Linux storage; use --read-only on this platform')
    result = asyncio.run(run_acceptance(read_only=args.read_only))
    encoded = json.dumps(result, indent=2)+'\n'
    if args.output: args.output.write_text(encoded)
    print(encoded, end='')


if __name__ == '__main__': main()
