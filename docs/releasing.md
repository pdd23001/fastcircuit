# Releasing

A checklist for maintainers. Releases are made by hand. Nothing publishes on a
tag push.

## Before the release

1. The latest `ci.yml` run on `main` is green, and so is the latest
   `scheduled.yml` run (all platforms, sanitizers).
2. Qiskit compatibility: the pinned minor series (`pyproject.toml`,
   `TESTED_QISKIT_MINOR` in `src/fastcircuit/_compat.py`) is the newest one the
   full suite passes on. Check the scheduled job's advisory run against newer
   Qiskit.
3. Regenerate `ci/constraints.txt` if dependencies have moved
   (`python ci/update_constraints.py ...`), and review the diff.
4. Set `__version__` in `src/fastcircuit/_version.py`.
5. In `CHANGELOG.md`, move the `Unreleased` entries under the new version with
   today's date, and add a new empty `Unreleased` section.
6. Merge these changes through a pull request.

## Build and check

```bash
python -m pip install -c ci/constraints.txt --group release
rm -rf dist && python -m build --outdir dist .
python -m twine check --strict dist/*
```

Install the wheel and the sdist, each into a fresh virtual environment, and run
the smoke test from outside the repository:

```bash
python -m venv /tmp/fc-wheel && /tmp/fc-wheel/bin/pip install dist/*.whl
(cd /tmp && /tmp/fc-wheel/bin/python -I "$OLDPWD/ci/smoke_installed.py")
```

## Publish

1. Tag the release commit: `git tag -s vX.Y.Z -m "fastcircuit X.Y.Z"`, then push
   the tag.
2. Upload the checked files from `dist/` to PyPI.
3. Create a GitHub release from the tag, with the changelog section as the notes
   and the files from `dist/` attached.
4. Install from PyPI in a fresh environment and run the smoke test again.
