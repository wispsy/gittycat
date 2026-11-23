"""Build and exercise wheel/sdist installations outside the source checkout."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


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
        for i, artifact in enumerate(artifacts):
            venv = workspace / f"installed-{i}"
            run("uv", "venv", "--python", sys.executable, str(venv))
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run("uv", "pip", "install", "--python", str(python), str(artifact))
            smoke = "import gittycat; from pathlib import Path; "
            smoke += f"assert Path(gittycat.__file__).resolve().is_relative_to(Path({str(venv)!r}).resolve()); "
            smoke += "assert gittycat.Principal().user_id is None"
            no_git = dict(os.environ, PATH="")
            run(str(python), "-I", "-c", smoke, cwd=workspace, env=no_git)
            run(str(python), "-I", "-m", "gittycat", "--help", cwd=workspace, env=no_git)
            run(str(python), "-I", "-W", "error::ResourceWarning", "-m", "unittest", "discover",
                "-s", str(root / "tests"), "-v", cwd=workspace)


if __name__ == "__main__":
    main()
