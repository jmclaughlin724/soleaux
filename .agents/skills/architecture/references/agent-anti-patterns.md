# Agent Configuration Anti-Patterns

Load this reference when designing or reviewing agent surfaces — prompts, briefs, tool exposure, model routing, and orchestration. Each row names an anti-pattern and the replacement that keeps the surface owned, evaluated, and disposable.

| Anti-pattern | Replacement |
| --- | --- |
| One enormous repository system prompt | Concise `AGENTS.md`, feature-owned prompts, central runtime policy |
| Same instruction repeated in AGENTS, config, prompt, and tool description | One canonical owner, referenced or enforced elsewhere |
| “Think step by step” | Explicit success criteria, evidence, assumptions, checks, and output fields |
| Reasoning summaries used by application logic | Strict output fields and deterministic tool results |
| Highest model, Pro mode, or `max` effort everywhere | Eval-driven workload routing |
| Every installed tool exposed to every task | Task-specific allowlists and least privilege |
| Long examples added “for quality” | Zero-shot first; examples only for measured gaps |
| Dynamic content at the beginning of prompts | Stable cacheable prefix first |
| Remote Prompt Object IDs | Version-controlled prompt builders |
| Generated schemas deployed unchanged | Cleanup, validation, typing, fixtures, and evals |
| Only visible assistant text preserved between tool turns | Preserve reasoning items, calls, outputs, and linkage |
| Blind retry of incomplete responses | Classified recovery policy |
| API keys or provider settings in `.codex/config.toml` | User config, environment, or secret manager |
| Runtime knobs written into `AGENTS.md` | Typed model policy and evaluated configuration |
| Subagents created for every task | Parallel agents only for cleanly independent workstreams |
| PTC used where model judgment is needed after each call | Direct tool calls with model control |
