# agent-toolkit

Personal, grouped agent customizations and the single entry point for setting up
my agent tools on a fresh machine, for Codex, OMP and Claude Code.

| Group | What it does | Use it when |
| --- | --- | --- |
| `general` | `using-git-worktrees`: sets up an isolated workspace (native tools, or a git worktree fallback) before work starts. | You start a feature or carry out an implementation plan and don't want to disturb your current checkout. |
| `spartan-hpc` | `spartan-cpu-jobs`, `spartan-gpu-jobs`: write, submit, schedule and tune Slurm jobs and job arrays on Spartan, with a duplicate-work preflight and quota checks before submitting. | You run anything on Spartan, CPU or GPU. |
| `agent-skills` | Engineering-lifecycle skills: spec, plan, build, test, review, ship, plus critical-thinking checks (blind-spot pass, doubt-driven development) and an explain-and-quiz skill. Claude Code gets slash commands such as `/spec`, `/build`, `/quiz`. | You build software features and want the agent to follow a disciplined process (spec first, tests, review) instead of jumping straight to code. |
| `llm-wiki` | A personal LLM Wiki "second brain" in `~/wiki`, set up by its own `setup.sh` with a plugin, skills and session-sync hooks. | You want agent sessions and notes captured into one persistent knowledge base across harnesses. |
| `aae` | Agentic Algorithm Engineering: an autonomous optimisation loop on existing code. You agree the benchmark, metric, correctness checks and budget; the agent then tests one falsifiable hypothesis per experiment, keeps or discards it, and leaves one patch per accepted change plus a PDF report. Claude Code gets `/aae:aae`. | You have a measurable benchmark (running time, memory, cost, WER) and want an agent to improve an existing code base or pipeline step by step. Not for broad hyperparameter sweeps; use Optuna or similar for those. |
| `dspy` | 22 DSPy skills: designing signatures and modules, RAG and ReAct agents, evaluation, and prompt and few-shot optimizers (MIPROv2, GEPA, SIMBA, BootstrapFewShot, optimizer selection), through to fine-tuning and deployment. | Your LLM pipeline is, or can be, written in DSPy, and you want to optimise its prompts against a metric and a set of labelled examples instead of hand-tuning them. |

