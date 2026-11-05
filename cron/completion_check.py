"""Opt-in, bounded business completion checks for an existing cron execution.

A checker is trusted operator code, not a model-selected command. Identity comes
from original pre-run stdout, never a candidate assistant response. This module
neither changes a schedule nor replays a pre-run script or an external effect.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


class CompletionCheckError(RuntimeError):
    """The configured business-completion contract was not satisfied."""


@dataclass(frozen=True)
class CompletionCheck:
    script: Path
    sha256: str
    max_continuations: int
    timeout_seconds: float


def resolve_completion_check(config: dict, job_id: str, home: Path) -> CompletionCheck | None:
    cron = config.get("cron") or {}
    checks = cron.get("completion_checks")
    if checks is None:
        return None
    if not isinstance(checks, dict):
        raise CompletionCheckError("cron.completion_checks must be a mapping")
    if job_id not in checks:
        return None
    value = checks[job_id]
    if not isinstance(value, dict) or set(value) - {"script", "sha256", "max_continuations", "timeout_seconds"}:
        raise CompletionCheckError("invalid completion-check configuration")
    name = value.get("script")
    maximum = value.get("max_continuations", 3)
    timeout = value.get("timeout_seconds", 30)
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        raise CompletionCheckError("completion script must be relative to profile scripts")
    if type(maximum) is not int or not 0 <= maximum <= 3:
        raise CompletionCheckError("max_continuations must be an integer from 0 to 3")
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise CompletionCheckError("completion timeout must be positive and at most 60 seconds")
    root = (Path(home) / "scripts").resolve()
    script = (root / name).resolve()
    if not script.is_relative_to(root) or not script.is_file() or script.suffix != ".py":
        raise CompletionCheckError("completion script missing or outside profile scripts")
    digest = hashlib.sha256(script.read_bytes()).hexdigest()
    if "sha256" in value and value["sha256"] != digest:
        raise CompletionCheckError("completion script does not match configured SHA-256")
    return CompletionCheck(script, digest, maximum, float(timeout))


def _cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _kill(process) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        pass


def run_completion_check(check: CompletionCheck, payload: dict, cancel_event=None) -> dict:
    """Execute a pinned checker with bounded input/output, cancellation and timeout.

    Stream readers retain at most 64 KiB each. The deadline also bounds pipe EOF
    when an incorrectly written checker leaves a descendant holding a pipe open.
    No checker stderr or input data is echoed into logs/errors.
    """
    if _cancelled(cancel_event):
        raise CompletionCheckError("business completion cancelled")
    try:
        if hashlib.sha256(check.script.read_bytes()).hexdigest() != check.sha256:
            raise CompletionCheckError("completion script changed during execution")
    except OSError as exc:
        raise CompletionCheckError("completion script became unavailable") from exc
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(raw) > 3_000_000:
        raise CompletionCheckError("completion input exceeds bounded size")
    process = subprocess.Popen(
        [sys.executable, "-B", str(check.script)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=os.name == "posix",
    )
    overflow = threading.Event()
    output, errors = bytearray(), bytearray()
    faults = []

    def drain(stream, target):
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
                if len(target) + len(data) > 65536:
                    overflow.set()
                    break
                target.extend(data)
        except OSError:
            faults.append(True)

    def feed():
        try:
            assert process.stdin is not None
            process.stdin.write(raw)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            faults.append(True)

    threads = [threading.Thread(target=drain, args=(process.stdout, output), daemon=True),
               threading.Thread(target=drain, args=(process.stderr, errors), daemon=True),
               threading.Thread(target=feed, daemon=True)]
    deadline = time.monotonic() + check.timeout_seconds
    try:
        for thread in threads:
            thread.start()
        while True:
            if _cancelled(cancel_event):
                raise CompletionCheckError("business completion cancelled")
            if overflow.is_set():
                raise CompletionCheckError("completion checker output exceeds bounded size")
            if time.monotonic() >= deadline:
                raise CompletionCheckError("completion checker timed out")
            if process.poll() is not None and not any(thread.is_alive() for thread in threads):
                break
            time.sleep(0.01)
        if process.returncode != 0:
            raise CompletionCheckError(f"completion checker exited with status {process.returncode}")
        if faults:
            raise CompletionCheckError("completion checker pipe failed")
    finally:
        _kill(process)
        process.wait(timeout=5)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
    try:
        verdict = json.loads(output.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CompletionCheckError("completion checker returned invalid JSON") from exc
    if (not isinstance(verdict, dict) or type(verdict.get("schema_version")) is not int
            or verdict["schema_version"] != 1 or type(verdict.get("complete")) is not bool
            or type(verdict.get("retryable")) is not bool
            or not isinstance(verdict.get("reason"), str) or not verdict["reason"].strip()
            or len(verdict["reason"]) > 2000
            or not isinstance(verdict.get("continuation_prompt"), str)
            or len(verdict["continuation_prompt"]) > 10000):
        raise CompletionCheckError("completion checker returned an invalid verdict schema")
    if verdict["complete"] and (verdict["retryable"] or verdict["continuation_prompt"]):
        raise CompletionCheckError("complete verdict cannot request continuation")
    if verdict["retryable"] and not verdict["continuation_prompt"].strip():
        raise CompletionCheckError("retryable verdict requires a continuation instruction")
    return verdict
