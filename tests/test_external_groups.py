import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Minimal stand-in for the `claude plugin` CLI: records calls, keeps state.
    import json, os, sys
    state_path = os.environ["FAKE_CLAUDE_STATE"]
    try:
        state = json.load(open(state_path))
    except FileNotFoundError:
        state = {"marketplaces": [], "plugins": [], "calls": []}
    args = sys.argv[1:]
    state["calls"].append(args)
    def save():
        json.dump(state, open(state_path, "w"))
    if args[:3] == ["plugin", "marketplace", "list"]:
        print(json.dumps([{"name": n} for n in state["marketplaces"]]))
    elif args[:2] == ["plugin", "list"]:
        print(json.dumps([{"id": p} for p in state["plugins"]]))
    elif args[:3] == ["plugin", "marketplace", "add"]:
        state["marketplaces"].append(os.environ["FAKE_MARKETPLACE_NAME"])
    elif args[:3] == ["plugin", "marketplace", "remove"]:
        state["marketplaces"].remove(args[3])
    elif args[:2] == ["plugin", "install"]:
        state["plugins"].append(args[2])
    elif args[:2] == ["plugin", "uninstall"]:
        state["plugins"].remove(args[2])
    save()
    """
)


FAKE_OMP = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Minimal stand-in for the `omp plugin` CLI, with its real output formats:
    # `marketplace list` is text only, `plugin list --json` is an object.
    import json, os, sys
    state_path = os.environ["FAKE_OMP_STATE"]
    try:
        state = json.load(open(state_path))
    except FileNotFoundError:
        state = {"marketplaces": [], "plugins": [], "calls": []}
    args = sys.argv[1:]
    state["calls"].append(args)
    def save():
        json.dump(state, open(state_path, "w"))
    def fail(message):
        save()
        print(message)
        sys.exit(1)
    if args[:3] == ["plugin", "marketplace", "list"]:
        if state["marketplaces"]:
            print("Configured Marketplaces:\\n")
            for name in state["marketplaces"]:
                print("  %s  owner/%s" % (name, name))
        else:
            print("No marketplaces configured\\n\\nAdd one with: omp plugin marketplace add <source>")
    elif args[:2] == ["plugin", "list"]:
        print(json.dumps({"npm": [], "marketplace": [{"id": p, "scope": "user"} for p in state["plugins"]]}))
    elif args[:3] == ["plugin", "marketplace", "add"]:
        name = os.environ["FAKE_MARKETPLACE_NAME"]
        if name in state["marketplaces"]:
            fail("already exists")
        state["marketplaces"].append(name)
    elif args[:3] == ["plugin", "marketplace", "remove"]:
        state["marketplaces"].remove(args[3])
    elif args[:2] == ["plugin", "install"]:
        if args[2] in state["plugins"]:
            fail("already installed")
        state["plugins"].append(args[2])
    elif args[:2] == ["plugin", "uninstall"]:
        if args[2] not in state["plugins"]:
            fail("not installed")
        state["plugins"].remove(args[2])
    save()
    """
)