| Group | Kind | Source | Codex | OMP | Claude Code |
| --- | --- | --- | --- | --- | --- |
| `general` | local | this repository | skills | skills | skills |
| `spartan-hpc` | local | this repository | skills | skills | skills |
| `agent-skills` | external | [DavidSamuell/agent-skills](https://github.com/DavidSamuell/agent-skills) | skills | plugin | plugin |
| `llm-wiki` | external | [llm-wiki-database](https://github.com/DavidSamuell/llm-wiki-database), cloned to `~/wiki` | not supported | setup script | setup script |
| `aae` | external | [CHSZLab/AgenticAlgorithmEngineering](https://github.com/CHSZLab/AgenticAlgorithmEngineering) | skills | plugin | plugin |
| `dspy` | external | [OmidZamani/dspy-skills](https://github.com/OmidZamani/dspy-skills) | skills | plugin | plugin |

Local groups ship their skills in this repository. External groups point at another
Git repository and install it per harness (see [External groups](#external-groups)).
All skills are instructions, not automatic workflow or job launchers.

"plugin" means the harness's own plugin system installs it from the upstream
marketplace (`claude plugin …` or `omp plugin …`), so Claude Code and OMP get
everything the plugin ships: skills plus slash commands and subagents where it has
them (agent-skills' `/spec`, `/ship` and its reviewer personas). Codex has no
compatible plugin system and gets the skills only.

Group notes:

- **agent-skills**: in Claude Code and OMP, remove upstream `agent-skills@addy-agent-skills`
  first. Both plugins are named `agent-skills`, so their commands collide.
- **aae**: Codex gets the self-contained skill from upstream's
  `claude-plugin/skills/aae` (its root `skills/aae` is symlinks, which the installer
  rejects); Claude Code and OMP get the main `AAE.md` through the plugin. The PDF
  report needs Python 3 and LaTeX (`latexmk`); without them you still get the patches.
- **OMP and Claude plugins**: OMP can also load plugins installed by Claude Code if
  its `claude-plugins` source is turned on. Leave it off so OMP uses only its own
  installs and nothing loads twice.
- **dspy**: the skills only teach the agent DSPy; the project still needs
  `pip install dspy`. They target DSPy 3.2.x, so check APIs against newer releases.

## Install by asking an agent

Paste this into Claude Code or Oh My Pi (OMP) and replace the two placeholders.
The agent clones or updates the toolkit, shows you the plan, installs, and checks the result.

```text
Install these groups from my agent toolkit (https://github.com/DavidSamuell/agent-toolkit):
  Groups:    <GROUPS, e.g. aae, dspy — or "all">
  Harnesses: <HARNESSES, e.g. claude, codex, omp>

1. If ~/agent-toolkit does not exist, clone it. It is private, so use
   `gh repo clone DavidSamuell/agent-toolkit ~/agent-toolkit` (or SSH if gh is not
   signed in). If it exists, run `git -C ~/agent-toolkit pull --ff-only`.
2. In ~/agent-toolkit, run `python3 manage.py list`. Check that every group exists
   and supports every harness I asked for; tell me about any that do not (llm-wiki
   has no Codex support) and leave those out.
3. Run `python3 manage.py install --group <g> ... --agent <h> ... --dry-run`
   (or `--all` for all groups), show me the plan, and wait for my OK.
4. Run the same command without --dry-run, then `python3 manage.py doctor`, and report
   what was installed for each harness.

If the installer reports a conflict, stop and tell me. Do not edit the state file or
move, overwrite or delete existing skills to get past it. At the end, tell me which
harnesses I need to restart to pick up new skills or plugins.
```

Harness names: `claude` = Claude Code, `codex` = Codex, `omp` = Oh My Pi. Group names
are in the first table above. For example:

```text
Install these groups from my agent toolkit (https://github.com/DavidSamuell/agent-toolkit):
  Groups:    aae, dspy
  Harnesses: claude, omp
```

followed by the numbered steps. Already-installed groups are skipped, so the same prompt
also works to add a group to a machine that already has the toolkit.
After pulling a toolkit change that switches how a group installs (as when OMP moved
from skill links to plugins), run `python3 manage.py update` to apply it.

## Fresh machine

`bootstrap.sh` checks for `git` and Python 3.8+ (including the macOS case where
`python3` is only an Xcode install stub), clones the toolkit to `~/agent-toolkit` if
needed, shows the dry-run plan, asks before installing, then runs `doctor`. The repo
is private, so use an authenticated GitHub CLI:

```bash
gh api repos/DavidSamuell/agent-toolkit/contents/bootstrap.sh \
  -H "Accept: application/vnd.github.raw" | bash -s -- --agent codex --agent omp --agent claude
```

or clone first (SSH key set up) and run it from the checkout:

```bash
git clone git@github.com:DavidSamuell/agent-toolkit.git ~/agent-toolkit
~/agent-toolkit/bootstrap.sh --agent codex --agent omp --agent claude
```

Options: `--group NAME` (repeatable; default every group), `--dir`, `--repo`, `--ref`,
and `--yes` to skip the confirmation. With no terminal to confirm on and no `--yes`,
it stops after the dry run. An existing checkout is used as it is and never pulled.

The same steps by hand:

```bash
python3 manage.py list
python3 manage.py install --all --agent codex --agent omp --agent claude --dry-run
python3 manage.py install --all --agent codex --agent omp --agent claude
python3 manage.py doctor
```

`--all` installs every group for every harness it supports, so llm-wiki is skipped for
Codex. External groups need `git`, plus the harness CLI (`claude` / `omp`) for plugin
installs and `bash` plus the target CLI for llm-wiki's setup script. GitHub access to
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

Four groups (`agent-skills`, `llm-wiki`, `aae`, `dspy`) are external: their
`group.json` points at another Git repository, which the toolkit clones and installs
per harness, as skill copies, a Claude Code or OMP plugin, or the project's own setup script.
`update` brings in upstream changes when you run it (llm-wiki syncs `~/wiki` itself).
External groups run code from those repositories, so only add ones you trust.

The manifest format, install methods and how to add a new external group are in
[docs/external-groups.md](docs/external-groups.md).

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
bootstrap.sh                    Fresh-machine entry point (prerequisites, clone, confirm, install)
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
groups/aae/group.json           External: Agentic Algorithm Engineering (skill + Claude plugin)
groups/dspy/group.json          External: DSPy skills (skills + Claude plugin)
docs/external-groups.md         External group manifest reference and how to add one
tests/test_installation.py       Isolated behavior and rollback tests
tests/test_external_groups.py    External checkouts, plugin and script methods (offline)
tests/test_bootstrap.py          bootstrap.sh clone, confirmation and selection (offline)
```

To add a skills group, create `groups/<group>/group.json`, put its skill directories
under `skills/`, and add the group name to `toolkit.json`. Skill names must be globally
unique. Each group currently accepts only `name`, `description`, and `skills`.
Skill directories may contain supporting files and executable helpers; installation
does not run those helpers.

To add an external group, see [docs/external-groups.md](docs/external-groups.md#adding-an-external-group).

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
