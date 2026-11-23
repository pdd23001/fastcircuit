# Continuous integration

Two GitHub Actions workflows. Both run with a read-only token
(`permissions: contents: read`), check out without persisting credentials, pin
every action to a full commit SHA (the version is in a comment beside it), set a
timeout on every job, and install dependencies from `ci/constraints.txt`.

## `ci.yml`: every pull request and push to `main`

| Check name | What it does |
|---|---|
| `lint` | `tox -e lint`: `ruff check`, `ruff format --check`, `mypy` for Linux and macOS, `actionlint` with shellcheck |
| `test (<os>, py<version>, <backend>)` | Unit, component and integration tests on `ubuntu-24.04` and `macos-15` (arm64), Python 3.11 and 3.14, LLVM and C backends: 8 jobs. Tests run against the *installed* package (non-editable). Branch coverage is collected |
| `coverage` | Combines the coverage data from every test job and fails below `fail_under` (`pyproject.toml`). Writes a Markdown summary and uploads the HTML report |
| `package` | Builds the sdist and wheel, runs `twine check --strict`, installs **each** into a clean virtual environment, and runs `ci/smoke_installed.py` from outside the repository: correct circuit, second-process cache reuse, `doctor`. Uploads `dist/` |
| `ci-ok` | Succeeds only if all of the above succeeded |

A newer push to a pull request cancels its running CI. Runs on `main` always finish.

## `scheduled.yml`: weekly (Mondays 04:17 UTC) and on demand

| Check name | What it does |
|---|---|
| `matrix (<os>, py<version>, <backend>)` | `ubuntu-24.04` (x86-64), `ubuntu-24.04-arm` (aarch64), `macos-15` (arm64), `macos-15-intel` (x86-64) × Python 3.11–3.14 × `auto`/`c` = 32 jobs. `auto` is what users get: LLVM where llvmlite installs, the C backend otherwise (Intel macOS on 3.14) |
| `sanitizers (ubsan)` / `sanitizers (asan)` | The C backend's generated code built with UndefinedBehaviorSanitizer / AddressSanitizer on Linux, running the component and integration tests |
| `advisory (newest Qiskit, pre-releases allowed)` | Installs the newest Qiskit (pre-releases included) with `FASTCIRCUIT_ALLOW_UNTESTED_QISKIT=1`. `continue-on-error`: an early warning that Qiskit's experimental C API changed, never a blocker |

The scheduled workflow is not a required check. A red run must be triaged:
- a platform regression is a bug;
- an advisory failure starts the work for the next Qiskit minor release.

### Sanitizer limits

Only code fastcircuit generates for the **C backend** is instrumented. CPython,
NumPy, llvmlite and Qiskit's prebuilt Rust are not, so memory errors inside them
are invisible. The LLVM backend's JIT-compiled code is not instrumented either.
LeakSanitizer is off, because the uninstrumented interpreter's allocations would
be reported as leaks. ASan's runtime is preloaded (`LD_PRELOAD`) because the Python
executable isn't built with it.

## Branch protection (repository settings; not applied by these files)

On `main`:
- require a pull request before merging;
- require status checks to pass, with the single required check **`ci-ok`**, and the
  branch up to date before merging;
- do not allow force pushes or deletions.

Requiring only `ci-ok` keeps the protection rule stable when the test matrix
changes. Individual job names may change; `ci-ok` will not.

## Reproducing CI locally

`tox.ini` defines each check with the versions in `ci/constraints.txt`. The `lint`
job runs `tox -e lint` itself.

```bash
python -m pip install -c ci/constraints.txt -e . --group dev
tox                  # lint, then the tests on the default and C backends
tox -e coverage      # both backends, combined, against the coverage floor
tox -e package       # build the sdist and wheel, twine check
pre-commit install   # the cheap checks (ruff, YAML/TOML, actionlint) before each commit
```

Coverage includes the Python child processes the integration tests start
(`patch = ["subprocess"]` in `pyproject.toml`), since some code paths, such as
re-running a script that contains blocks, only run there.

## Pinned dependencies

`ci/constraints.txt` is generated, never edited by hand. It holds exact versions
per Python version, resolved with pip's own resolver:

```bash
python ci/update_constraints.py python3.11 python3.12 python3.13 python3.14
```

llvmlite is not in it; `pyproject.toml` pins it per platform. Regenerate the file
when a dependency group changes or to pick up new releases, and review the diff.

## Coverage baseline

The floor was set when branch coverage measured 93% (both backends combined).
`fail_under = 90` leaves a 3-point margin for platform and
Python-version branches. Both code generators are at 100%. Most of the uncovered
lines are cache-recovery paths that run inside child processes, which coverage
does not measure, plus `doctor`'s optional checks. Raise the floor as coverage
improves. Never lower it to let a change pass.
