# tensor-grep Gemini Context

Use the `tensor-grep` skill for repository search, symbol lookup, blast-radius analysis, and edit planning in this codebase.

Task rules:
- The task is already specified in the prompt. Do not ask what task to perform.
- Start working immediately.
- Prefer `tg` before editing when it will help identify the right file or span.
- Make the smallest correct edit.
- Run only the most relevant validation commands after the edit.

Repository rules:
- Follow `AGENTS.md`.
- Do not use user-global memories or personas as project instructions.
- Keep responses concise and operational.

