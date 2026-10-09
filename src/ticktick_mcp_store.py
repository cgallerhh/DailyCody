"""Dedicated macOS Keychain IPC adapter; never call this to inspect other tokens."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess

from ticktick_mcp_auth import AuthError, State


class MissingState(AuthError):
    pass


class KeychainStore:
    writable = True

    def __init__(self, executable: Path):
        self.executable = executable

    def _run(self, mode, value=None):
        # Only the reviewed, private helper can access this exact dedicated item.
        if (not self.executable.is_absolute() or self.executable.is_symlink() or
                not self.executable.is_file() or self.executable.stat().st_mode & 0o022):
            raise AuthError("Geprüfter eigener Cody-Keychain-Helfer fehlt oder ist nicht geschützt.")
        try:
            result = subprocess.run([str(self.executable), mode], input=value, capture_output=True,
                                    timeout=60, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.TimeoutExpired):
            raise AuthError("Eigener Cody-Keychain-Aufruf fehlgeschlagen; keine geheimen Details ausgegeben.") from None
        if result.returncode == 2 and mode == "load":
            raise MissingState("Eigener Cody-MCP-Zustand ist noch nicht eingerichtet.")
        if result.returncode or len(result.stdout) > 65536:
            raise AuthError("Eigener Cody-Keychain-Zustand konnte nicht sicher bestätigt werden.")
        return result.stdout

    def load(self):
        try:
            return State.from_json(self._run("load").decode("utf-8"))
        except UnicodeError:
            raise AuthError("Eigener Cody-Keychain-Zustand ist ungültig.") from None

    def save(self, state):
        self._run("save", state.to_json().encode())

    @contextmanager
    def transaction(self):
        # The shared lock contains no secrets; serialize refresh + publish callers.
        path = self.executable.with_suffix(".lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
