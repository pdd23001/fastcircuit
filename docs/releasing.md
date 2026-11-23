# Releasing

A checklist for maintainers. Publishing a GitHub release publishes the package:
[`release.yml`](https://github.com/pdd23001/fastcircuit/blob/main/.github/workflows/release.yml) builds it, uploads it to PyPI and
attaches the files to the release. Pushing a tag alone publishes nothing.

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

## Optional: build and check locally

The release workflow runs these steps itself (`package.yml`, the same job as CI's
`package` check). To try them before releasing:

```bash
python -m pip install -c ci/constraints.txt --group release
rm -rf dist && python -m build --outdir dist .
python -m twine check --strict dist/*
python -m venv /tmp/fc-wheel && /tmp/fc-wheel/bin/pip install dist/*.whl
(cd /tmp && /tmp/fc-wheel/bin/python -I "$OLDPWD/ci/smoke_installed.py")
```

## Publish

1. Wait for `ci-ok` to pass on the merged commit on `main`.
2. On GitHub, open **Releases → Draft a new release**. Create the tag `vX.Y.Z`
   (exactly `v` + `__version__`) on `main`, title it `fastcircuit X.Y.Z`, and paste
   the changelog section as the notes. Tick **Set as a pre-release** for alpha,
   beta, release-candidate and dev versions (`a`, `b`, `rc`, `dev`), and only
   for those.
3. Click **Publish release**. The **Release** workflow then:
   1. `verify`: refuses to continue unless the tag matches `__version__`, the
      pre-release box matches the version, and the commit is on `main` with
      `ci-ok` green;
   2. `package`: builds the sdist and wheel, runs `twine check --strict`, and
      smoke-tests each in a clean environment;
   3. `publish to PyPI`: uploads through PyPI trusted publishing, in the `pypi`
      environment. No API token exists; PyPI also records digital attestations
      of where the files were built;
   4. `attach to the GitHub release`: uploads the same files to the release.
4. Install from PyPI in a fresh environment and run the smoke test again.

If `verify` failed only because CI on `main` had not finished, re-run the
workflow once `ci-ok` is green. Otherwise, if `verify` or `package` fails,
nothing was published: delete the release (the
`v*` tag cannot be deleted or moved once created, except by an admin), fix the
problem through a pull request, and release again with a new version. If only
the last job fails, re-run it from the workflow page. PyPI never accepts the same
file twice, so a version that reached PyPI cannot be replaced, only yanked; fix it
with a new version.

## One-time setup

PyPI trusts this workflow, not a token. On PyPI, under **Your account →
Publishing → Add a new pending publisher** (before the first release; on the
project's **Settings → Publishing** afterwards), enter:

| Field | Value |
|---|---|
| PyPI project name | `fastcircuit` |
| Owner | `pdd23001` |
| Repository name | `fastcircuit` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

On GitHub, **Settings → Environments → `pypi`** should restrict deployments to
tags matching `v*` and require a maintainer's approval, so that publishing needs
a click on the running workflow too. GitHub only offers these protections on
private repositories with a paid plan; on a public repository they are free.
