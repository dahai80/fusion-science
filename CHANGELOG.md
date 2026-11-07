---
doc_id: fusion-science/changelog
product: fusion-science
class: app
entry: service
doc_type: changelog
title: { zh: "变更日志", en: "Changelog" }
audience: engineer
status: draft
version: 1.0.17
updated: 2026-11-07
owner: @dahai
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

### Added
- Adopted the fusion docs-system v1.1 contract (grid position app x service):
  `docs/product-page.md`, `docs/get-started.md`, `docs/api-reference.md`,
  `docs/config-reference.md`, `docs/architecture.md`, `docs/troubleshooting.md`,
  12-key frontmatter on in-scope markdown, and CI validation via
  `ci/validate_docs.py` gating markdown PRs (`.github/workflows/docs.yml`).
