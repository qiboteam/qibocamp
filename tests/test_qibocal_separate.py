"""Qibocal in an environment of its own, when the server's interpreter is read-only (#101).

The Nix package sets QIBOCAMP_QIBOCAL_ENVIRONMENT: its Python is in the store,
where nothing can be installed, and cannot import qibocamp by itself.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from qibocamp import qibocal_environment as environment
from qibocamp.reports import generator


@pytest.fixture
def separate(tmp_path, monkeypatch):
    path = tmp_path / "qibocal-env"
    monkeypatch.setenv(environment.ENVIRONMENT_VARIABLE, str(path))
    return path


def test_the_environment_is_the_server_s_own_unless_told(tmp_path, monkeypatch):
    monkeypatch.delenv(environment.ENVIRONMENT_VARIABLE, raising=False)
    assert environment.separate_environment() is None
    assert environment.qibocal_python() == sys.executable
    monkeypatch.setenv(environment.ENVIRONMENT_VARIABLE, "separate")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    minor = f"{sys.version_info[0]}.{sys.version_info[1]}"
    assert environment.separate_environment() == tmp_path / "cache" / "qibocamp" / "envs" / f"qibocal-py{minor}"
    monkeypatch.setenv(environment.ENVIRONMENT_VARIABLE, str(tmp_path / "mine"))
    assert environment.separate_environment() == tmp_path / "mine"


def test_the_environment_is_made_once_and_imports_this_qibocamp(separate):
    python = environment.qibocal_python()
    assert Path(python) == environment._environment_python(separate)
    # The packaged qibocamp, this very one, through the path file.
    answer = subprocess.run(
        [python, "-c", "import qibocamp; print(qibocamp.__file__)"],
        capture_output=True, text=True, check=True,
    )
    import qibocamp

    assert Path(answer.stdout.strip()).resolve() == Path(qibocamp.__file__).resolve()
    # Its own packages first: what is installed there wins over the server's.
    path_file = environment._environment_site_packages(separate) / environment._PATH_FILE
    assert path_file.is_file()
    created = (separate / "pyvenv.cfg").stat().st_mtime_ns
    assert environment.qibocal_python() == python
    assert (separate / "pyvenv.cfg").stat().st_mtime_ns == created, "not made again"


def test_a_changed_interpreter_upgrades_the_environment_and_keeps_its_packages(separate, monkeypatch):
    environment.qibocal_python()
    configuration = separate / "pyvenv.cfg"
    configuration.write_text(
        "\n".join(
            "home = /gone/python/bin" if line.startswith("home") else line
            for line in configuration.read_text().splitlines()
        )
        + "\n"
    )
    kept = environment._environment_site_packages(separate) / "kept.py"
    kept.write_text("")
    calls = []
    real_run = subprocess.run
    monkeypatch.setattr(environment.subprocess, "run", lambda command, **kw: calls.append(command) or real_run(command, **kw))
    environment.qibocal_python()
    assert calls and calls[0][1:4] == ["-m", "venv", "--upgrade"]
    assert kept.is_file(), "Qibocal and the rest stay"
    assert "/gone/" not in configuration.read_text()


def test_qibocal_is_read_and_installed_there_not_in_the_server(separate, monkeypatch):
    environment.qibocal_python()
    assert environment.get_qibocal_status().installed is False, "the server's own Qibocal does not count"
    info = environment._environment_site_packages(separate) / "qibocal-9.9.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: qibocal\nVersion: 9.9\n")
    status = environment.get_qibocal_status()
    assert (status.installed, status.version) == (True, "9.9")

    python = str(environment._environment_python(separate))
    monkeypatch.setattr(environment.shutil, "which", lambda name: "/tools/uv" if name == "uv" else None)
    [command] = environment._installer_commands("qibocal==9.9")
    assert command[:5] == ["/tools/uv", "pip", "install", "--python", python]
    monkeypatch.setattr(environment.shutil, "which", lambda name: None)
    commands = environment._installer_commands("qibocal==9.9")
    assert all(command[:4] == [python, "-m", "pip", "install"] for command in commands), "its own pip"


def test_plots_are_generated_with_the_environment_s_python(separate, tmp_path, monkeypatch):
    environment.qibocal_python()  # made for real, before the worker is stood in for
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 1, "", "stopped here")

    monkeypatch.setattr(generator.subprocess, "run", run)
    generator._generate_qibocal_protocols(tmp_path)
    assert seen[-1][:3] == [str(environment._environment_python(separate)), "-m", "qibocamp.reports.generation_worker"]


def test_an_environment_that_cannot_be_made_is_an_error_the_page_can_show(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("")
    monkeypatch.setenv(environment.ENVIRONMENT_VARIABLE, str(blocked / "env"))
    with pytest.raises(environment.EnvironmentOperationError, match="Qibocal environment"):
        environment.qibocal_python()
    protocols, error = generator._generate_qibocal_protocols(tmp_path)
    assert protocols is None and "Qibocal environment" in error
