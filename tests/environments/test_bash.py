"""Tests for the BashEnvironment (bash -c execution instead of cmd.exe)."""

import shutil

import pytest

from minisweagent.environments import get_environment
from minisweagent.environments.extra.bash import BashEnvironment, BashEnvironmentConfig
from minisweagent.exceptions import Submitted

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _bash_env() -> BashEnvironment:
    return BashEnvironment(config_class=BashEnvironmentConfig)


def test_plain_command():
    result = _bash_env().execute({"command": "echo hello"})
    assert result["returncode"] == 0
    assert result["output"].strip() == "hello"


def test_heredoc_creates_file(tmp_path):
    """The exact case that fails under cmd.exe must work under bash."""
    cmd = "cat <<'EOF' > _heredoc_test.txt\nline one\nline two\nEOF\ncat _heredoc_test.txt"
    result = _bash_env().execute({"command": cmd}, cwd=str(tmp_path))
    assert result["returncode"] == 0
    assert result["output"].strip() == "line one\nline two"
    assert (tmp_path / "_heredoc_test.txt").read_text(encoding="utf-8") == "line one\nline two\n"


def test_inline_env_prefix():
    result = _bash_env().execute({"command": "MY_VAR=hello && echo $MY_VAR"})
    assert result["returncode"] == 0
    assert result["output"].strip() == "hello"


def test_utf8_content_preserved(tmp_path):
    cmd = "printf '中文内容\n' > _utf8.txt\ncat _utf8.txt"
    result = _bash_env().execute({"command": cmd}, cwd=str(tmp_path))
    assert result["returncode"] == 0
    assert result["output"].strip() == "中文内容"
    assert (tmp_path / "_utf8.txt").read_text(encoding="utf-8") == "中文内容\n"


def test_completion_marker_raises_submitted():
    env = _bash_env()
    with pytest.raises(Submitted):
        env.execute({"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && echo submission text"})


def test_factory_registration():
    env = get_environment({"environment_class": "bash"})
    assert isinstance(env, BashEnvironment)


def test_serialize_and_template_vars():
    env = _bash_env()
    data = env.serialize()
    assert "environment_type" in data["info"]["config"]
    assert "interpreter" in env.get_template_vars()
