# Contributing to fastcircuit

Thank you for helping. Contributions use the standard GitHub flow: open an
issue, fork the repository, make your change on a branch, and open a pull
request. This guide walks through each step.

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Contents

- [Reporting bugs and asking for features](#reporting-bugs-and-asking-for-features)
- [Choosing something to work on](#choosing-something-to-work-on)
- [Setting up a development environment](#setting-up-a-development-environment)
- [Running the checks](#running-the-checks)
- [Making a change](#making-a-change)
- [Opening a pull request](#opening-a-pull-request)
- [Code review](#code-review)
- [Use of generative AI](#use-of-generative-ai)
- [Style](#style)
- [Licensing of contributions](#licensing-of-contributions)

## Reporting bugs and asking for features

Search the [issues](https://github.com/pdd23001/fastcircuit/issues) first, then
[open a new one](https://github.com/pdd23001/fastcircuit/issues/new/choose). For a
bug, include:
- the output of `python -m fastcircuit doctor`;
- the smallest block that shows the problem, and the values it was run with;
- what happened (the full traceback) and what you expected.

A block that builds a different circuit from the same code without
`with fastcircuit:`, or raises a different exception, is always a bug. Please
report it even if you have a workaround.

Report security problems privately, as [SECURITY.md](SECURITY.md) describes, not
in a public issue.

## Choosing something to work on

- Issues labelled
  [`good first issue`](https://github.com/pdd23001/fastcircuit/labels/good%20first%20issue)
  are small and fully scoped, ideal for a first contribution.
- Issues labelled
  [`help wanted`](https://github.com/pdd23001/fastcircuit/labels/help%20wanted)
  are larger, or need some investigation.

Comment on the issue to say you are working on it. For anything bigger than a
small fix, agree on the approach in the issue before writing the code; it saves
everyone a rewrite.

## Setting up a development environment

You need Linux or macOS, Python 3.11 or newer, and git. A C compiler is
optional; without one, the C-backend tests are skipped or fail with a clear
message. On macOS run `xcode-select --install`; on Linux install `gcc` or `clang`
and the Python headers (`python3-dev` or `python3-devel`).

**1. Fork and clone.** Click **Fork** on
[the repository page](https://github.com/pdd23001/fastcircuit), then clone your
fork and add the main repository as `upstream`:

```bash
git clone https://github.com/<your-username>/fastcircuit.git
cd fastcircuit
git remote add upstream https://github.com/pdd23001/fastcircuit.git
```

**2. Create a virtual environment and install.** One command installs
fastcircuit in editable mode with every development tool (pytest, ruff, mypy,
pre-commit, tox), at the versions CI uses:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "pip>=25.1"     # 25.1+ understands --group
python -m pip install -c ci/constraints.txt -e . --group dev
```

**3. Install the git hooks, and check the environment.**

```bash
pre-commit install            # ruff and file checks on every commit
python -m fastcircuit doctor  # backend, Qiskit, compiler and cache
```

`pre-commit` runs ruff (lint and formatting), YAML/TOML checks and actionlint on
the files you commit, and blocks the commit if they fail.

## Running the checks

[tox](https://tox.wiki) runs each check in its own clean environment, with the
same pinned versions as CI. CI's lint job runs `tox -e lint` itself, so a lint
run that passes locally passes in CI.

| Command | What it does | Time |
|---|---|---|
| `tox` | lint, then the tests on both backends: run this before opening a pull request | ~3 min |
| `tox -e lint` | ruff, ruff format check, mypy (Linux and macOS rules), actionlint | ~10 s after the first run |
| `tox -e format` | applies ruff formatting and safe lint fixes | seconds |
| `tox -e py` | tests on the default backend (`tox -e py312` for a specific Python) | ~1 min |
| `tox -e c` | tests on the C backend | ~1 min |
| `tox -e coverage` | both backends with combined branch coverage, failing below CI's floor | ~2 min |
| `tox -e package` | builds the sdist and wheel and checks them as PyPI would | seconds |

Arguments after `--` go to pytest:

```bash
tox -e py -- tests/unit                   # one layer
tox -e py -- tests/component -k cache     # matching tests only
```

While iterating, you can also run the tools directly in your virtual
environment, for example `pytest tests/unit` or `ruff check .`.

### The test suite

Tests are layered. Add yours to the layer that matches what it checks:

| Directory | Marker | What it covers |
|---|---|---|
| `tests/unit` | `unit` | Analysis, rewriting, cache keys, numerics and platform rules. No native compilation; runs in about a second |
| `tests/component` | `component` | Each part compiled and run against real Qiskit: gates, language features, blocks, runtime errors, cache, build failures |
| `tests/integration` | `integration` | Everything together, including separate processes: scripts with blocks, cross-process cache reuse |

Further markers: `slow` (left out with `-m "not slow"`), `needs_c_backend`
(needs a C compiler), and `needs_llvmlite` (skipped automatically without
llvmlite).

Tests always use **real Qiskit**. Mocks are only for injecting specific
failures, such as a compiler that crashes or a corrupted cache file.

## Making a change

**1. Start a branch from the latest `main`.**

```bash
git fetch upstream
git switch -c fix-measure-bounds upstream/main
```

**2. Write a test that fails without your change**, then make it pass. A change
to the compiler is compared against the same code run as plain Python (the
`as_block` helper in `tests/support/blocks.py` gives you both) and runs on both
backends.

**3. Read [docs/architecture.md](docs/architecture.md) before changing the
compiler.** Its invariants (identical results to Python, no silent fallback,
validated inputs) are not negotiable.

**4. Update the documentation** for anything users can see: the pages in
[`docs/`](docs/), docstrings, and a line under `Unreleased` in
[CHANGELOG.md](CHANGELOG.md).

**5. Run `tox`** and fix anything it reports (`tox -e format` fixes formatting).

**6. Commit and push to your fork.** Write commit messages that say what changed
and why: a short summary line, a blank line, then details if needed.

```bash
git add -A
git commit
git push -u origin fix-measure-bounds
```

If `main` moves on while you work, rebase onto it (`git fetch upstream && git
rebase upstream/main`, then `git push --force-with-lease`). There is no need to
do this unless there are conflicts.

## Opening a pull request

Open a pull request from your branch to `pdd23001/fastcircuit:main` and fill in
the template. If the work is not finished but you want early feedback, open it
as a **draft**.

### Pull request checklist

Before marking a pull request ready for review, check that:

1. **The tests pass locally.** You ran `tox` (lint and both backends), and it
   passed. CI runs the same checks on Linux and macOS and several Python
   versions; the required check is `ci-ok` ([docs/ci.md](docs/ci.md)).
2. **New behaviour has tests**, and bug fixes have a test that failed before
   the fix.
3. **No test was weakened**, the coverage floor was not lowered, and no
   validation was removed to make a change pass. If a test itself is wrong,
   explain why in the pull request.
4. **The documentation is updated**, including a `CHANGELOG.md` line for anything
   users notice.
5. **The title is a clear summary** that makes sense without opening the pull
   request, such as "Check clbit bounds in blocks" rather than "Fix #123". Pull
   requests are squash-merged, so the title becomes the commit message.
6. **The description links the issue** with `Fixes #123`, so GitHub closes it on
   merge.
7. **One logical change per pull request.** Unrelated fixes go in separate pull
   requests.
8. **New dependencies were agreed in an issue first.** Runtime dependencies are
   kept to Qiskit, NumPy and llvmlite.
9. **Any use of generative AI is disclosed** in the template's checkboxes, as
   [Use of generative AI](#use-of-generative-ai) describes.

## Code review

Every change, including the maintainer's own, goes through a pull request and
review. Anyone can review; a maintainer approves and merges.

When reviewing:
- check the checklist above, correctness against Python's behaviour, edge cases
  and tests;
- make concrete suggestions, and ask rather than assume ("Would raising
  `TypeError` here be clearer?");
- review the whole pull request in one pass where you can;
- leave formatting to the tools. Style preferences that do not affect
  correctness or readability are not review comments.

When your pull request is reviewed, ask if a comment is unclear, and explain your
reasoning if you disagree; review is a conversation. Push follow-up commits to
the same branch rather than opening a new pull request.

## Use of generative AI

By "generative AI" we mean tools such as large language models (LLMs) and coding
agents. You may use them, under these rules:

- **A human drives every interaction.** Agents must not open pull requests or
  issues, or post comments, on their own. You submit the change and you answer
  the review.
- **You are responsible for what you submit**, however it was produced. You have
  reviewed and understood every line, and you can explain why it is right in
  your own words. An explanation generated by an LLM does not count.
- **Disclose it.** If generative AI wrote any of the code or text you submit
  (code, tests, documentation, commit messages, the pull request description or
  comments), say so in the pull request, using the checkboxes in the template.
- **Respect licenses and terms.** Your use of the tool and of its output must not
  break the tool's terms or the license of any code it reproduced.
- **Be worth the review.** A contribution must be worth more than the maintainer
  time it takes to review. As a rule of thumb, put in at least as much effort as
  the review will take. Generated code and prose tend to be long: make the change
  as small and the description as short as you can.
- **Not for `good first issue`.** Those issues exist so that newcomers can learn
  the contribution process; do them without generative AI.

We recommend writing pull request descriptions and review replies yourself:
imperfect human words are more useful than generated ones, whatever your
English. Maintainers may close a pull request whose review would cost more than
it brings, without needing to show that AI was used.

This policy follows the spirit of
[Qiskit's generative AI policy](https://github.com/Qiskit/qiskit/blob/main/docs/devguide/contributing.md#use-of-generative-ai),
which in turn cites the [LLVM](https://llvm.org/docs/AIToolPolicy.html) and
[NumPy](https://numpy.org/devdocs/dev/ai_policy.html) policies.

## Style

- Formatting is `ruff format` (100-character lines) and lint is `ruff check`;
  both run in `pre-commit` and `tox -e lint`.
- `mypy --strict` must pass for `src/fastcircuit`.
- Error messages say what is wrong, where (file and line), and what to do
  instead.
- Comments explain *why*, not *what*.

## Licensing of contributions

fastcircuit is licensed under the [Apache License 2.0](LICENSE.txt). By submitting
a contribution, you agree that it is licensed under the same terms (section 5 of
the license), and that you have the right to submit it.
