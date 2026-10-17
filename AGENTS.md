# Project rules for the coding agent (Debug Dojo backend)

You are building the BACKEND of Debug Dojo, a hackathon project, inside backend/.
Source of truth for the API is docs/frontend-prompt.md (section "API CONTRACT") and docs/API_CONTRACT.md once it exists.

ANTI-HALLUCINATION RULES (follow strictly)
1. Never invent a library, function, CLI flag, Docker option, or API signature. Before using any third-party package, run `pip show <package>` or `pip install <package>` and read its real installed version. If unsure how an API works, read the installed package source or official docs. If you still cannot verify, STOP and ask me.
2. Never claim something works unless you ran it in this session and saw the output. Always show me the command and the real output.
3. Do only the current phase. Do not add features I did not ask for. Do not refactor unrelated files.
4. Write tests for every module you create, run them, and report pass/fail counts honestly. If a test fails, say so; never edit a test just to make it pass without telling me why.
5. If a requirement conflicts with another, stop and ask. Do not guess silently.
6. Use only Python 3.11+ standard library plus these packages unless I approve more: fastapi, uvicorn, pydantic, python-dotenv, pytest, httpx, google-genai (verify the exact package name on pip before installing).
7. Use plain sqlite3 with parameterised queries. No string-formatted SQL.
8. Never print, log, or commit any API key or secret. Secrets come only from environment variables loaded from backend/.env. .env is git-ignored.

SECURITY RULES (non-negotiable)
- Learner code NEVER runs inside the FastAPI process. It runs only inside the Docker sandbox.
- Hidden test inputs, expected outputs, program outputs on hidden tests, and exception messages must never appear in: API responses, logs, error messages, AI prompts, the database, or frontend-visible files.
- Learner-facing test outcomes are exactly: passed, wrong_output, runtime_error, timeout. Memory, output and disk limit kills are runtime_error with a "limit" field.
- AI (Gemini) never decides pass or fail. Deterministic code decides.
- No hint or explanation may contain complete or partial solution code.

WORKFLOW RULES
- End every task with: (a) list of files changed, (b) commands run with real output, (c) what is NOT done or uncertain.
- Keep functions small and typed. Add docstrings explaining WHY for security-relevant code.
