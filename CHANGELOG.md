# Changelog

All notable changes to `research-portal` are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- GitHub Actions release workflow (`release.yml`) publishing to PyPI
  via [trusted publishing](https://docs.pypi.org/trusted-publishers/)
  on tag push; no API tokens to manage.
- Python 3.13 in the CI matrix.
- `pytest --cov` coverage reporting with a 60% lower bound gate (alpha
  baseline; raise as test surface grows).
- `twine check dist/*` in the clean-build CI job so metadata issues
  fail CI instead of PyPI rejecting the upload.
- `.gitattributes` pinning source files to LF endings (prevents
  Windows-contributor commits from tripping `ruff format --check` on
  the Linux CI runner).
- PyPI metadata polish in `pyproject.toml`: expanded classifiers,
  extra keywords, `Documentation` / `Changelog` URLs.

### Changed
- Ruff lint rules extended with `SIM` (flake8-simplify) on top of the
  existing `E, F, I, W, B, UP` set.

## [0.1.6] and earlier

See https://github.com/ahb-sjsu/atlas-portal/releases for release notes
prior to the introduction of this file.
