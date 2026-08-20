"""Bash environment: execute commands through a bash shell.

On Windows the default ``LocalEnvironment`` runs every command via ``cmd.exe``
(``subprocess.Popen(..., shell=True)``), which does not support bash syntax such
as ``cat <<'EOF'`` heredocs, inline ``VAR=x cmd`` prefixes, or ``sed -i``. The
agent prompt templates are written for bash, so commands silently fail and the
agent falls back to error-prone ``echo ... >> file`` (mangling non-ASCII text).

This environment runs every command via ``bash -c`` instead, so the bash
templates work unchanged. On POSIX it is equivalent to ``LocalEnvironment``;
on Windows it resolves Git Bash (``bash`` on PATH, else common install paths).
"""

import os
import shutil
import subprocess
from pathlib import Path

from minisweagent.environments.local import LocalEnvironment, LocalEnvironmentConfig

# Common Git Bash install locations on Windows, tried in order.
_WINDOWS_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
]


def _find_bash() -> str:
    """Return a bash executable, preferring one already on PATH."""
    if found := shutil.which("bash"):
        return found
    for candidate in _WINDOWS_BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    # Fall back to plain `bash`; the user may have it elsewhere.
    return "bash"


class BashEnvironmentConfig(LocalEnvironmentConfig):
    interpreter: str = _find_bash()
    """Path to the bash executable (auto-detected on Windows)."""


class BashEnvironment(LocalEnvironment):
    def __init__(self, *, config_class: type = BashEnvironmentConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict:
        """Execute a command via ``bash -c`` and return the result as a dict."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        try:
            result = subprocess.run(
                [self.config.interpreter, "-c", command],
                cwd=cwd,
                env=os.environ | self.config.env,
                timeout=timeout or self.config.timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output
