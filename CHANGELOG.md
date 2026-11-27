# Changelog

## Unreleased

- README: added a copy-paste prompt for installing chosen groups for chosen harnesses
  by asking an agent in Claude Code or OMP.
- Added the `dspy` group (OmidZamani/dspy-skills, 22 skills including the MIPROv2, GEPA,
  SIMBA and BootstrapFewShot optimizers): plugin for Claude Code, skills for Codex and OMP.
- README: the group table now says what each group does and when to use it; install
  methods per harness moved to a second table.
- Added the `aae` group (CHSZLab/AgenticAlgorithmEngineering): the `aae@aae` plugin for
  Claude Code and the `claude-plugin/skills/aae` skill for Codex and OMP.
- Added `bootstrap.sh`, a fresh-machine entry point: prerequisite checks (including the
  macOS Python stub), clone, dry-run plan, confirmation via the terminal or `--yes`,
  install and `doctor`. It is safe to pipe from `gh api` and has 5 offline tests.
- Added external groups: a group can point at another Git repository and install it
  per harness with `skills` (managed copies), `claude-plugin` or `script` methods,
  with origin-checked checkouts, deliberate fast-forward updates, a CLI preflight,
  `uninstall --skip-native`, and status checks for checkouts and Claude plugins.
- Added the `agent-skills` group (DavidSamuell/agent-skills fork: skills for Codex/OMP,
  plugin for Claude Code) and the `llm-wiki` group (`~/wiki` via its `setup.sh`,
  Claude Code and OMP).
- `install --all` now skips harnesses a group does not support.
- Added 10 offline tests for external groups.
- Introduced the `spartan-hpc` group with CPU and GPU submission skills, including
  mandatory duplicate-work preflight and separate submission/resource quota checks.
- Added the `general` group with the portable `using-git-worktrees` isolation skill.
- Added explicit group/harness installation for Codex, OMP and Claude Code using
  shared managed copies and per-skill compatibility links.
- Added deliberate updates, dry-runs, drift diagnostics and ownership-aware uninstall.
- Added strict manifests, exact-match adoption, path/conflict checks, serialized
  mutations, staged hash validation and rollback on caught write failures.
- Added 18 isolated behavioral regression tests and documented the installation
  lifecycle, safety boundaries and future native command/hook adapter requirements.
