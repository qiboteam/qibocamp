"""Agent configurations pushed to another machine's agents.json."""

import json
import os
import subprocess
import sys
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from qibocamp.cli import main
from qibocamp.remote import agents

# Patching `agents.subprocess.run` patches the module: the real one, kept.
REAL_RUN = subprocess.run


def run_merge_here(tmp_path):
    """Run the remote merge script with this Python, as ssh would run it there."""
    def run(command, **kwargs):
        import shlex
        python, flag, script, request = shlex.split(command[-1])
        assert (python, flag) == ("python3", "-c")
        env = {**os.environ, "QIBOCAL_AGENTS_CONFIG_DIR": str(tmp_path)}
        return REAL_RUN([sys.executable, "-c", script, request], capture_output=True, text=True, env=env, check=False)
    return run


def test_pushed_entries_merge_into_the_remote_file_and_leave_the_rest(tmp_path):
    (tmp_path / "agents.json").write_text(json.dumps({
        "agents": {"default": {"platform": "keep"}, "theirs": {"platform": "a"}, "old": {"platform": "b"}},
    }))
    with patch.object(agents.subprocess, "run", side_effect=run_merge_here(tmp_path)):
        answer = agents.push("me@login", {"lab": {"platform": "qw5q", "partition": "qpu"}}, remove=["old"])
    document = json.loads((tmp_path / "agents.json").read_text())
    assert document["agents"] == {
        "default": {"platform": "keep"}, "theirs": {"platform": "a"}, "lab": {"platform": "qw5q", "partition": "qpu"},
    }, "the configurations not pushed are left as they are"
    assert answer["agents"] == ["default", "lab", "theirs"]
    assert "version" not in document, "qibocal-agent refuses unknown keys"


def test_a_file_that_is_not_json_is_left_alone(tmp_path):
    (tmp_path / "agents.json").write_text("{broken")
    with patch.object(agents.subprocess, "run", side_effect=run_merge_here(tmp_path)):
        with pytest.raises(click.ClickException, match="not valid JSON"):
            agents.push("me@login", {"lab": {"platform": "x"}})
    assert (tmp_path / "agents.json").read_text() == "{broken"


def test_names_are_checked_and_the_command_takes_json():
    with pytest.raises(click.ClickException, match="Invalid agent name"):
        agents.push("me@login", {"bad name": {}})
    for reserved in ("agent", "agents"):
        with pytest.raises(click.ClickException, match="Invalid agent name"):
            agents.push("me@login", {reserved: {}})
    with patch.object(agents, "push", return_value={"path": "p", "agents": ["lab"]}) as push:
        result = CliRunner().invoke(main, ["agents", "push", "me@login", "--agents", '{"lab": {"platform": "x"}}', "--remove", "old"])
    assert result.exit_code == 0, result.output
    assert push.call_args.args == ("me@login", {"lab": {"platform": "x"}}, ["old"])
    bad = CliRunner().invoke(main, ["agents", "push", "me@login", "--agents", "[1]"])
    assert bad.exit_code != 0


def managed_environment(tmp_path, monkeypatch, name="lab"):
    """A managed environment where qibocal-agent makes it, under this XDG cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    path = tmp_path / "qibocal" / "agents" / "envs" / name
    (path / "bin").mkdir(parents=True)
    (path / "pyvenv.cfg").write_text("home = /usr/bin\n")
    return path


def test_the_environment_of_a_configuration_is_deleted_here(tmp_path, monkeypatch):
    path = managed_environment(tmp_path, monkeypatch)
    answer = agents.remove_environment("lab")
    assert answer == {"path": str(path.resolve()), "removed": True}
    assert not path.exists()
    assert agents.remove_environment("lab")["removed"] is False, "none yet is not an error"


def test_only_a_python_environment_is_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    folder = tmp_path / "precious"
    (folder / "data").mkdir(parents=True)
    with pytest.raises(click.ClickException, match="not a Python environment"):
        agents.remove_environment("lab", venv=str(folder))
    assert (folder / "data").is_dir()


def test_an_environment_being_prepared_is_left_alone(tmp_path, monkeypatch):
    import fcntl
    import hashlib

    path = managed_environment(tmp_path, monkeypatch).resolve()
    lock = tmp_path / "qibocal" / "agents" / "locks" / (hashlib.sha256(str(path).encode()).hexdigest() + ".lock")
    lock.parent.mkdir(parents=True)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        with pytest.raises(click.ClickException, match="being prepared"):
            agents.remove_environment("lab")
    assert path.is_dir()


def test_the_environment_is_reached_where_the_server_starts():
    import shlex

    completed = subprocess.CompletedProcess([], 0, stdout='{"path": "p", "removed": true}\n', stderr="")
    with patch.object(agents.subprocess, "run", return_value=completed) as run:
        agents.remove_environment("lab", ssh="login")
        direct = run.call_args.args[0]
        agents.remove_environment("lab", venv="shared", ssh="node", via="-p 2222 me@login")
        nested = run.call_args.args[0]
    assert direct[:6] == ["ssh", "-T", "-o", "BatchMode=yes", "--", "login"]
    assert shlex.split(direct[-1])[:2] == ["python3", "-c"]
    assert nested[:6] == ["ssh", "-p", "2222", "-T", "-o", "BatchMode=yes"] and nested[6] == "me@login"
    hop = shlex.split(nested[-1])
    assert hop[:6] == ["ssh", "-T", "-o", "BatchMode=yes", "--", "node"], "from the machine it is kept for"
    inner = shlex.split(hop[6])
    assert inner[:2] == ["python3", "-c"] and json.loads(inner[3]) == {"venv": "shared"}
    with pytest.raises(click.ClickException, match="Invalid agent name"):
        agents.remove_environment("agents")


def test_the_rebuild_command_answers_json():
    with patch.object(agents, "remove_environment", return_value={"path": "p", "removed": True}) as remove:
        result = CliRunner().invoke(main, ["agents", "rebuild", "lab", "--ssh", "login", "--via", "me@host"])
    assert result.exit_code == 0, result.output
    assert remove.call_args.args == ("lab", None, "login", "me@host")
    assert json.loads(result.output) == {"path": "p", "removed": True}
