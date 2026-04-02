# Contributing to faceguard

Thanks for your interest. This is a personal security tool — contributions that add complexity without clear benefit will be declined. Contributions that improve reliability, fix real bugs, or add well-scoped features are welcome.

---

## Before opening a PR

- Check open issues first to avoid duplicate work
- For non-trivial changes, open an issue describing what you want to change and why before writing code
- If it's a bug fix, include steps to reproduce the bug

---

## Development setup

```bash
git clone https://github.com/fady17/faceguard.git
cd faceguard

uv venv .venv
brew install cmake
uv pip install -r requirements.txt
uv pip install git+https://github.com/ageitgey/face_recognition_models
uv pip install -r requirements-dev.txt

# Scaffold and configure
make setup
# Edit ~/.faceguard/config.json with your Discord webhook
make enroll
```

Full developer documentation is in [README.dev.md](README.dev.md).

---

## Running tests

```bash
make test-suite
```

The test suite runs without a camera, without Discord, and without LM Studio. All external calls are mocked. Tests must pass locally before pushing.

For the guard core tests (`tests/test_guard_core.py`), `face_recognition` must be installed. The tests mock the face detection itself — no real camera is opened.

---

## Code standards

**Style**
- Python 3.10+ features are fine
- Type annotations on all public functions and methods
- Docstrings on all public modules — one paragraph describing responsibility, followed by "What this module deliberately does NOT do" where relevant
- No star imports

**Error handling**
- `guard_core.run()` and all alert functions must never raise — return typed results or `None`
- Exceptions should be caught at the boundary of each layer, not propagated up
- Every except clause logs with context using `get_logger()`
- Prefer specific exceptions over bare `except Exception` — use bare only as a final safety net

**Testing**
- All new code must have tests in `tests/`
- Use the fixtures in `conftest.py` — don't create new temp directories manually
- Tests must not hit the network, open the camera, or play sounds
- Test failure modes explicitly — not just the happy path
- `assert` statements must have descriptive failure messages for non-obvious conditions

**No new dependencies** without a discussion in an issue first. The dependency count is intentional — `face_recognition`, `opencv-python`, `requests`. That's the whole runtime surface.

---

## Architecture constraints

These are non-negotiable — PRs that violate them will be closed:

1. `guard_core.py` must not import from `alerts/` — the recognition engine has no knowledge of alerting
2. `alerts/` must not import from `guard_core.py` — alerts consume `GuardResult`, nothing else
3. Every alert channel is a separate file in `alerts/` with a single `send_*` function — no mega-dispatcher
4. Config validation happens only in `config.py` — no `if cfg.get(...)` patterns elsewhere
5. All path expansion happens in `config.py` — no `Path("~").expanduser()` outside that module

---

## What we're not looking for

- GUI/web interface — this is a headless background tool
- Cloud sync of the roster — by design, face data stays local
- Windows or Linux support — macOS-specific tools (`afplay`, `launchctl`) are load-bearing
- Additional notification channels in the same PR as a bug fix — keep scope tight
- Rewrites of working modules — prefer targeted fixes

---

## Submitting a PR

1. Fork the repo, create a branch: `git checkout -b fix/camera-retry-logic`
2. Make your change with tests
3. Run `make test-suite` — all 91+ tests must pass
4. Update `CHANGELOG.md` under `[Unreleased]`
5. Open the PR with a description of what changed and why
6. Reference any related issue: `Fixes #42`

PR titles should follow: `fix:`, `feat:`, `docs:`, `test:`, `refactor:` prefixes.

---

## Reporting security issues

If you find a security issue (e.g. a bypass for the face recognition check), **do not open a public issue**. Email the maintainer directly. Include steps to reproduce and your assessment of impact.