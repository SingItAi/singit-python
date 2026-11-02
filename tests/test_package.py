from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def test_publishable_manifest():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "singit"
    assert project["version"] == "0.1.1"
    assert not any(c.startswith("Private ::") for c in project["classifiers"])
    assert project["readme"] == "README.md"
    assert project["license-files"] == ["LICENSE"]


def test_wheel_contains_spend_modules(tmp_path):
    import importlib.util
    import subprocess
    import sys
    import zipfile
    import pytest
    if importlib.util.find_spec("hatchling") is None:
        pytest.skip("hatchling is not installed; wheel build requires the build backend")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(tmp_path)], cwd=root, check=True, capture_output=True)
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        for name in ("spend.py", "spend_cli.py", "cli.py", "__main__.py"):
            assert "singit/" + name in wheel.namelist()
        entry = next(n for n in wheel.namelist() if n.endswith("entry_points.txt"))
        assert "singit = singit.cli:main" in wheel.read(entry).decode()
