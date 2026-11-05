"""Native cron-runner coverage for opt-in bounded business completion."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace, ModuleType

from cron import scheduler
from gateway import session_cron


class _RecordingSessionDB:
    def __init__(self):
        self.ended = []

    def get_compression_tip(self, session_id):
        return None

    def set_session_title(self, *_args, **_kwargs):
        return True

    def session_lifecycle_statuses(self, session_ids):
        return {session_id: "complete" for session_id in session_ids}

    def end_session(self, session_id, reason):
        self.ended.append((session_id, reason))

    def close(self):
        pass


class _FakeAgent:
    def __init__(self, session_id):
        self.session_id = session_id
        self.calls = []
        self._end_session_on_close = True

    def run_conversation(self, prompt, *, task_id=None, **kwargs):
        self.calls.append((prompt, task_id, kwargs))
        turn = len(self.calls)
        response = "Research is pending" if turn == 1 else "Business work complete"
        return {
            "completed": True,
            "failed": False,
            "final_response": response,
            "messages": [{"role": "assistant", "content": response}],
        }

    def close(self):
        pass


def _fixture_checker(home):
    scripts = home / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    checker = scripts / "completion.py"
    checker.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        "if payload['attempt'] == 0:\n"
        "    verdict = {'schema_version': 1, 'complete': False, 'retryable': True, "
        "'reason': 'business work still pending', 'continuation_prompt': 'Finish the business work'}\n"
        "else:\n"
        "    verdict = {'schema_version': 1, 'complete': True, 'retryable': False, "
        "'reason': 'business work verified', 'continuation_prompt': ''}\n"
        "json.dump(verdict, sys.stdout)\n",
        encoding="utf-8",
    )
    return checker


def _run_native_job(monkeypatch, tmp_path, config, cancel_event=None, job_id="business-job", prerun_stdout=None):
    """Run run_job's owned execution path with only agent/runtime boundaries faked."""
    home = tmp_path / "hermes-home"
    (home / "scripts").mkdir(parents=True)
    _fixture_checker(home)
    db = _RecordingSessionDB()
    job = {
        "id": job_id,
        "name": "Business completion",
        "prompt": "Research the business task",
        "script": "prerun.py",
        "schedule_display": "manual",
    }
    agent = _FakeAgent("cron-business-session")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setitem(sys.modules, "hermes_bootstrap", ModuleType("hermes_bootstrap"))
    monkeypatch.setitem(sys.modules, "hermes_cli.main", ModuleType("hermes_cli.main"))
    import run_agent

    monkeypatch.setattr(run_agent, "AIAgent", type("FakeAIAgent", (), {}))
    monkeypatch.setattr(scheduler, "_get_hermes_home", lambda: home)
    monkeypatch.setattr(scheduler, "_load_cron_job_config", lambda *_args: SimpleNamespace(
        cfg=config, model="test-model"))
    monkeypatch.setattr(scheduler, "_resolve_cron_agent_setup", lambda *_args: scheduler._CronAgentSetup(
        model="test-model", runtime={}))
    monkeypatch.setattr(scheduler, "_construct_cron_agent", lambda *_args, **_kwargs: agent)
    monkeypatch.setattr(scheduler, "_open_cron_session_db", lambda *_args: db)
    monkeypatch.setattr(scheduler, "_reload_dotenv_and_publish_delivery_target", lambda *_args: None)
    prerun_outputs = []

    def run_prerun(*_args, **_kwargs):
        prerun_outputs.append(prerun_stdout if prerun_stdout is not None else "ORIGINAL\r\ntrusted stdout\n")
        return True, prerun_outputs[-1]

    monkeypatch.setattr(scheduler, "_run_job_script_with_claim_heartbeat", run_prerun)
    owner = SimpleNamespace(db=db)
    execution_id = "business-fire"
    token = session_cron._execution.set((owner, agent.session_id, job["id"], execution_id))
    try:
        result = scheduler.run_job(job, execution_id=execution_id, cancel_event=cancel_event)
    finally:
        session_cron._execution.reset(token)
    return result, agent, db, prerun_outputs


def test_premature_report_continues_same_cron_agent_with_prior_messages(monkeypatch, tmp_path):
    config = {
        "cron": {
            "completion_checks": {
                "business-job": {
                    "script": "completion.py",
                    "max_continuations": 1,
                    "timeout_seconds": 5,
                }
            }
        }
    }

    result, agent, _db, prerun_outputs = _run_native_job(monkeypatch, tmp_path, config)

    assert result[0] is True, result
    assert result[2] == "Business work complete"
    assert len(agent.calls) == 2, "a normal premature report currently returns as cron success"
    first_prompt, task_id, first_kwargs = agent.calls[0]
    second_prompt, second_task_id, second_kwargs = agent.calls[1]
    assert "Research the business task" in first_prompt
    assert task_id == second_task_id
    assert second_prompt == "Finish the business work"
    assert second_kwargs["conversation_history"] == [
        {"role": "assistant", "content": "Research is pending"}
    ]
    assert first_kwargs == {}
    assert len(prerun_outputs) == 1
