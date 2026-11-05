"""Parent review of native continuation/failure handling. No provider/CRM access."""
import json
from pathlib import Path
import threading

import pytest
from tests.cron import test_business_completion as support


def config(maximum=1, **overrides):
    return {"cron": {"completion_checks": {"business-job": {
        "script": "completion.py", "max_continuations": maximum,
        "timeout_seconds": 2, **overrides}}}}


def checker(monkeypatch, body):
    def create(home):
        path = home / "scripts/completion.py"
        path.write_text(body)
        return path
    monkeypatch.setattr(support, "_fixture_checker", create)


def test_framework_failure_does_not_get_business_complete_booking(monkeypatch, tmp_path):
    class Failure(support._FakeAgent):
        def run_conversation(self, prompt, **kwargs):
            self.calls.append((prompt, kwargs))
            return {"failed": True, "completed": False, "error": "fixture transport failure",
                    "final_response": "failed", "messages": [{"role": "assistant", "content": "failed"}]}
    monkeypatch.setattr(support, "_FakeAgent", Failure)
    result, agent, db, _ = support._run_native_job(monkeypatch, tmp_path, config())
    assert result[0] is False
    assert len(agent.calls) == 1
    assert db.ended[-1][1] == "cron_business_incomplete"


READ = "import json,sys\np=json.load(sys.stdin)\n"
RETRY = {"schema_version": 1, "complete": False, "retryable": True,
         "reason": "still pending", "continuation_prompt": "Resume the pending native stage"}
DONE = {"schema_version": 1, "complete": True, "retryable": False,
        "reason": "verified", "continuation_prompt": ""}


def test_repeated_premature_stops_have_a_hard_bound(monkeypatch, tmp_path):
    checker(monkeypatch, READ + "json.dump(" + repr(RETRY) + ",sys.stdout)\n")
    result, agent, db, preruns = support._run_native_job(monkeypatch, tmp_path, config(2))
    assert result[0] is False
    assert len(agent.calls) == 3
    assert len(preruns) == 1
    assert db.ended[-1][1] == "cron_business_incomplete"
    assert "after 2 continuations" in (result[3] or "")


def test_unconfigured_job_is_unchanged_and_never_checks(monkeypatch, tmp_path):
    checker(monkeypatch, "raise AssertionError('must not run')\n")
    result, agent, db, preruns = support._run_native_job(monkeypatch, tmp_path, {})
    assert result[0] is True
    assert result[2] == "Research is pending"
    assert len(agent.calls) == len(preruns) == 1
    assert db.ended[-1][1] == "cron_complete"


@pytest.mark.parametrize("body", [
    READ + "print('not-json')\n",
    READ + "print('{}')\n",
    READ + "json.dump({**" + repr(DONE) + ", 'complete':'true'},sys.stdout)\n",
    READ + "json.dump({**" + repr(DONE) + ", 'retryable':True},sys.stdout)\n",
    READ + "sys.exit(7)\n",
    READ + "sys.stdout.write('x'*1000000)\n",
    READ + "sys.stderr.write('x'*1000000)\n",
])
def test_checker_faults_never_become_success(monkeypatch, tmp_path, body):
    checker(monkeypatch, body)
    result, agent, db, _ = support._run_native_job(monkeypatch, tmp_path, config())
    assert result[0] is False
    assert len(agent.calls) == 1
    assert db.ended[-1][1] == "cron_business_incomplete"


def test_checker_timeout_is_bounded_and_does_not_replay_agent(monkeypatch, tmp_path):
    checker(monkeypatch, READ + "import time\ntime.sleep(30)\n")
    result, agent, db, _ = support._run_native_job(monkeypatch, tmp_path, config(timeout_seconds=0.1))
    assert result[0] is False
    assert len(agent.calls) == 1
    assert "timed out" in (result[3] or "")
    assert db.ended[-1][1] == "cron_business_incomplete"


