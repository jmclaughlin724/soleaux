<source_index> <provenance> This skill's reference set was consolidated from the MIT-licensed `https://github.com/wdm0006/python-skills`. Its source topics — api-design, code-quality, community, documentation, library-review, packaging, performance, project-setup, release-management, security-audit, and testing-strategy — were merged into this skill's local `references/` and `workflows/` files; no upstream paths exist in this repository.

Attribution: MIT License, Copyright (c) 2025 Will McGinnis. </provenance>

<official_upstream_sources> Use these before making current-version-sensitive claims:

- Python Packaging User Guide: `https://packaging.python.org/en/latest/guides/writing-pyproject-toml/`
- PyPA `pyproject.toml` specification: `https://packaging.python.org/en/latest/specifications/pyproject-toml/`
- PyPA dependency groups specification: `https://packaging.python.org/en/latest/specifications/dependency-groups/`
- PyPA command-line tools guide: `https://packaging.python.org/en/latest/guides/creating-command-line-tools/`
- PyPA binary extensions guide: `https://packaging.python.org/en/latest/guides/packaging-binary-extensions/`
- PyPA src layout discussion: `https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/`
- PyPI Trusted Publishing: `https://docs.pypi.org/trusted-publishers/`
- PyPI Digital Attestations: `https://docs.pypi.org/attestations/`
- GitHub Actions Python guide: `https://docs.github.com/en/actions/tutorials/build-and-test-code/python`
- GitHub Actions secure use: `https://docs.github.com/en/actions/reference/security/secure-use`
- Python stdlib CLI/runtime/docs: `https://docs.python.org/3/library/argparse.html`, `https://docs.python.org/3/library/venv.html`, `https://docs.python.org/3/library/asyncio-task.html`, `https://docs.python.org/3/library/concurrent.futures.html`, `https://docs.python.org/3/library/logging.html`, `https://docs.python.org/3/library/logging.config.html`, `https://docs.python.org/3/library/pdb.html`, `https://docs.python.org/3/library/faulthandler.html`, `https://docs.python.org/3/library/tracemalloc.html`
- uv project and dependency docs: `https://docs.astral.sh/uv/concepts/projects/`, `https://docs.astral.sh/uv/concepts/projects/dependencies/`
- tox docs: `https://tox.wiki/en/latest/`
- pre-commit docs: `https://pre-commit.com/`
- OpenTelemetry Python docs: `https://opentelemetry.io/docs/languages/python/`
- Pydantic v2 docs: `https://docs.pydantic.dev/latest/`
- setuptools extension modules: `https://setuptools.pypa.io/en/latest/userguide/ext_modules.html`
- Wheel docs: `https://wheel.readthedocs.io/en/stable/`
- maturin docs: `https://www.maturin.rs/`
- Ruff configuration: `https://docs.astral.sh/ruff/configuration/`
- mypy docs: `https://mypy.readthedocs.io/en/stable/`
- Python typing docs: `https://docs.python.org/3/library/typing.html`
- Python exceptions and warnings: `https://docs.python.org/3/library/exceptions.html`, `https://docs.python.org/3/library/warnings.html`
- pytest fixtures and parametrization: `https://docs.pytest.org/en/stable/how-to/fixtures.html`, `https://docs.pytest.org/en/stable/how-to/parametrize.html`
- Hypothesis docs: `https://hypothesis.readthedocs.io/en/latest/`
- Sphinx autodoc and napoleon: `https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html`, `https://www.sphinx-doc.org/en/master/usage/extensions/napoleon.html`
- Python profiling and memory tracing: `https://docs.python.org/3/library/profile.html`, `https://docs.python.org/3/library/tracemalloc.html`
- Bandit docs: `https://bandit.readthedocs.io/en/latest/`
- pip-audit: `https://pypa.github.io/pip-audit/`
- Semgrep docs: `https://semgrep.dev/docs/`
- detect-secrets: `https://github.com/Yelp/detect-secrets`
- OWASP injection and command injection references: `https://owasp.org/www-community/Injection_Flaws`, `https://owasp.org/www-community/attacks/Command_Injection`
- Semantic Versioning: `https://semver.org/`
- Keep a Changelog: `https://keepachangelog.com/en/1.1.0/`
- Optional web app docs: `https://fastapi.tiangolo.com/`, `https://flask.palletsprojects.com/`, `https://docs.djangoproject.com/`, `https://docs.sqlalchemy.org/`
- Optional data/ML docs: `https://pandas.pydata.org/docs/`, `https://numpy.org/doc/stable/`, `https://docs.jupyter.org/en/latest/`, `https://jupyter-server.readthedocs.io/en/latest/operators/security.html`, `https://scikit-learn.org/stable/model_persistence.html` </official_upstream_sources>

<verification_notes>

- PyPA docs confirm `pyproject.toml` is the packaging/tool configuration home and current license metadata uses SPDX expressions.
- PyPA docs define `[project.scripts]` entry points and `[dependency-groups]` for local dependency sets that are not built package metadata.
- PyPA docs explain the `src/` layout prevents accidental import of non-installed source tree code.
- Python stdlib docs cover argparse, venv, asyncio task groups, executors, logging configuration, pdb, fault tracebacks, and memory allocation tracing.
- uv docs cover project dependency fields, dependency groups, lock/sync behavior, and project structure.
- PyPI/GitHub docs support trusted publishing through OIDC, attestations, and Python matrix CI.
- Pydantic v2 docs support using models for untrusted boundary validation and serialization.
- setuptools, wheel, and maturin docs support native-extension and binary wheel packaging decisions.
- Ruff, mypy, pytest, Hypothesis, Sphinx, Bandit, pip-audit, Semgrep, and detect-secrets guidance is tool-specific and should be checked upstream before adding or changing configuration. </verification_notes> </source_index>
