"""Build and exercise wheel/sdist installations outside the source checkout."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import urllib.request


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        dist = workspace / "dist"
        run("uv", "build", "--no-sources", "--out-dir", str(dist), cwd=root)
        artifacts = list(dist.glob("*.whl")) + list(dist.glob("*.tar.gz"))
        if len(artifacts) != 2:
            raise RuntimeError("expected exactly one wheel and one sdist")
        profile = json.loads((root/"docs/backend-sources.json").read_text())["gitdx"]
        # Build the selected immutable backend independently, never the sibling
        # working tree or an unrelated package-index project.
        archive = workspace/"backend.tar.gz"
        with urllib.request.urlopen("https://codeload.github.com/wispsy/gitdx/tar.gz/"+profile["commit"], timeout=30) as response:
            data = response.read(32*1024*1024+1)
        if len(data)>32*1024*1024:raise RuntimeError("backend source archive exceeds its limit")
        archive.write_bytes(data)
        source = workspace/"backend-source";source.mkdir()
        with tarfile.open(archive) as bundle:bundle.extractall(source, filter="data")
        sources = list(source.iterdir())
        if len(sources)!=1:raise RuntimeError("unexpected backend archive layout")
        backend_dist = workspace/"backend-dist"
        run("uv", "build", "--no-sources", "--out-dir", str(backend_dist), cwd=sources[0])
        backend_artifacts = list(backend_dist.glob("*.whl"))+list(backend_dist.glob("*.tar.gz"))
        if len(backend_artifacts)!=2:raise RuntimeError("expected backend wheel and sdist")
        for i, artifact in enumerate(artifacts):
            venv = workspace / f"installed-{i}"
            run("uv", "venv", "--python", sys.executable, str(venv))
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run("uv", "pip", "install", "--python", str(python), str(backend_artifacts[i]))
            run("uv", "pip", "install", "--no-deps", "--python", str(python), str(artifact))
            smoke = "import gittycat, gitdx; from pathlib import Path; "
            smoke += f"assert all(Path(p.__file__).resolve().is_relative_to(Path({str(venv)!r}).resolve()) for p in (gittycat,gitdx)); "
            smoke += "assert gittycat.Principal().user_id is None"
            no_git = dict(os.environ, PATH="")
            run(str(python), "-I", "-c", smoke, cwd=workspace, env=no_git)
            run(str(python), "-I", "-m", "gittycat", "--help", cwd=workspace, env=no_git)
            run(str(python), "-I", "-W", "error::ResourceWarning", "-m", "unittest", "discover",
                "-s", str(root / "tests"), "-v", cwd=workspace)


if __name__ == "__main__":
    main()
