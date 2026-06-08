# T6 — Agent Backend Plan (opencode / codex / qwen)

Source specification: [`scripts/release-helper/vibe/specification.md`](scripts/release-helper/vibe/specification.md:1)
Parent plan: [`plans/T6-implementation-plan.md`](plans/T6-implementation-plan.md:1)

## Status: Deferred (post-MVP)

The HTTP API backend (`http_api`) is the default and covers the MVP.
This document captures the design for the `agent` backend so it can be
implemented without reworking T7/T10.

---

## Goal

Allow the LLM to **walk the repository** — run `git show <sha>`, grep, and
read files — when classifying commits.  This is useful for commits where the
subject/body/metadata alone is ambiguous.

The agent backend slots in behind the existing
[`LlmClient`](scripts/release-helper/changelog_tool/llm/base.py:1) interface,
so T7 and T10 are unaffected.

---

## How it works

```
changelog-tool collect
  └─ T7 classification loop
       └─ build_client(cfg)  →  AgentBackend
            └─ complete(request)
                 └─ subprocess: opencode run --format json -m <model> "<prompt>"
                      cwd = repo.local_path
                      stdout → extract_agent_final_message → parse_classification_response
```

1. `AgentBackend.complete()` writes the prompt to a temp file (for large prompts)
   or passes it as a positional arg.
2. Spawns `opencode run --format json -m <model> <prompt>` with
   `cwd = repo.local_path`.
3. The agent can call `git show <sha>`, `grep`, `cat <file>` — all read-only.
4. Captures stdout, routes through `extract_agent_final_message()` (new helper
   in `parsing.py`) then the existing `parse_classification_response()`.
5. Non-zero exit / timeout → `LlmTransientError`.
   Missing binary → `LlmError` with actionable message.

---

## Read-only safety (Spec §15.5)

Create a minimal `opencode.json` in the workdir (or pass flags) that disables
all write tools:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "edit": "deny",
    "bash": "deny"
  },
  "tools": {
    "write": false
  }
}
```

The agent may only read / grep / `git show`.  It must never modify the repo,
create commits, or push.

---

## Granularity change vs HTTP backend

The HTTP backend sends batches of up to `llm.batch_size` commits per call.
The agent backend is naturally **per-commit** (one task = one agent run).

T7 must detect the backend type and iterate per-commit when `AgentBackend` is
active.  The §9.12 invariants still apply to each single-commit "batch"
(`input_count == 1`, one SHA back).

---

## Configuration additions

```yaml
llm:
  backend: agent
  model: anthropic/claude-sonnet-4-5   # provider/model for opencode
  agent:
    command: opencode          # binary name (must be on PATH)
    extra_args: []             # e.g. ["--no-auto-update"]
    server_url: null           # optional: attach to `opencode serve` instance
    timeout_seconds: 600       # per-commit timeout
    opencode_config_path: null # path to opencode.json; auto-generated if null
```

Secrets (API key for the underlying provider) are configured in opencode's own
auth store (`opencode auth login`) or via provider-specific env vars — not in
`changelog.yaml`.

---

## New files to create

```text
changelog_tool/llm/backends/agent.py
```

### `AgentBackend` sketch

```python
class AgentBackend(LlmClient):
    def complete(self, request: LlmRequest) -> LlmResponse:
        prompt = self._prepare_prompt(request)
        cmd = self._build_cmd(prompt)
        try:
            result = subprocess.run(
                cmd,
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LlmTransientError(f"Agent timed out after {self._timeout}s") from exc
        except FileNotFoundError as exc:
            raise LlmError(
                f"Agent binary '{self._command}' not found. "
                "Install opencode or set llm.backend: http_api."
            ) from exc
        if result.returncode != 0:
            raise LlmTransientError(
                f"Agent exited with code {result.returncode}: {result.stderr[:500]}"
            )
        text = extract_agent_final_message(result.stdout)
        return LlmResponse(text=text, backend="agent", raw=result.stdout)
```

### `extract_agent_final_message` (new in `parsing.py`)

opencode `--format json` emits nd-JSON events.  The final assistant message
is in the last event with `type == "assistant"` or `type == "result"`.

```python
def extract_agent_final_message(stdout: str) -> str:
    """Extract the final assistant text from opencode --format json output."""
    last_text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        # opencode emits {"type": "assistant", "message": {"content": [...]}}
        # or {"type": "result", "message": "..."}
        if event.get("type") in ("assistant", "result"):
            msg = event.get("message", "")
            if isinstance(msg, str) and msg.strip():
                last_text = msg.strip()
            elif isinstance(msg, dict):
                for part in msg.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text":
                        last_text = part.get("text", "").strip()
    if not last_text:
        # Fallback: return the entire stdout for the caller to attempt parsing.
        return stdout.strip()
    return last_text
```

---

## Testing plan

New test module: `tests/test_llm_agent_backend.py`

- `AgentBackend` with a fake subprocess runner (stdout parsing, non-zero exit
  → transient, missing binary → permanent).
- `extract_agent_final_message` with sample opencode nd-JSON output.
- `build_client` returns `AgentBackend` when `llm.backend == "agent"`.
- Read-only `opencode.json` is generated with correct `permission` settings.

All tests run offline (injected subprocess runner, no real opencode binary).

---

## Config validation additions

- `llm.backend: agent` added to `_KNOWN_LLM_BACKENDS` in `config.py`.
- `LlmConfig` gains an `agent` sub-config dataclass (`AgentConfig`).
- `_parse_llm` parses the `agent:` sub-section.

---

## Definition of done for this plan

- `AgentBackend` in `changelog_tool/llm/backends/agent.py`.
- `extract_agent_final_message` in `parsing.py`.
- `build_client` returns `AgentBackend` for `backend: agent`.
- `AgentConfig` parsed from `changelog.yaml`.
- Read-only `opencode.json` auto-generated in workdir.
- Offline unit tests pass.
- T7 iterates per-commit when `AgentBackend` is active.
- README updated with opencode setup instructions.
