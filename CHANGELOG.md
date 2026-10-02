# Changelog

## Unreleased

- Introduced the `spartan-hpc` group with CPU and GPU submission skills, including
  mandatory duplicate-work preflight and separate submission/resource quota checks.
- Added explicit group/harness installation for Codex, OMP and Claude Code using
  shared managed copies and per-skill compatibility links.
- Added deliberate updates, dry-runs, drift diagnostics and ownership-aware uninstall.
- Added strict manifests, exact-match adoption, path/conflict checks, serialized
  mutations, staged hash validation and rollback on caught write failures.
- Added 18 isolated behavioral regression tests and documented the installation
  lifecycle, safety boundaries and future native command/hook adapter requirements.
