# Evaluation Dimensions

Load this reference when defining how to measure whether a component, agent workflow, or task outcome meets its contract. Each dimension is scored independently; a change is only done when the dimensions relevant to its contract are measured.

| Dimension | What to measure |
| --- | --- |
| Task success | Did the requested outcome occur? |
| Correctness | Ground-truth or reviewer score |
| Constraint adherence | Public API, scope, permissions, formatting |
| Evidence quality | Sources, citations, command results, traceability |
| Tool selection | Correct tool versus unnecessary or unsafe tool |
| Tool arguments | Validity and semantic correctness |
| Structured output | Schema adherence and runtime validation |
| State handling | Correct continuity across tool and model turns |
| Approval behavior | No unapproved external or destructive action |
| Incomplete responses | Rate, reason, and recovery success |
| Tokens | Input, visible output, reasoning, cache write, cache read |
| Performance | Time to first token and total latency |
| Economics | Per-task and successful-task cost |
| Safety | Refusal, safeguard, and false-positive behavior |
