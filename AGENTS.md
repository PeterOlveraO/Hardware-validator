# Hardware Validator

Hardware Validator is a Rust project for inspecting and validating computer
hardware on Linux. It is developed incrementally; the active prompt defines the
scope of each version and task.

## Project rules

- Support stable Rust 1.85 or newer (edition 2024) on Linux.
- Keep code and identifiers in English.
- Keep modules small and organized by responsibility or hardware component.
- Model unknown values with `Option` and let the type system carry invariants
  instead of runtime type checks.
- Treat unavailable hardware information as a normal condition; do not crash
  when an optional Linux file or field is missing.
- Never guess hardware data. Use an explicit unknown value when necessary.
- Do not require root or perform destructive hardware operations.
- Third-party crates are allowed when they are useful for the active task. Add
  only justified dependencies to `Cargo.toml` and commit `Cargo.lock`.
- Do not require or invoke external applications, system utilities, or remote
  services unless the active prompt or the user explicitly allows them.
- Do not implement features outside the active prompt.

## Workflow

- Inspect the repository before editing and preserve existing behavior.
- Make the smallest coherent change that completes the requested task.
- Add or update tests for behavior introduced or changed.
- Run `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, and
  `cargo test` before finishing.
- Do not modify unrelated files, create commits, or publish releases unless
  explicitly requested.
- Finish by summarizing changed files, verification performed, and remaining
  limitations.
