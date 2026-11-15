"""Build a credential-free container context from qualified package bytes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fixtures", root / "scripts/verify_dependency_fixtures.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def prepare(distribution, output):
    wheels = fixtures.verify(root)
    subprocess.run([sys.executable, str(root / "scripts/verify_wheel.py"), str(distribution)], check=True)
    wheel, = distribution.glob("*.whl")
    base = ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"]
    complete = subprocess.check_output(base + ["--no-hashes"], cwd=root, text=True)
    private = set(re.findall(r"^([a-z0-9-]+) @ git\+", complete, re.M))
    records = json.loads((root / "tests_artifacts/dependencies/manifest.json").read_text())["dependencies"]
    known = {r["distribution"]: r for r in records}
    if not private or private - known.keys():
        raise ValueError("Every runtime Git dependency requires a verified exact-source wheel")
    arguments = base + [arg for name in sorted(private) for arg in ["--no-emit-package", name]]
    public = subprocess.check_output(arguments, cwd=root, text=True)
    if "git+" in public or re.search(r"^\S+ @ ", public, re.M):
        raise ValueError("Runtime public lock cannot contain direct source references")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / "private").mkdir()
    verified = {p.name: p for p in wheels}
    for name in sorted(private):
        source = verified[known[name]["wheel"]]
        shutil.copyfile(source, output / "private" / source.name)
    shutil.copyfile(wheel, output / wheel.name)
    shutil.copyfile(root / "deploy/Dockerfile.runtime", output / "Dockerfile")
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(root / name, output / name)
    for name in ("prepare_notices.py", "verify_notices.py"):
        shutil.copyfile(root / "scripts" / name, output / name)
    shutil.copytree(root / ".github/workflows/third-party-notices", output / "notice-supplements")
    (output / "requirements.txt").write_text(public)
    subprocess.run([
        sys.executable, str(root / "scripts/prepare-storage-schema.py"),
        str(root / "tests_artifacts/storage-schema"), str(output / "storage-schema"),
    ], check=True)
    subprocess.run([
        sys.executable, str(root / "scripts/prepare_sources.py"), "fetch",
        str(root / "licenses/sources.json"), "--output", str(output / "sources"),
    ], check=True)
    shutil.copytree(root / ".github/workflows/source-provenance", output / "sources/provenance")
    shutil.copyfile(root / "licenses/SOURCES.md", output / "sources/SOURCES.md")
    shutil.copyfile(root / "scripts/prepare_sources.py", output / "prepare_sources.py")
    subprocess.run(["bash", str(root / "scripts/scan-source-bundle.sh"), str(output / "sources")], check=True)
    inventory = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(output.rglob("*")) if p.is_file()}
    (output / "inventory.json").write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
