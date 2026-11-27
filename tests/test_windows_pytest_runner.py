from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "tools" / "run_windows_pytest.py"
    spec = importlib.util.spec_from_file_location("run_windows_pytest", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_discovery_finds_test_modules_deterministically(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_z.py").touch()
    (tmp_path / "tests" / "helper.py").touch()
    (tmp_path / "tests" / "test_a.py").touch()
    assert [p.name for p in _module().discover_files([str(tmp_path / "tests")])] == [
        "test_a.py", "test_z.py"]


def test_group_timeout_is_reported_for_parent_runner(monkeypatch):
    from subprocess import TimeoutExpired
    module = _module()
    monkeypatch.setattr(module.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(TimeoutExpired("pytest", 2)))
    result = module.run_group(["pytest"], cwd=Path.cwd(), env={}, hard_timeout=2)
    assert result[0] == 124
    assert "2s hard ceiling" in result[1]


def test_main_continues_after_one_isolated_module_fails(tmp_path, monkeypatch, capsys):
    runner = _module()
    (tmp_path / "test_a.py").touch()
    (tmp_path / "test_b.py").touch()
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[-1])
        return type("Result", (), {"returncode": 1 if command[-1] == "test_a.py" else 0,
                                    "stdout": "1 failed" if command[-1] == "test_a.py" else "1 passed"})()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main(["test_a.py", "test_b.py"]) == 1
    assert calls == ["test_a.py", "test_b.py"]
    output = capsys.readouterr().out
    assert "[FAIL 1] test_a.py" in output and "[PASS] test_b.py" in output


def test_main_reports_isolated_collection_timeout_and_continues(tmp_path, monkeypatch, capsys):
    from subprocess import TimeoutExpired
    runner = _module()
    (tmp_path / "test_a.py").touch()
    (tmp_path / "test_b.py").touch()
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        label = command[-1]
        calls.append(label)
        if "--collect-only" in command:
            raise TimeoutExpired(command, kwargs["timeout"], output=b"collecting")
        return type("Result", (), {"returncode": 0, "stdout": "1 passed"})()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.main(["--isolate-file", "test_a.py", "test_a.py", "test_b.py"]) == 1
    assert calls == ["test_a.py", "test_b.py"]
    output = capsys.readouterr().out
    assert "[FAIL 124] test_a.py (collection)" in output
    assert "pytest collection exceeded 600s hard ceiling" in output
    assert "[PASS] test_b.py" in output
