from __future__ import annotations

import subprocess
from typing import Tuple

from utils.constants import COMMAND_TIMEOUT_DEFAULT


def run_shell(
    command: str, timeout: int = COMMAND_TIMEOUT_DEFAULT
) -> Tuple[str | None, str | None, int]:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return None, f"Command timed out after {timeout}s", -1
    except Exception as exc:
        return None, str(exc), -1


def is_container_running(name: str) -> bool:
    stdout, _, _ = run_shell(
        f"docker ps --filter name={name} --format '{{{{.Names}}}}'"
    )
    return name in (stdout or "")


def docker_exec(
    container: str, cmd: str, **kwargs
) -> Tuple[str | None, str | None, int]:
    return run_shell(f"docker exec {container} {cmd}", **kwargs)
