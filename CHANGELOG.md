---
doc_id: fusion-science/changelog
product: fusion-science
class: app
entry: service
doc_type: changelog
title: { zh: "变更日志", en: "Changelog" }
audience: engineer
status: draft
version: 1.0.21
updated: 2026-11-07
owner: "@dahai"
source_of_truth:
  - CHANGELOG.md
  - README.md
---

# Changelog

All notable changes to fusion-science are documented here. Release-by-release
history up to v1.0.17 lives in README.md ("Architecture" phase sections and
patch-release tables); this file tracks changes going forward in
Keep-a-Changelog format.

## [Unreleased]

## [1.0.21] - 2026-11-26

### Added

- CI gate for the no-docstring convention (PR #52, refs dahai80/fusion#9): `ci/check_docstrings.py` AST-scans all tracked `.py` files and fails lint with file:line locations when any docstring is found; wired into the lint job of `.github/workflows/ci.yml`

## [1.0.20] - 2026-11-26

### Fixed

- Closes #49, closes #50: enforce no-docstring convention — converted 316 docstrings to `#` comments across `fusion_science/`, `tests/`, `scripts/`, `ci/`; restored CLI `--help` text via click `help=` on all 12 commands; zero docstrings remain (AST-verified)

## [1.0.19] - 2026-11-16

### Changed
- Docs: quoted the `owner` frontmatter value so strict YAML parsers accept it (PR #45).

### CI
- Docs workflow gains concurrency cancel-in-progress, pip caching, and a docs-only path filter so markdown-only PRs skip the heavy test job (fusion#2 checklist, PR #47).

## [1.0.18] - 2026-11-07

### Added
- Adopted the fusion docs-system v1.1 contract (grid position app x service):
  `docs/product-page.md`, `docs/get-started.md`, `docs/api-reference.md`,
  `docs/config-reference.md`, `docs/architecture.md`, `docs/troubleshooting.md`,
  12-key frontmatter on in-scope markdown, and CI validation via
  `ci/validate_docs.py` gating markdown PRs (`.github/workflows/docs.yml`).
