# Hardware Validator

Hardware Validator is a Python project for inspecting and validating computer
hardware on Linux. It is developed incrementally; the active prompt defines the
scope of each version and task.

## Project rules

- Support Python 3.11 or newer on Linux.
- Keep code and identifiers in English.
- Keep modules small and organized by responsibility or hardware component.
- Use type hints for public functions and data models.
- Treat unavailable hardware information as a normal condition; do not crash
  when an optional Linux file or field is missing.
- Never guess hardware data. Use an explicit unknown value when necessary.
- Do not require root or perform destructive hardware operations.
- Third-party Python libraries are allowed when they are useful for the active
  task. Add only justified dependencies and record them in the project dependency
  file.
- Do not require or invoke external applications, system utilities, or remote
  services unless the active prompt or the user explicitly allows them.
- Do not implement features outside the active prompt.

## Workflow

- Inspect the repository before editing and preserve existing behavior.
- Make the smallest coherent change that completes the requested task.
- Add or update tests for behavior introduced or changed.
- Run the relevant tests and syntax checks before finishing.
- Do not modify unrelated files, create commits, or publish releases unless
  explicitly requested.
- Finish by summarizing changed files, verification performed, and remaining
  limitations.
