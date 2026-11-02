import pytest


@pytest.fixture(autouse=True)
def isolated_spend_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SINGIT_HOME", str(tmp_path / "singit-home"))
