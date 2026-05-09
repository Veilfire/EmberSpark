# Contributing

Thanks for your interest. EmberSpark is Apache 2.0 and accepts contributions.

## Before you start

1. **Read the values.** EmberSpark is explicitly opinionated — bounded autonomy, local-first, declarative, auditable, fail closed. A change that violates these is unlikely to land even if it's otherwise good code.
2. **Read [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md).** The threat model shapes every design decision. If you don't know it, you'll propose things that get refused.
3. **Pick up an issue first.** For anything non-trivial, comment on or open an issue so the direction can be discussed before you start writing code.

## Setup

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd spark
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[openai,anthropic,openrouter,ollama,web,dev]'
python -m spacy download en_core_web_lg
```

Verify your install:

```bash
spark doctor check
pytest tests/unit
```

## Development workflow

1. **Branch from main.** Feature branches prefixed with `feat/`, fix branches with `fix/`, docs with `docs/`.
2. **Write tests first** (or alongside). Every safety-critical path should have a test that proves the gate refuses what it should.
3. **Run the full test suite** before pushing:
   ```bash
   pytest
   ```
4. **Run lint + type checks:**
   ```bash
   ruff check spark tests
   mypy --strict spark
   bandit -r spark
   pip-audit
   ```
5. **Compile-check** after substantial changes:
   ```bash
   python3 -m py_compile $(find spark tests -name "*.py")
   ```
6. **Commit with Co-Authored-By trailer** if you used AI assistance (EmberSpark's own history is full of these).
7. **Open a PR** with:
   - A clear summary of what changed
   - The threat model implications, if any
   - A test plan

## Code style

- Python 3.12+ syntax
- Type annotations on every public function (`mypy --strict` enforces this)
- Ruff for formatting and linting
- Docstrings on every module and public class; one-line for simple functions
- No emojis in source code (unless explicitly requested)
- Pydantic v2 everywhere for data models; `ConfigDict(extra="forbid")` on any model that faces user input

## Writing a new plugin

See [Plugin Authoring](Plugin-Authoring) + [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md).

If the plugin is general-purpose enough to ship as a built-in, open an issue first to discuss the design. The bar for built-ins is high:

- Narrow surface area
- Operator-configurable
- Strict input/output schemas
- Known-good sandbox behavior
- Documented tradeoffs

## Writing new tests

- **Unit tests** under `tests/unit/` — Pure Python, no DB, fast. Mock DB calls with an in-memory sqlite when unavoidable.
- **Integration tests** under `tests/integration/` — Real DB, real TestClient for the web layer, but mock the sandbox and the LLM.
- **Acceptance tests** under `tests/acceptance/` — End-to-end flows that match spec items (see `tests/acceptance/test_v1_acceptance.py`).

Safety-critical paths (SSRF defense, path traversal, secret leakage, sandbox escape) should have tests that exercise the refusal path specifically. A test that proves the happy path works is not enough — prove the unhappy path refuses.

## Docs updates

Any user-visible change needs a docs update:

- Code that changes a public API → update [docs/plugin-authoring.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-authoring.md) or the relevant reference
- New plugin config field → update [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) and the corresponding wiki page
- New permission / gate → update [docs/tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md) and [Concepts: Permissions](Concepts-Permissions)
- New security property → update [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md)

The wiki and docs/ have some overlap. Generally:

- `docs/` is the source-of-truth reference (referenced from code comments, PRs)
- `wiki/` is the user-facing prose layer

Updates can land in either or both.

## Security disclosures

**Do not** file public GitHub issues for security vulnerabilities. Email `security@spark.dev` (placeholder — replace at ship time). Include:

- A clear description of the issue
- Steps to reproduce
- The potential impact
- Any PoC code (if applicable)

We'll coordinate disclosure before landing a fix.

## Licensing

EmberSpark is Apache 2.0. By opening a PR, you agree to license your contribution under the same terms. No CLA.

## Questions

- General questions: GitHub Discussions
- Bug reports: GitHub Issues
- Security: private email
- Feature requests: GitHub Issues, but expect them to be held to the bounded-autonomy values
