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

        self.skills_upstream = self._make_upstream("skills-upstream", {
            "skills/alpha-skill/SKILL.md": self._skill("alpha-skill"),
            "skills/beta-skill/SKILL.md": self._skill("beta-skill"),
            "skills/README.md": "not a skill\n",
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
