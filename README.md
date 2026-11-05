# agent-toolkit

Personal, grouped agent customizations and the single entry point for setting up
my agent tools on a fresh machine, for Codex, OMP and Claude Code.

| Group | Kind | What it installs | Codex | OMP | Claude Code |
| --- | --- | --- | --- | --- | --- |
| `general` | local | `using-git-worktrees` | skills | skills | skills |
| `spartan-hpc` | local | `spartan-cpu-jobs`, `spartan-gpu-jobs` | skills | skills | skills |
| `agent-skills` | external | [DavidSamuell/agent-skills](https://github.com/DavidSamuell/agent-skills): spec, plan, build, test, review, ship, blind-spot pass, quiz | skills | skills | plugin (adds `/spec`, `/build`, `/quiz`, …) |
| `llm-wiki` | external | [llm-wiki-database](https://github.com/DavidSamuell/llm-wiki-database) cloned to `~/wiki`, configured by its `setup.sh` | not supported | setup script | setup script |

Local groups ship their skills in this repository. External groups point at another
Git repository and install it per harness (see [External groups](#external-groups)).
All skills are instructions, not automatic workflow or job launchers.

## Fresh machine

```bash
git clone git@github.com:DavidSamuell/agent-toolkit.git
cd agent-toolkit
python3 manage.py list
python3 manage.py install --all --agent codex --agent omp --agent claude --dry-run
python3 manage.py install --all --agent codex --agent omp --agent claude
python3 manage.py doctor
```

`--all` installs every group for every harness it supports, so llm-wiki is skipped for
Codex. External groups need `git`, plus `claude` for the Claude Code plugin and `bash`
plus the target CLI (`claude` / `omp`) for llm-wiki's setup script. GitHub access to
the private llm-wiki repository uses your normal Git credentials.

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

## External groups

An external group's `group.json` has an `external` block instead of a `skills` list:

```json
{
  "name": "agent-skills",
  "description": "...",
  "external": {
    "repository": "https://github.com/DavidSamuell/agent-skills.git",
    "ref": "main",
    "checkout": "managed",
    "sync": "ff-only",
    "agents": {
      "claude": {"method": "claude-plugin", "marketplace": "DavidSamuell/agent-skills",
                 "marketplace_name": "davidsamuell-agent-skills",
                 "plugin": "agent-skills@davidsamuell-agent-skills"},
      "codex": {"method": "skills", "path": "skills"},
      "omp": {"method": "skills", "path": "skills"}
    }
  }
}
```

- **checkout**: `managed` clones to `~/.local/share/agent-toolkit/sources/<group>`;
  `home:<path>` uses a fixed location such as `home:wiki` → `~/wiki`. An existing
  checkout is reused only if its `origin` matches `repository`.
- **sync**: `ff-only` fast-forwards the checkout on `update` and refuses a dirty tree;
  `clone-only` clones when missing and otherwise leaves the checkout to the tool
  (llm-wiki syncs `~/wiki` itself). `install` never pulls; updates stay deliberate.
- **methods**, one per harness. A harness missing from `agents` is unsupported, and
  installing it is refused:
  - `skills`: every `<path>/<skill>/SKILL.md` in the checkout goes through the same
    managed-copy, ownership, drift and rollback rules as local skills.
  - `claude-plugin`: adds the marketplace and installs the plugin at user scope;
    `update` refreshes both; `uninstall` removes both. Already-present items are skipped.
  - `script`: runs the repository's own setup command in the checkout on `install`
    and `update`. Scripts have no uninstaller, so `uninstall` refuses and prints the
    manual steps from `uninstall_note`; after doing them, `uninstall --skip-native`
    drops the toolkit binding.

Native steps (plugin commands and setup scripts) run before the binding is recorded,
so a failure leaves no binding behind, and every step is safe to rerun. They are not
part of the filesystem transaction, and they are the one place this toolkit changes
agent configuration and runs repository code: only list repositories you trust.
`status`/`doctor` also report missing checkouts and Claude plugins that are not installed.

For Claude Code, agent-skills installs the plugin from the fork's default branch.
Remove upstream `agent-skills@addy-agent-skills` first: both plugins are named
`agent-skills`, so their commands collide.

## Deliberate updates

Repository sources are copied, not linked into the checkout. Moving/removing the
checkout does not break installed skill files or compatibility links.

```bash
git pull --ff-only
python3 manage.py update --dry-run
python3 manage.py update
python3 manage.py doctor
```

`git pull` alone cannot change active installed copies. `update` does not fetch this
toolkit's own checkout. It fast-forwards `ff-only` external checkouts, reruns native
steps, and updates only recorded group/harness selections. New groups require an explicit
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
groups/agent-skills/group.json  External: agent-skills fork (skills + Claude plugin)
groups/llm-wiki/group.json      External: llm-wiki via its setup.sh
tests/test_installation.py       Isolated behavior and rollback tests
tests/test_external_groups.py    External checkouts, plugin and script methods (offline)
```

To add a skills group, create `groups/<group>/group.json`, put its skill directories
under `skills/`, and add the group name to `toolkit.json`. Skill names must be globally
unique. Each group currently accepts only `name`, `description`, and `skills`.
Skill directories may contain supporting files and executable helpers; installation
does not run those helpers.

To add an external group, create `groups/<group>/group.json` with an `external` block
(the directory holds nothing else) and add the name to `toolkit.json`.

Local groups still ship no commands or hooks. Future
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
