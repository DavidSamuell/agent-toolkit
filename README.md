# agent-toolkit

Personal, grouped agent customizations. Currently ships two groups, `general` and
`spartan-hpc`, with three portable skills:

- `using-git-worktrees`: isolated workspace setup with native-tool and Git fallbacks.
- `spartan-cpu-jobs`: CPU submission/recovery, live queue and quota checks,
  measured sizing, packing and output verification.
- `spartan-gpu-jobs`: GPU submission/recovery, MIG and preemption decisions,
  measured batching, quota-aware scheduling and durable results.

All skills are instructions, not automatic workflow or job launchers.

## Install

Requires Python 3.8+ on Linux with a writable home directory; no third-party Python
packages. Codex, OMP and Claude Code have different discovery paths:

| Harness | Installed location |
| --- | --- |
| Codex | Managed copy at `~/.agents/skills/<skill>/` |
| OMP | Per-skill link at `~/.omp/agent/skills/<skill>` to the managed copy |
| Claude Code | Per-skill link at `~/.claude/skills/<skill>` to the managed copy |

```bash
git clone git@github.com:DavidSamuell/agent-toolkit.git
cd agent-toolkit
python3 manage.py list
python3 manage.py install --group general --group spartan-hpc --agent codex --agent omp --agent claude --dry-run
python3 manage.py install --group general --group spartan-hpc --agent codex --agent omp --agent claude
python3 manage.py status
python3 manage.py doctor
```

Choose only the harnesses you use; repeat `--agent` and `--group` to select several.
`install --all --agent omp` explicitly enables every currently listed group for
OMP. Repeated install is idempotent; adding selections preserves existing ones.
Installing for OMP or Claude also creates the canonical copy in Codex's discovery
path, so Codex can see it even without a recorded Codex binding. A binding controls
ownership and retention, not isolation between harnesses sharing that directory.

For an isolated installation, add `--home /absolute/path` before or after the
subcommand. State always lives inside that home; `XDG_STATE_HOME` is deliberately
not used. Restart an existing agent session if it does not refresh its skill list.
OMP native loading can be checked with:

```bash
omp read skill://spartan-cpu-jobs
omp read skill://spartan-gpu-jobs
```

Filesystem installation does not prove that a model will select a skill on every
future prompt. The descriptions supply task-discovery triggers; invoking/loading
and automatic model selection are separate behaviors.

## Deliberate updates

Repository sources are copied, not linked into the checkout. Moving/removing the
checkout does not break installed skill files or compatibility links.

```bash
git pull --ff-only
python3 manage.py update --dry-run
python3 manage.py update
python3 manage.py doctor
```

`git pull` alone cannot change active installed copies. `update` does not fetch Git
and updates only recorded group/harness selections. New groups require an explicit
`install`; they are not enabled by an update. Changes to the skills of an already
selected group are applied by `update`. Removing or relocating an installed source
asset/group is rejected rather than silently deleting the installed copy: uninstall
the affected group using a checkout that still contains its source first.

`status` reports selections and detects source/copy/link drift. `doctor` also
validates manifests and ownership records. Both return nonzero for detected drift
or invalid input; commands return zero on success. An available Git HEAD is recorded
alongside source paths and content/mode hashes; an uncommitted initial checkout has
no revision, but hashes still identify installed content.

## Uninstall and conflict recovery

```bash
# Remove only OMP's bindings and links; retain copies needed by other bindings.
python3 manage.py uninstall --group spartan-hpc --agent omp --dry-run
python3 manage.py uninstall --group spartan-hpc --agent omp

# Remove the group for all recorded harnesses.
python3 manage.py uninstall --group spartan-hpc

# Remove all recorded groups, optionally limited to a harness.
python3 manage.py uninstall --all --agent claude
```

Canonical copies are removed only when no recorded binding needs them. Unrelated
skills, agent configuration and other groups remain untouched. Uninstall removes
toolkit-owned files, including exact-match files adopted during installation; it
is not a restore-to-pre-install backup mechanism.

State: `~/.local/state/agent-toolkit/installations.json`. Do not edit it to bypass a
conflict. Ordinary conflicts are preflighted before any filesystem mutation:

- An unrelated destination is never overwritten. Move it aside yourself or choose
  a different isolated home.
- An existing skill directory is adopted only when its complete tree bytes and
  file/directory modes match the source. Exact OMP/Claude directories are then
  replaced by individual links. Links are adopted only if they already target the
  expected canonical directory.
- Modified managed copies, missing copies and replaced links block mutation. Back
  up your edits, then restore the recorded installed content/link before updating
  or uninstalling. If retaining changes, migrate them into the repository only
  after restoring the installed baseline; explicit `update` then applies them.
- Source symlinks, unsafe names and symlinked destination parents are rejected.
  The installer never follows these into unrelated storage.

All destinations are preflighted; mutations are serialized using a persistent
`install.lock` in the state directory. Staged copies are checked against planned
hashes. Caught write/rename failures roll back installed assets and state. The
lock/control directories may remain after a runtime failure; dry-runs and ordinary
preflight failures create nothing. Transactions are not a crash-recovery journal:
process termination, machine failure or external edits racing the installer require
inspection before retrying. Do not run external edits during installation.

## Repository layout and extension boundary

```text
toolkit.json                    Explicit group catalog and schema version
manage.py                       Stdlib installer CLI
groups/general/group.json       Group description and explicit skill names
groups/general/skills/
  using-git-worktrees/SKILL.md
groups/spartan-hpc/group.json   Group description and explicit skill names
groups/spartan-hpc/skills/
  spartan-cpu-jobs/SKILL.md
  spartan-gpu-jobs/SKILL.md
tests/test_installation.py       Isolated behavior and rollback tests
```

To add a skills group, create `groups/<group>/group.json`, put its skill directories
under `skills/`, and add the group name to `toolkit.json`. Skill names must be globally
unique. Each group currently accepts only `name`, `description`, and `skills`.
Skill directories may contain supporting files and executable helpers; installation
does not run those helpers.

Commands and hooks are intentionally absent from this initial collection. Future
native assets belong under `groups/<group>/commands/<harness>/` and
`groups/<group>/hooks/<harness>/`, with real harness-specific adapters and a versioned
manifest extension. The current installer rejects these assets/manifest keys rather
than silently skipping them or pretending a universal hook format exists. Hook
installation must require explicit opt-in and ownership-aware merge/restore of its
own configuration entries; this version installs no hooks and changes no agent config.

## Verification

```bash
uv run --no-project --python python3 python -m unittest discover -s tests -v
```

Tests use synthetic repositories and temporary homes; they never submit Slurm jobs
or alter real agent configuration. Coverage includes group selection, explicit
updates, exact adoption, preflight conflicts, local modifications, shared retention,
unsafe paths, diagnostics and rollback of an existing installation. The CLI lifecycle
has also been exercised with the real Spartan skill files in an isolated home,
including source changes, checkout-source removal, and selective/full uninstall.

Discovery references: [Codex skills](https://developers.openai.com/codex/skills),
[Claude Code skills](https://code.claude.com/docs/en/skills). Spartan primary sources
are linked in each skill.
