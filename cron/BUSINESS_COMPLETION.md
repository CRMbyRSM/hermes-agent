# Opt-in business completion for cron

A normal agent reply is not always a completed business task. An operator can attach a deterministic local completion checker to one existing job, without changing its clock, model, provider, tools, or delivery target:

```yaml
cron:
  completion_checks:
    YOUR_JOB_ID:
      script: integrations/check_completion.py
      sha256: THE_REVIEWED_SCRIPT_SHA256
      max_continuations: 3
      timeout_seconds: 30
```

`script` must be a Python file under the active profile's `scripts/` directory. Absolute paths, traversal, symlink escape, invalid limits, and a configured hash mismatch fail closed before the agent starts. The script's bytes are pinned before the first agent call even when `sha256` is omitted, and checked again before each invocation. Jobs without a matching configuration retain their existing behavior.

The checker receives JSON on stdin containing schema_version=1, job_id, the current session_id, attempt (0 for the initial result), and the ORIGINAL pre-run script's success flag/stdout. It does not receive or parse an assistant's answer as execution identity. The checker must independently bind the actual business artifacts to the exact pre-run identity. Its output is JSON:

```json
{"schema_version":1,"complete":false,"retryable":true,"reason":"research is unfinished","continuation_prompt":"Resume the current run from its saved research stage."}
```

A complete verdict requires retryable=false and an empty continuation_prompt. A retryable verdict requires an instruction. Invalid output, excessive output, a checker failure/timeout, a terminal business failure, or exhausted continuation attempts fails the run. The same agent, task identity, native watchdog and conversation history are reused; the pre-run script is NOT replayed. Missing history fails rather than starting over. Cancellation prevents continuation. Session finalization records configured failures as cron_business_incomplete, not cron_complete. Native token/cost fields are cumulative session totals and are not summed again.

Limits: at most three additional conversation turns; each retains the normal per-turn iteration/inactivity controls. This is not authorization to replay paid requests or uncertain writes. The business checker must preserve the native identity, eligibility, idempotency, cost and write-verification contracts. This mechanism itself does not prove business completion or live deployment: an operator must separately test the actual checker and observe the target workflow.

Local evidence: the original run_job regression was observed failing before implementation; 25 new focused tests pass, plus 23 existing run-job/prompt tests and 14 existing watchdog/cleanup/progress/canonical-run tests. Broader delivery tests have independent HOME-I/O isolation failures reproduced on unchanged baseline code; no guard was disabled. Production activation and gateway restart were not performed.
