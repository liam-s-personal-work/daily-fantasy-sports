"""Model-call layer: one ``AgentTask`` in, one validated ``AgentResult`` out.

Backends:
- ``SDKBackend``: Claude Agent SDK (spawns the Claude Code CLI; works with a Claude
  subscription or an API key). Supports server-side WebSearch/WebFetch and JSON-schema output.
- ``ReplayBackend``: answers only from the on-disk cache (deterministic re-runs, tests).
- ``FakeBackend``: a Python callable per stage, for unit tests.

Every backend goes through ``Runner``, which adds the cache, a run-level dollar budget,
a concurrency limit, per-task timeouts and one retry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

MODELS = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
}


@dataclass
class AgentTask:
    stage: str  # research | forecast | strategist | checker | backup | news ...
    name: str  # unique within the run, e.g. "research:BUF@MIA"
    system: str
    prompt: str
    schema: dict[str, Any]
    model: str = "sonnet"  # alias in MODELS or a full model id
    tools: list[str] = field(default_factory=list)  # e.g. ["WebSearch", "WebFetch"]
    max_turns: int = 12
    timeout_s: float = 600.0
    max_budget_usd: float | None = None
    effort: str | None = None

    def cache_key(self) -> str:
        blob = json.dumps([MODELS.get(self.model, self.model), self.system, self.prompt, self.schema,
                           sorted(self.tools)], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]


@dataclass
class AgentResult:
    name: str
    output: dict[str, Any] | None
    ok: bool
    cost_usd: float = 0.0
    seconds: float = 0.0
    web_searches: int = 0
    cached: bool = False
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class Backend(Protocol):
    async def run(self, task: AgentTask) -> AgentResult: ...


class BudgetExceeded(RuntimeError):
    pass


class SDKBackend:
    """Claude Agent SDK backend. Each task is an isolated one-shot session."""

    def __init__(self, workdir: str | None = None):
        self.workdir = workdir or tempfile.mkdtemp(prefix="dfs-agent-")

    async def run(self, task: AgentTask) -> AgentResult:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        from dfs.sources import FETCH_ALLOW_DOMAINS

        # "dontAsk": any tool use not pre-approved here is denied by the harness. Page fetches
        # are approved only for allow-listed domains; search goes through the server-side tool.
        allowed = [t for t in task.tools if t != "WebFetch"]
        if "WebFetch" in task.tools:
            allowed += [f"WebFetch(domain:{d})" for d in FETCH_ALLOW_DOMAINS]
        opts = ClaudeAgentOptions(
            model=MODELS.get(task.model, task.model),
            system_prompt=task.system,
            tools=list(task.tools),
            allowed_tools=allowed,
            max_turns=task.max_turns,
            output_format={"type": "json_schema", "schema": task.schema},
            setting_sources=[],
            permission_mode="dontAsk",
            cwd=self.workdir,
            max_budget_usd=task.max_budget_usd,
            **({"effort": task.effort} if task.effort else {}),
        )
        t0 = time.monotonic()
        result: AgentResult | None = None
        async for msg in query(prompt=task.prompt, options=opts):
            if isinstance(msg, ResultMessage):
                usage = msg.usage or {}
                stu = usage.get("server_tool_use") or {}
                out = getattr(msg, "structured_output", None)
                ok = msg.subtype == "success" and isinstance(out, dict)
                result = AgentResult(
                    name=task.name, output=out if ok else None, ok=ok,
                    cost_usd=float(msg.total_cost_usd or 0.0), seconds=time.monotonic() - t0,
                    web_searches=int(stu.get("web_search_requests") or 0),
                    error=None if ok else f"result subtype {msg.subtype}",
                    usage={k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                                     "cache_read_input_tokens", "cache_creation_input_tokens")},
                )
        if result is None:
            result = AgentResult(name=task.name, output=None, ok=False, seconds=time.monotonic() - t0,
                                 error="no result message")
        return result


class ReplayBackend:
    """Serves nothing itself: with ``Runner(replay_only=True)`` every task must be cached."""

    async def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(name=task.name, output=None, ok=False, error="not in replay cache")


class FakeBackend:
    """Calls ``handlers[stage](task) -> dict`` (tests and offline demos)."""

    def __init__(self, handlers: dict[str, Callable[[AgentTask], dict[str, Any]]]):
        self.handlers = handlers
        self.calls: list[AgentTask] = []

    async def run(self, task: AgentTask) -> AgentResult:
        self.calls.append(task)
        h = self.handlers.get(task.stage)
        if h is None:
            return AgentResult(name=task.name, output=None, ok=False, error=f"no fake handler for {task.stage}")
        try:
            return AgentResult(name=task.name, output=h(task), ok=True)
        except Exception as e:  # surfaced like a model failure
            return AgentResult(name=task.name, output=None, ok=False, error=repr(e))


class Runner:
    """Cache + budget + concurrency + timeout + retry around a backend; logs every call."""

    def __init__(self, backend: Backend, cache_dir: str | Path | None = None, budget_usd: float = 25.0,
                 concurrency: int = 6, replay_only: bool = False, log: Callable[[str], None] | None = None,
                 retries: int = 1):
        self.backend = backend
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.budget_usd = budget_usd
        self.spent_usd = 0.0
        self.sem = asyncio.Semaphore(concurrency)
        self.replay_only = replay_only
        self.retries = retries
        self.log = log or (lambda s: None)
        self.ledger: list[AgentResult] = []

    def _cache_path(self, task: AgentTask) -> Path | None:
        return self.cache_dir / f"{task.cache_key()}.json" if self.cache_dir else None

    async def run(self, task: AgentTask) -> AgentResult:
        path = self._cache_path(task)
        if path and path.exists():
            data = json.loads(path.read_text())
            res = AgentResult(name=task.name, output=data["output"], ok=True, cached=True)
            self.ledger.append(res)
            return res
        if self.replay_only:
            res = AgentResult(name=task.name, output=None, ok=False, error="replay-only: not cached")
            self.ledger.append(res)
            return res
        res = AgentResult(name=task.name, output=None, ok=False, error="not run")
        async with self.sem:
            for attempt in range(self.retries + 1):
                if self.spent_usd >= self.budget_usd:
                    res = AgentResult(name=task.name, output=None, ok=False, error="run budget exhausted")
                    break
                try:
                    res = await asyncio.wait_for(self.backend.run(task), timeout=task.timeout_s)
                except asyncio.TimeoutError:
                    res = AgentResult(name=task.name, output=None, ok=False, error=f"timeout {task.timeout_s}s")
                except Exception as e:
                    res = AgentResult(name=task.name, output=None, ok=False, error=repr(e))
                self.spent_usd += res.cost_usd
                if res.ok:
                    break
                self.log(f"[llm] {task.name} attempt {attempt + 1} failed: {res.error}")
        if res.ok and path:
            path.write_text(json.dumps({"task": task.name, "stage": task.stage, "output": res.output,
                                        "cost_usd": res.cost_usd, "seconds": res.seconds}, indent=1))
        self.log(f"[llm] {task.name}: {'ok' if res.ok else 'FAILED'} ${res.cost_usd:.3f} {res.seconds:.0f}s"
                 f" searches={res.web_searches} (run total ${self.spent_usd:.2f})")
        self.ledger.append(res)
        return res

    async def run_all(self, tasks: list[AgentTask]) -> list[AgentResult]:
        return list(await asyncio.gather(*(self.run(t) for t in tasks)))

    def summary(self) -> dict[str, Any]:
        live = [r for r in self.ledger if not r.cached]
        return {"calls": len(self.ledger), "cached": len(self.ledger) - len(live),
                "failed": sum(not r.ok for r in self.ledger), "cost_usd": round(self.spent_usd, 4),
                "web_searches": sum(r.web_searches for r in live)}


def default_backend() -> Backend:
    if os.environ.get("DFS_LLM", "sdk") == "replay":
        return ReplayBackend()
    return SDKBackend()
