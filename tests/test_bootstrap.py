import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    """bootstrap.sh against a local toolkit repository and an isolated HOME.

    Runs in a new session so /dev/tty is unavailable, exactly like a piped
    `gh api ... | bash` run with no terminal.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-toolkit-bootstrap-")
        base = Path(self._tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.upstream = base / "toolkit-upstream"
        self.upstream.mkdir()
        shutil.copy2(PROJECT_ROOT / "manage.py", self.upstream / "manage.py")
        shutil.copy2(PROJECT_ROOT / "bootstrap.sh", self.upstream / "bootstrap.sh")
        for group in ("general", "spartan-hpc"):
            shutil.copytree(PROJECT_ROOT / "groups" / group, self.upstream / "groups" / group)
        (self.upstream / "toolkit.json").write_text(json.dumps(
            {"schema_version": 1, "name": "agent-toolkit", "groups": ["general", "spartan-hpc"]}))
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git"] + args, cwd=self.upstream, env=env, check=True)
        # A copy outside any checkout, as when the script is piped from GitHub.
        self.standalone = base / "bootstrap.sh"
        shutil.copy2(PROJECT_ROOT / "bootstrap.sh", self.standalone)
        self.target = self.home / "agent-toolkit"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args, script=None):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("AGENT_TOOLKIT_DIR", None)
        env.pop("AGENT_TOOLKIT_REPO", None)
        return subprocess.run(
            ["bash", str(script or self.standalone)] + list(args),
            env=env, text=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )

    def test_clones_installs_selected_group_and_runs_doctor(self):
        result = self._run("--repo", str(self.upstream), "--dir", str(self.target),
                           "--agent", "codex", "--group", "general", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.target / "manage.py").is_file())
        skills = self.home / ".agents" / "skills"
        self.assertEqual(sorted(p.name for p in skills.iterdir()), ["using-git-worktrees"])
        self.assertIn("Doctor: no manifest", result.stdout)

    def test_without_terminal_or_yes_stops_after_dry_run(self):
        result = self._run("--repo", str(self.upstream), "--dir", str(self.target), "--agent", "omp")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rerun with --yes", result.stdout)
        self.assertIn("Would install", result.stdout)
        self.assertFalse((self.home / ".agents").exists())

    def test_runs_from_its_own_checkout_without_cloning(self):
        result = self._run("--agent", "codex", "--yes", script=self.upstream / "bootstrap.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("using existing checkout: %s" % self.upstream.resolve(), result.stdout)
        self.assertFalse(self.target.exists())
        self.assertTrue((self.home / ".agents" / "skills" / "spartan-cpu-jobs").is_dir())

    def test_requires_an_agent_and_rejects_unknown_ones(self):
        self.assertIn("at least one --agent", self._run("--yes").stderr)
        self.assertIn("unknown agent", self._run("--agent", "cursor").stderr)

    def test_refuses_a_directory_that_is_not_a_toolkit_checkout(self):
        self.target.mkdir()
        (self.target / "notes.txt").write_text("mine\n")
        result = self._run("--repo", str(self.upstream), "--dir", str(self.target), "--agent", "codex", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an agent-toolkit checkout", result.stderr)
        self.assertEqual([p.name for p in self.target.iterdir()], ["notes.txt"])


if __name__ == "__main__":
    unittest.main()
