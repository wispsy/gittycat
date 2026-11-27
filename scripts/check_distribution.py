"""Qualify selected Gittycat artifacts with the explicitly pinned backend."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, help="Qualify prebuilt release files without rebuilding")
    parser.add_argument("--receipt-file", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profile = json.loads((root / "docs/backend-sources.json").read_text())["gitdx"]
    receipts = []
    with tempfile.TemporaryDirectory() as temporary:
        workspace = Path(temporary)
        dist = args.artifacts.resolve() if args.artifacts else workspace / "dist"
        if not args.artifacts:
            run("uv", "build", "--no-sources", "--out-dir", str(dist), cwd=root)
        artifacts = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
        if len(artifacts) != 2:
            raise RuntimeError("expected exactly one wheel and one sdist")
        for index, artifact in enumerate(artifacts):
            environment = workspace / f"installed-{index}"
            run("uv", "venv", "--python", sys.executable, str(environment))
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            # Resolve the artifact's declared dependency normally. This detects
            # incomplete or incompatible index metadata instead of hiding it.
            run("uv", "pip", "install", "--python", str(python), str(artifact), cwd=workspace)
            smoke = "import gittycat, gitdx, sys, json; from pathlib import Path; from importlib.metadata import version, distribution; "
            smoke += f"assert all(Path(p.__file__).resolve().is_relative_to(Path({str(environment)!r}).resolve()) for p in (gittycat,gitdx)); "
            smoke += f"assert version('gitdx') == {profile['version']!r}; "
            if profile.get('source_kind')=='git':
                smoke += f"assert json.loads(distribution('gitdx').read_text('direct_url.json'))['vcs_info']['commit_id'] == {profile['commit']!r}; "
            smoke += "assert gittycat.Principal().user_id is None; assert 'expose_api' not in sys.modules"
            no_git = dict(os.environ, PATH="")
            run(str(python), "-I", "-c", smoke, cwd=workspace, env=no_git)
            run(str(python), "-I", "-m", "gittycat", "--help", cwd=workspace, env=no_git)
            run(str(python), "-I", "-m", "gittycat", "--version", cwd=workspace, env=no_git)
            run("uv", "pip", "install", "--python", str(python), str(artifact) + "[http]", cwd=workspace)
            run("uv", "pip", "check", "--python", str(python), cwd=workspace)
            run(str(python), "-I", "-W", "error::ResourceWarning", "-m", "unittest", "discover",
                "-s", str(root / "tests"), "-v", cwd=workspace)
            receipts.append({"artifact": artifact.name, "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                             "backend": {"distribution": "gitdx", "version": profile["version"],
                                         "source": profile["commit"]}, "profile": "catalog-auth-management-smart-http-receive/3"})
    if args.receipt_file:
        args.receipt_file.write_text(json.dumps(receipts, indent=2) + "\n")
    print(json.dumps(receipts, indent=2))


if __name__ == "__main__":
    main()