class ExternalGroupTests(unittest.TestCase):
    """External groups: Git checkouts plus per-harness install methods.

    Upstream repositories are local Git repositories and the Claude CLI is a
    recording fake, so no test touches the network or real agent config.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-toolkit-external-")
        self.base = Path(self._tmp.name)
        self.repo = self.base / "toolkit"
        self.home = self.base / "home"
        self.bin = self.base / "bin"
        for path in (self.repo, self.home, self.bin):
            path.mkdir()
        shutil.copy2(PROJECT_ROOT / "manage.py", self.repo / "manage.py")
        self.claude_state = self.base / "claude-state.json"
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(0o755)
        self.omp_state = self.base / "omp-state.json"
        fake_omp = self.bin / "omp"
        fake_omp.write_text(FAKE_OMP, encoding="utf-8")
        fake_omp.chmod(0o755)

        self.skills_upstream = self._make_upstream("skills-upstream", {
            "skills/alpha-skill/SKILL.md": self._skill("alpha-skill"),
            "skills/beta-skill/SKILL.md": self._skill("beta-skill"),
            "skills/README.md": "not a skill\n",
        })
        self.plugin_upstream = self._make_upstream("plugin-upstream", {
            "skills/gamma-skill/SKILL.md": self._skill("gamma-skill"),
        })
        self.script_upstream = self._make_upstream("script-upstream", {
            "setup.sh": "#!/bin/sh\necho \"$@\" >> \"$PWD/setup-calls.log\"\n",
        })
        self._write_fixture()

    def tearDown(self):
        self._tmp.cleanup()

    # ---------- helpers ----------

    @staticmethod
    def _skill(name):
        return "---\nname: %s\ndescription: Use when testing %s.\n---\n\n# %s\n" % (name, name, name)

    def _git(self, *args, cwd=None):
        env = os.environ.copy()
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
        subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _make_upstream(self, name, files):
        path = self.base / name
        path.mkdir()
        self._git("init", "-q", "-b", "main", cwd=path)
        self._commit(path, files, "initial")
        return path

    def _commit(self, path, files, message):
        for relative, content in files.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._git("add", "-A", cwd=path)
        self._git("commit", "-q", "-m", message, cwd=path)

    def _write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def _write_fixture(self, extra_groups=None):
        groups = {
            "lifecycle": {
                "name": "lifecycle",
                "description": "External skills with a Claude plugin.",
                "external": {
                    "repository": str(self.skills_upstream),
                    "ref": "main",
                    "checkout": "managed",
                    "sync": "ff-only",
                    "agents": {
                        "claude": {"method": "claude-plugin", "marketplace": "owner/lifecycle",
                                   "marketplace_name": "lifecycle-market",
                                   "plugin": "lifecycle@lifecycle-market"},
                        "codex": {"method": "skills", "path": "skills"},
                        "omp": {"method": "skills", "path": "skills"},
                    },
                },
            },
            "ompplug": {
                "name": "ompplug",
                "description": "External skills installed as plugins for Claude and OMP.",
                "external": {
                    "repository": str(self.plugin_upstream),
                    "ref": "main",
                    "checkout": "managed",
                    "sync": "ff-only",
                    "agents": {
                        "claude": {"method": "claude-plugin", "marketplace": "owner/lifecycle",
                                   "marketplace_name": "lifecycle-market",
                                   "plugin": "lifecycle@lifecycle-market"},
                        "codex": {"method": "skills", "path": "skills"},
                        "omp": {"method": "omp-plugin", "marketplace": "owner/lifecycle",
                                "marketplace_name": "lifecycle-market",
                                "plugin": "lifecycle@lifecycle-market"},
                    },
                },
            },
            "wiki": {
                "name": "wiki",
                "description": "External tool configured by its own setup script.",
                "external": {
                    "repository": str(self.script_upstream),
                    "ref": "main",
                    "checkout": "home:wiki",
                    "sync": "clone-only",
                    "agents": {
                        "omp": {"method": "script", "command": ["sh", "setup.sh", "--omp"],
                                "uninstall_note": "Delete the OMP wiki hook."},
                    },
                },
            },
        }
        groups.update(extra_groups or {})
        if self.repo.joinpath("groups").exists():
            shutil.rmtree(self.repo / "groups")
        for name, manifest in groups.items():
            self._write_json(self.repo / "groups" / name / "group.json", manifest)
        self._write_json(self.repo / "toolkit.json", {
            "schema_version": 1, "name": "agent-toolkit", "groups": sorted(groups),
        })

    def _run(self, *args):
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["AGENT_TOOLKIT_CLAUDE"] = str(self.bin / "claude")
        env["FAKE_CLAUDE_STATE"] = str(self.claude_state)
        env["AGENT_TOOLKIT_OMP"] = str(self.bin / "omp")
        env["FAKE_OMP_STATE"] = str(self.omp_state)
        env["FAKE_MARKETPLACE_NAME"] = "lifecycle-market"
        return subprocess.run(
            [sys.executable, str(self.repo / "manage.py"), "--home", str(self.home)] + [str(a) for a in args],
            cwd=self.repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def _ok(self, *args):
        result = self._run(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _claude(self):
        return json.loads(self.claude_state.read_text()) if self.claude_state.exists() else {"calls": []}

    @property
    def checkout(self):
        return self.home / ".local" / "share" / "agent-toolkit" / "sources" / "lifecycle"

    # ---------- skills method ----------

    def test_skills_method_clones_installs_updates_and_uninstalls(self):
        self._ok("install", "--group", "lifecycle", "--agent", "codex", "--agent", "omp")
        self.assertTrue((self.checkout / ".git").is_dir())
        canonical = self.home / ".agents" / "skills"
        self.assertEqual(sorted(p.name for p in canonical.iterdir()), ["alpha-skill", "beta-skill"])
        self.assertTrue((self.home / ".omp" / "agent" / "skills" / "alpha-skill").is_symlink())
        self.assertFalse((self.home / ".claude" / "skills" / "alpha-skill").exists())
        self._ok("status")

        self._commit(self.skills_upstream, {"skills/gamma-skill/SKILL.md": self._skill("gamma-skill")}, "add gamma")
        self._ok("update")
        self.assertTrue((canonical / "gamma-skill" / "SKILL.md").is_file())
        self.assertTrue((self.home / ".omp" / "agent" / "skills" / "gamma-skill").is_symlink())

        self._ok("uninstall", "--group", "lifecycle")
        self.assertFalse(any(canonical.iterdir()))
        self.assertTrue(self.checkout.is_dir(), "checkouts are sources and are kept")

    def test_dry_run_creates_nothing(self):
        result = self._ok("install", "--group", "lifecycle", "--agent", "codex", "--dry-run")
        self.assertIn("would run: git clone", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_update_refuses_dirty_checkout(self):
        self._ok("install", "--group", "lifecycle", "--agent", "codex")
        (self.checkout / "skills" / "alpha-skill" / "SKILL.md").write_text("edited\n", encoding="utf-8")
        result = self._run("update")
        self.assertEqual(result.returncode, 2)
        self.assertIn("local changes", result.stderr)

    def test_checkout_tracking_another_repository_is_rejected(self):
        other = self._make_upstream("other-upstream", {"README": "x\n"})
        self.checkout.parent.mkdir(parents=True)
        self._git("clone", "-q", str(other), str(self.checkout))
        result = self._run("install", "--group", "lifecycle", "--agent", "codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected", result.stderr)
        self.assertFalse((self.home / ".agents").exists())

    # ---------- claude-plugin method ----------

    def test_claude_plugin_install_is_idempotent_and_uninstalls(self):
        self._ok("install", "--group", "lifecycle", "--agent", "claude")
        state = self._claude()
        self.assertEqual(state["plugins"], ["lifecycle@lifecycle-market"])
        self.assertIn(["plugin", "marketplace", "add", "owner/lifecycle", "--scope", "user"], state["calls"])
        self.assertFalse((self.home / ".claude" / "skills" / "alpha-skill").exists(),
                         "claude gets the plugin, not skill copies")
        self._ok("status")

        self._ok("install", "--group", "lifecycle", "--agent", "claude")
        installs = [c for c in self._claude()["calls"] if c[:2] == ["plugin", "install"]]
        self.assertEqual(len(installs), 1)

        self._ok("uninstall", "--group", "lifecycle", "--agent", "claude")
        state = self._claude()
        self.assertEqual(state["plugins"], [])
        self.assertEqual(state["marketplaces"], [])

    def test_missing_cli_stops_before_any_clone(self):
        (self.bin / "claude").unlink()
        result = self._run("install", "--group", "lifecycle", "--agent", "claude", "--agent", "codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found on PATH", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_status_reports_missing_plugin(self):
        self._ok("install", "--group", "lifecycle", "--agent", "claude")
        self.claude_state.write_text(json.dumps({"marketplaces": [], "plugins": [], "calls": []}))
        result = self._run("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("claude plugin not installed", result.stdout)

    # ---------- omp-plugin method ----------

    def _omp(self):
        return json.loads(self.omp_state.read_text()) if self.omp_state.exists() else {"calls": []}

    def _write_lifecycle(self, omp_spec):
        # Rewrite only the lifecycle group's omp entry, keeping the other fixtures.
        path = self.repo / "groups" / "lifecycle" / "group.json"
        manifest = json.loads(path.read_text())
        manifest["external"]["agents"]["omp"] = omp_spec
        self._write_json(path, manifest)

    def test_omp_plugin_install_is_idempotent_and_uninstalls(self):
        self._ok("install", "--group", "ompplug", "--agent", "omp")
        state = self._omp()
        self.assertEqual(state["plugins"], ["lifecycle@lifecycle-market"])
        self.assertIn(["plugin", "marketplace", "add", "owner/lifecycle"], state["calls"])
        self.assertIn(["plugin", "install", "lifecycle@lifecycle-market", "--scope", "user"], state["calls"])
        self.assertFalse((self.home / ".omp" / "agent" / "skills" / "gamma-skill").exists(),
                         "omp gets the plugin, not skill links")
        self.assertFalse((self.home / ".agents" / "skills" / "gamma-skill").exists(),
                         "no canonical copy without a skills-method binding")
        self._ok("status")

        self._ok("install", "--group", "ompplug", "--agent", "omp")
        installs = [c for c in self._omp()["calls"] if c[:2] == ["plugin", "install"]]
        self.assertEqual(len(installs), 1, "an installed plugin is not reinstalled")

        self._ok("uninstall", "--group", "ompplug", "--agent", "omp")
        state = self._omp()
        self.assertEqual(state["plugins"], [])
        self.assertEqual(state["marketplaces"], [])

    def test_omp_plugin_update_refreshes_marketplace_and_upgrades(self):
        self._ok("install", "--group", "ompplug", "--agent", "omp")
        self._ok("update")
        calls = self._omp()["calls"]
        self.assertIn(["plugin", "marketplace", "update", "lifecycle-market"], calls)
        self.assertIn(["plugin", "upgrade", "lifecycle@lifecycle-market", "--scope", "user"], calls)

    def test_omp_and_claude_plugins_install_side_by_side(self):
        self._ok("install", "--group", "ompplug", "--agent", "omp", "--agent", "claude", "--agent", "codex")
        self.assertEqual(self._omp()["plugins"], ["lifecycle@lifecycle-market"])
        self.assertEqual(self._claude()["plugins"], ["lifecycle@lifecycle-market"])
        self.assertTrue((self.home / ".agents" / "skills" / "gamma-skill" / "SKILL.md").is_file())
        self.assertFalse((self.home / ".omp" / "agent" / "skills" / "gamma-skill").exists())
        self._ok("doctor")

    def test_status_reports_missing_omp_plugin(self):
        self._ok("install", "--group", "ompplug", "--agent", "omp")
        self.omp_state.write_text(json.dumps({"marketplaces": [], "plugins": [], "calls": []}))
        result = self._run("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("omp plugin not installed", result.stdout)

    def test_missing_omp_cli_stops_before_any_clone(self):
        (self.bin / "omp").unlink()
        result = self._run("install", "--group", "ompplug", "--agent", "omp")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not found on PATH", result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_switching_omp_from_skills_to_plugin_migrates_on_update(self):
        self._ok("install", "--group", "lifecycle", "--agent", "omp", "--agent", "codex")
        link = self.home / ".omp" / "agent" / "skills" / "alpha-skill"
        self.assertTrue(link.is_symlink())

        self._write_lifecycle({"method": "omp-plugin", "marketplace": "owner/lifecycle",
                               "marketplace_name": "lifecycle-market",
                               "plugin": "lifecycle@lifecycle-market"})
        result = self._run("status")
        self.assertEqual(result.returncode, 1, "the old skill link is stale until update")
        self.assertIn("stale compatibility link", result.stdout)

        self._ok("update")
        self.assertFalse(os.path.lexists(link), "update removes the old OMP skill link")
        self.assertTrue((self.home / ".agents" / "skills" / "alpha-skill" / "SKILL.md").is_file(),
                        "codex keeps its copy")
        self.assertEqual(self._omp()["plugins"], ["lifecycle@lifecycle-market"])
        self._ok("doctor")

    # ---------- script method ----------

    def test_script_method_runs_in_checkout_and_uninstall_needs_skip_native(self):
        self._ok("install", "--group", "wiki", "--agent", "omp")
        log = self.home / "wiki" / "setup-calls.log"
        self.assertEqual(log.read_text().split(), ["--omp"])

        result = self._run("uninstall", "--group", "wiki")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Delete the OMP wiki hook.", result.stderr)

        self._ok("uninstall", "--group", "wiki", "--skip-native")
        self.assertIn("No installed selections", self._ok("status").stdout)
        self.assertTrue((self.home / "wiki").is_dir())

    # ---------- selection and manifest validation ----------

    def test_unsupported_agent_is_rejected_but_all_skips_it(self):
        result = self._run("install", "--group", "wiki", "--agent", "codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not support codex", result.stderr)
        self._ok("install", "--all", "--agent", "codex")
        self.assertIn("lifecycle/codex", self._ok("status").stdout)
        self.assertNotIn("wiki/codex", self._ok("status").stdout)

    def test_invalid_external_manifests_are_rejected(self):
        bad_specs = [
            {"codex": {"method": "claude-plugin", "marketplace": "a/b", "marketplace_name": "m", "plugin": "p@m"}},
            {"claude": {"method": "omp-plugin", "marketplace": "a/b", "marketplace_name": "m", "plugin": "p@m"}},
            {"omp": {"method": "omp-plugin", "marketplace": "a/b", "marketplace_name": "m", "plugin": "p@other"}},
            {"codex": {"method": "teleport"}},
            {"codex": {"method": "skills", "path": "../outside"}},
        ]
        for agents in bad_specs:
            self._write_fixture({"broken": {
                "name": "broken", "description": "Broken.",
                "external": {"repository": "x", "ref": "main", "checkout": "managed",
                             "sync": "ff-only", "agents": agents},
            }})
            result = self._run("list")
            self.assertEqual(result.returncode, 2, agents)
        self._write_fixture({"broken": {
            "name": "broken", "description": "Broken.",
            "external": {"repository": "x", "ref": "main", "checkout": "home:../escape",
                         "sync": "ff-only", "agents": {"codex": {"method": "skills", "path": "skills"}}},
        }})
        self.assertEqual(self._run("list").returncode, 2)


if __name__ == "__main__":
    unittest.main()