@pytest.mark.parametrize("override", [
    {"script": "../completion.py"}, {"script": "/arbitrary/check.py"},
    {"script": "missing.py"}, {"max_continuations": True},
    {"max_continuations": 4}, {"timeout_seconds": True}, {"timeout_seconds": 61},
    {"sha256": "0"*64},
])
def test_invalid_config_fails_before_inference(monkeypatch, tmp_path, override):
    result, agent, _db, _ = support._run_native_job(monkeypatch, tmp_path, config(**override))
    assert result[0] is False
    assert agent.calls == []


def test_missing_history_cannot_restart_a_blank_conversation(monkeypatch, tmp_path):
    class MissingHistory(support._FakeAgent):
        def run_conversation(self, prompt, **kwargs):
            result = super().run_conversation(prompt, **kwargs)
            result.pop("messages")
            return result
    monkeypatch.setattr(support, "_FakeAgent", MissingHistory)
    result, agent, _db, _ = support._run_native_job(monkeypatch, tmp_path, config())
    assert result[0] is False
    assert len(agent.calls) == 1
    assert "prior conversation history" in (result[3] or "")


def test_checker_pinned_before_inference_cannot_be_replaced(monkeypatch, tmp_path):
    class ChangeChecker(support._FakeAgent):
        def run_conversation(self, prompt, **kwargs):
            (tmp_path / "hermes-home/scripts/completion.py").write_text(READ + "json.dump(" + repr(DONE) + ",sys.stdout)")
            return super().run_conversation(prompt, **kwargs)
    monkeypatch.setattr(support, "_FakeAgent", ChangeChecker)
    result, agent, _db, _ = support._run_native_job(monkeypatch, tmp_path, config())
    assert result[0] is False
    assert "changed during execution" in (result[3] or "")
    assert len(agent.calls) == 1


def test_cancellation_prevents_another_agent_call(monkeypatch, tmp_path):
    cancel = threading.Event()
    class Cancel(support._FakeAgent):
        def run_conversation(self, prompt, **kwargs):
            result = super().run_conversation(prompt, **kwargs)
            cancel.set()
            return result
    monkeypatch.setattr(support, "_FakeAgent", Cancel)
    result, agent, _db, _ = support._run_native_job(monkeypatch, tmp_path, config(), cancel_event=cancel)
    assert result[0] is False
    assert len(agent.calls) == 1


def test_original_stdout_and_run_identity_are_not_taken_from_prose(monkeypatch, tmp_path):
    marker = tmp_path / "checker-input.jsonl"
    body = READ + "from pathlib import Path\nwith Path(" + repr(str(marker)) + ").open('a') as f: f.write(json.dumps(p)+'\\n')\n"
    body += "json.dump(" + repr(RETRY) + " if p['attempt']==0 else " + repr(DONE) + ",sys.stdout)\n"
    checker(monkeypatch, body)
    result, agent, _db, preruns = support._run_native_job(monkeypatch, tmp_path, config())
    assert result[0] is True, result
    rows = [json.loads(line) for line in marker.read_text().splitlines()]
    assert len(rows) == 2
    assert all(row["prerun_script_output"] == preruns[0] for row in rows)
    assert all(row["session_id"] == agent.session_id for row in rows)
    assert all("final_response" not in row and "prompt" not in row for row in rows)



def test_checker_setup_failure_reaps_the_child(monkeypatch, tmp_path):
    from cron import completion_check as module
    script = tmp_path / "checker.py"
    script.write_text("pass\n")
    import hashlib
    check = module.CompletionCheck(script, hashlib.sha256(script.read_bytes()).hexdigest(), 1, 1)
    class Child:
        stdin = stdout = stderr = None
        waited = False
        def wait(self, timeout=None):
            self.waited = True
    child = Child()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(module, "_kill", lambda process: None)
    def broken_start(thread):
        raise RuntimeError("fixture cannot start I/O thread")
    monkeypatch.setattr(module.threading.Thread, "start", broken_start)
    with pytest.raises(RuntimeError):
        module.run_completion_check(check, {})
    assert child.waited, "a child started before setup failure must still be reaped"
