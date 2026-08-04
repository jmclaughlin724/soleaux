# Soleaux Rust workspace

One binary crate, `soleaux-ast-grep-worker`: the supervised JSONL worker for structural search and rewrite. It reads one JSON request per stdin line, writes one JSON response per stdout line (stderr only for logs), and serves `ping`, `shutdown`, and `structural` requests with an 8 MiB frame cap. The field vocabulary follows `src/soleaux/contracts/structural.py`, and the JSONL conventions follow `src/soleaux/structural/worker.py`.

## Pin policy

The ast-grep crates (`ast-grep-core`, `ast-grep-language`, `ast-grep-config`) are pinned exactly to `=0.45.0` and `Cargo.lock` is committed. The upstream Rust API is not semver-stable: minor releases rename and restructure public items, so a caret requirement would break builds without any code change here. Bump all three pins together, in one change, after revalidating the worker against the new upstream source.

## Managed build

```sh
pnpm run soleaux:rust:check
```

This runs `cargo test --locked` and `cargo build --release --locked` against this workspace. The release binary lands at `target/release/soleaux-ast-grep-worker`.
