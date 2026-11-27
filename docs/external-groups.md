# External groups

An external group installs something that lives in another Git repository: the toolkit
clones that repository and installs from the clone, per harness. Local groups, by
contrast, keep their skill files in this repository under `groups/<group>/skills/`.

## Manifest

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

## Adding an external group

1. Create `groups/<group>/group.json` with an `external` block. The directory holds
   nothing else.
2. Add the group name to `toolkit.json`.
3. Set `ref` to the branch you want to track; it is not always `main` (dspy uses `master`).
4. For the `skills` method, point `path` at a folder of `<skill>/SKILL.md` directories
   with no symlinks; the installer rejects source symlinks. Skill names must be unique
   across all groups.
5. For `claude-plugin`, take `marketplace_name` from the upstream
   `.claude-plugin/marketplace.json` (`name`), and set `plugin` to `<plugin>@<marketplace_name>`.
6. Check it in an isolated home before committing:

   ```bash
   python3 manage.py list
   python3 manage.py --home "$(mktemp -d)" install --group <group> --agent codex --agent omp
   python3 manage.py install --group <group> --agent claude --dry-run
   ```
