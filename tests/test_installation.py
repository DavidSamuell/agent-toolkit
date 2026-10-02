import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    """Consumer-facing tests for the stdlib installer CLI.

    Every test uses a synthetic checkout and an explicit home.  The checkout's
    manage.py is copied at setup time so source edits and home contents remain
    independent of the real repository and user configuration.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="agent-toolkit-tests-")
        base = Path(self._tmp.name)
        self.repo = base / "repository"
        self.home = base / "home"
        self.repo.mkdir()
        self.home.mkdir()
        shutil.copy2(PROJECT_ROOT / "manage.py", self.repo / "manage.py")
        self._write_fixture()

    def tearDown(self):
        self._tmp.cleanup()

    # ---------- fixture and process helpers ----------

    def _write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _write_fixture(self):
        self._write_json(
            self.repo / "toolkit.json",
            {
                "schema_version": 1,
                "name": "agent-toolkit",
                "groups": ["alpha", "beta"],
            },
        )
        self._write_group(
            "alpha",
            "Alpha jobs for the Spartan cluster.",
            "alpha-skill",
            "Submit and monitor Alpha jobs on Spartan HPC.",
        )
        self._write_group(
            "beta",
            "Beta jobs for the Spartan cluster.",
            "beta-skill",
            "Submit and monitor Beta jobs on Spartan HPC.",
        )

    def _write_group(self, group, description, skill, skill_description):
        group_dir = self.repo / "groups" / group
        skill_dir = group_dir / "skills" / skill
        self._write_json(
            group_dir / "group.json",
            {"name": group, "description": description, "skills": [skill]},
        )
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_dir.joinpath("SKILL.md").write_text(
            "---\n"
            f"name: {skill}\n"
            f"description: {skill_description}\n"
            "---\n\n"
            f"# {skill.replace('-', ' ').title()}\n\n"
            "Use the supplied helper to submit a reproducible batch job.\n",
            encoding="utf-8",
        )
        references = skill_dir / "references"
        references.mkdir()
        (references / "defaults.conf").write_text(
            "partition=general\nwalltime=00:30:00\n", encoding="utf-8"
        )
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        submit = scripts / "submit.sh"
        submit.write_text("#!/bin/sh\nexec sbatch \"$@\"\n", encoding="utf-8")
        submit.chmod(0o755)

    def _run(self, *args):
        env = os.environ.copy()
        # Explicit --home must isolate state even if a user's XDG variables are
        # set.  These values also prevent accidental writes to real user paths.
        env["HOME"] = str(self.home)
        env["XDG_CONFIG_HOME"] = str(Path(self._tmp.name) / "xdg-config")
        env["XDG_CACHE_HOME"] = str(Path(self._tmp.name) / "xdg-cache")
        env["XDG_STATE_HOME"] = str(Path(self._tmp.name) / "xdg-state")
        command = [sys.executable, str(self.repo / "manage.py")]
        command.extend(str(arg) for arg in args)
        return subprocess.run(
            command,
            cwd=self.repo,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def _ok(self, result):
        self.assertEqual(
            result.returncode,
            0,
            msg=f"command failed ({result.returncode}):\n{result.stdout}\n{result.stderr}",
        )

    def _state_path(self, home=None):
        selected_home = Path(home) if home is not None else self.home
        return selected_home / ".local" / "state" / "agent-toolkit" / "installations.json"

    def _skill_path(self, skill, home=None):
        selected_home = Path(home) if home is not None else self.home
        return selected_home / ".agents" / "skills" / skill

    def _source_skill_path(self, skill):
        for group in ("alpha", "beta"):
            candidate = self.repo / "groups" / group / "skills" / skill
            if candidate.exists() or candidate.is_symlink():
                return candidate
        self.fail(f"unknown fixture skill: {skill}")

    def _snapshot(self, root):
        """Capture bytes, modes, and links without following symlinks."""
        root = Path(root)
        snapshot = {}
        if not root.exists() and not root.is_symlink():
            return snapshot
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            entries = [current_path / name for name in directories + files]
            for path in entries:
                relative = path.relative_to(root).as_posix()
                info = path.lstat()
                mode = stat.S_IMODE(info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    snapshot[relative] = ("link", os.readlink(path))
                elif stat.S_ISDIR(info.st_mode):
                    snapshot[relative] = ("directory", mode)
                elif stat.S_ISREG(info.st_mode):
                    snapshot[relative] = ("file", mode, path.read_bytes())
                else:
                    snapshot[relative] = ("other", mode, info.st_size)
        return snapshot

    def _snapshot_without_mutation_control(self, root):
        snapshot = self._snapshot(root)
        for relative in list(snapshot):
            if relative == ".local" or relative.startswith(".local/"):
                del snapshot[relative]
        return snapshot

    def _link_resolves_to(self, link, target):
        return link.is_symlink() and os.path.realpath(link) == os.path.realpath(target)

    def _install(self, *groups, agents=("codex",), home=None, all_groups=False):
        args = ["install"]
        if all_groups:
            args.append("--all")
        else:
            for group in groups:
                args.extend(("--group", group))
        for agent in agents:
            args.extend(("--agent", agent))
        if home is not None:
            args.extend(("--home", str(home)))
        return self._run(*args)

    # ---------- selection and copy behavior ----------

    def test_list_reports_group_descriptions_and_skill_contents(self):
        result = self._run("--home", self.home, "list")
        self._ok(result)
        listing = result.stdout + result.stderr
        for expected in (
            "Alpha jobs for the Spartan cluster.",
            "alpha-skill",
            "Beta jobs for the Spartan cluster.",
            "beta-skill",
        ):
            self.assertIn(expected, listing)

    def test_explicit_multiple_groups_are_idempotent(self):
        first = self._install("alpha", "beta", agents=("codex",))
        self._ok(first)
        self.assertTrue(self._skill_path("alpha-skill").is_dir())
        self.assertTrue(self._skill_path("beta-skill").is_dir())
        before = self._snapshot(self.home)
        second = self._install("alpha", "beta", agents=("codex",))
        self._ok(second)
        self.assertEqual(before, self._snapshot(self.home))

    def test_all_groups_and_explicit_agent_requirement(self):
        before = self._snapshot(self.home)
        missing_agent = self._run(
            "install", "--home", self.home, "--group", "alpha"
        )
        self.assertNotEqual(missing_agent.returncode, 0)
        self.assertEqual(before, self._snapshot(self.home))

        installed = self._install(all_groups=True, agents=("omp",))
        self._ok(installed)
        self.assertTrue(self._skill_path("alpha-skill").is_dir())
        self.assertTrue(self._skill_path("beta-skill").is_dir())

    def test_dry_run_install_update_and_uninstall_do_not_mutate_filesystem(self):
        initial = self._snapshot(self.home)
        dry_install = self._run(
            "install",
            "--group",
            "alpha",
            "--agent",
            "codex",
            "--dry-run",
            "--home",
            self.home,
        )
        self._ok(dry_install)
        self.assertEqual(initial, self._snapshot(self.home))

        self._ok(self._install("alpha"))
        source_skill = self._source_skill_path("alpha-skill")
        source_skill.joinpath("SKILL.md").write_text(
            source_skill.joinpath("SKILL.md").read_text(encoding="utf-8")
            + "\nSource update.\n",
            encoding="utf-8",
        )
        before_dry_update = self._snapshot(self.home)
        dry_update = self._run("update", "--home", self.home, "--dry-run")
        self._ok(dry_update)
        self.assertEqual(before_dry_update, self._snapshot(self.home))

        before_dry_uninstall = self._snapshot(self.home)
        dry_uninstall = self._run(
            "uninstall", "--group", "alpha", "--home", self.home, "--dry-run"
        )
        self._ok(dry_uninstall)
        self.assertEqual(before_dry_uninstall, self._snapshot(self.home))

    def test_source_edits_wait_for_explicit_update_and_unselected_groups_stay_absent(self):
        self._ok(self._install("alpha"))
        canonical = self._skill_path("alpha-skill")
        old_canonical = canonical.joinpath("SKILL.md").read_bytes()
        alpha_source = self._source_skill_path("alpha-skill").joinpath("SKILL.md")
        alpha_source.write_text(
            alpha_source.read_text(encoding="utf-8") + "\nUpdated source content.\n",
            encoding="utf-8",
        )
        beta_source = self._source_skill_path("beta-skill").joinpath("SKILL.md")
        beta_source.write_text(
            beta_source.read_text(encoding="utf-8") + "\nUnselected update.\n",
            encoding="utf-8",
        )
        self._ok(self._install("alpha"))
        self.assertEqual(old_canonical, canonical.joinpath("SKILL.md").read_bytes())
        self._ok(self._run("update", "--home", self.home))
        self.assertEqual(alpha_source.read_bytes(), canonical.joinpath("SKILL.md").read_bytes())
        self.assertFalse(self._skill_path("beta-skill").exists())

    def test_harness_links_point_to_canonical_copy_for_compatibility_agents(self):
        self._ok(self._install("alpha", agents=("codex", "omp", "claude")))
        canonical = self._skill_path("alpha-skill")
        self.assertTrue(canonical.is_dir())
        self.assertFalse(canonical.is_symlink())
        omp_link = self.home / ".omp" / "agent" / "skills" / "alpha-skill"
        claude_link = self.home / ".claude" / "skills" / "alpha-skill"
        self.assertTrue(self._link_resolves_to(omp_link, canonical))
        self.assertTrue(self._link_resolves_to(claude_link, canonical))

    # ---------- transaction and safety behavior ----------

    def test_conflict_preflight_prevents_partial_install(self):
        conflict = self._skill_path("beta-skill")
        conflict.parent.mkdir(parents=True)
        conflict.write_text("unrelated user file\n", encoding="utf-8")
        before = self._snapshot(self.home)
        result = self._install("alpha", "beta")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self._snapshot(self.home))
        self.assertFalse(self._skill_path("alpha-skill").exists())
        self.assertFalse(self._state_path().exists())

    def test_exact_untracked_tree_is_adopted_and_existing_harness_dir_becomes_link(self):
        source = self._source_skill_path("alpha-skill")
        canonical = self._skill_path("alpha-skill")
        canonical.parent.mkdir(parents=True)
        shutil.copytree(source, canonical)
        existing_omp = self.home / ".omp" / "agent" / "skills" / "alpha-skill"
        existing_omp.parent.mkdir(parents=True)
        shutil.copytree(source, existing_omp)
        existing_claude = self.home / ".claude" / "skills" / "alpha-skill"
        existing_claude.parent.mkdir(parents=True)
        existing_claude.symlink_to(canonical, target_is_directory=True)
        self._ok(self._install("alpha", agents=("omp", "claude")))
        self.assertTrue(canonical.is_dir())
        self.assertFalse(canonical.is_symlink())
        self.assertEqual(self._snapshot(source), self._snapshot(canonical))
        self.assertTrue(self._link_resolves_to(existing_omp, canonical))
        self.assertTrue(self._link_resolves_to(existing_claude, canonical))

    def test_modified_managed_file_blocks_update_and_uninstall(self):
        self._ok(self._install("alpha"))
        canonical_file = self._skill_path("alpha-skill") / "SKILL.md"
        canonical_file.write_text(
            canonical_file.read_text(encoding="utf-8") + "\nLocal edit.\n",
            encoding="utf-8",
        )
        modified = canonical_file.read_bytes()
        update = self._run("update", "--home", self.home)
        self.assertNotEqual(update.returncode, 0)
        self.assertEqual(modified, canonical_file.read_bytes())
        uninstall = self._run(
            "uninstall", "--group", "alpha", "--home", self.home
        )
        self.assertNotEqual(uninstall.returncode, 0)
        self.assertEqual(modified, canonical_file.read_bytes())
        self.assertTrue(self._state_path().exists())

    def test_uninstall_preserves_shared_bindings_and_omission_removes_all_group_agents(self):
        self._ok(self._install("alpha", agents=("codex", "omp")))
        self._ok(self._install("beta", agents=("codex",)))
        alpha = self._skill_path("alpha-skill")
        beta = self._skill_path("beta-skill")
        omp_link = self.home / ".omp" / "agent" / "skills" / "alpha-skill"

        remove_one = self._run(
            "uninstall",
            "--group",
            "alpha",
            "--agent",
            "codex",
            "--home",
            self.home,
        )
        self._ok(remove_one)
        self.assertTrue(alpha.is_dir())
        self.assertTrue(self._link_resolves_to(omp_link, alpha))
        self.assertTrue(beta.is_dir())

        remove_remaining = self._run(
            "uninstall", "--group", "alpha", "--home", self.home
        )
        self._ok(remove_remaining)
        self.assertFalse(alpha.exists())
        self.assertTrue(beta.is_dir())
        self.assertFalse(omp_link.exists() or omp_link.is_symlink())

    def test_uninstall_all_can_limit_agents_without_removing_other_bindings(self):
        self._ok(self._install("alpha", "beta", agents=("codex", "omp")))
        alpha = self._skill_path("alpha-skill")
        beta = self._skill_path("beta-skill")
        remove_codex = self._run(
            "uninstall",
            "--all",
            "--agent",
            "codex",
            "--home",
            self.home,
        )
        self._ok(remove_codex)
        self.assertTrue(alpha.is_dir())
        self.assertTrue(beta.is_dir())
        self._ok(self._run("uninstall", "--all", "--home", self.home))
        self.assertFalse(alpha.exists())
        self.assertFalse(beta.exists())

    def test_unsupported_manifest_assets_are_rejected_without_mutation(self):
        group_manifest = self.repo / "groups" / "alpha" / "group.json"
        data = json.loads(group_manifest.read_text(encoding="utf-8"))
        data["commands"] = ["submit"]
        self._write_json(group_manifest, data)
        before = self._snapshot(self.home)
        result = self._install("alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self._snapshot(self.home))
        self.assertFalse(self._state_path().exists())

    def test_unsafe_manifest_traversal_is_rejected_without_mutation(self):
        group_manifest = self.repo / "groups" / "alpha" / "group.json"
        data = json.loads(group_manifest.read_text(encoding="utf-8"))
        data["skills"] = ["../outside-skill"]
        self._write_json(group_manifest, data)
        before = self._snapshot(self.home)
        result = self._install("alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self._snapshot(self.home))

    def test_source_symlinks_are_rejected_without_mutation(self):
        source_skill = self._source_skill_path("alpha-skill")
        outside = Path(self._tmp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        os.symlink(outside, source_skill / "references" / "outside.txt")
        before = self._snapshot(self.home)
        result = self._install("alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, self._snapshot(self.home))

    def test_symlinked_home_parent_outside_home_is_rejected(self):
        outside = Path(self._tmp.name) / "outside-home"
        outside.mkdir()
        agents_parent = self.home / ".agents"
        agents_parent.symlink_to(outside, target_is_directory=True)
        before_home = self._snapshot(self.home)
        before_outside = self._snapshot(outside)
        result = self._install("alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before_home, self._snapshot(self.home))
        self.assertEqual(before_outside, self._snapshot(outside))
        self.assertFalse(self._state_path().exists())

    # ---------- diagnostics ----------

    def test_status_and_doctor_detect_asset_drift_and_broken_links(self):
        self._ok(self._install("alpha", agents=("omp",)))
        canonical_file = self._skill_path("alpha-skill") / "SKILL.md"
        canonical_file.write_text("locally changed\n", encoding="utf-8")
        status = self._run("status", "--home", self.home)
        self.assertNotEqual(status.returncode, 0)
        doctor = self._run("doctor", "--home", self.home)
        self.assertNotEqual(doctor.returncode, 0)

        canonical_file.unlink()
        broken_doctor = self._run("doctor", "--home", self.home)
        self.assertNotEqual(broken_doctor.returncode, 0)

    def test_doctor_detects_missing_sources_and_invalid_manifests(self):
        self._ok(self._install("alpha"))
        shutil.rmtree(self.repo / "groups" / "alpha")
        missing_source = self._run("doctor", "--home", self.home)
        self.assertNotEqual(missing_source.returncode, 0)

        second_home = Path(self._tmp.name) / "second-home"
        second_home.mkdir()
        # Restore the source group for an independent invalid-manifest check.
        self._write_group(
            "alpha",
            "Alpha jobs for the Spartan cluster.",
            "alpha-skill",
            "Submit and monitor Alpha jobs on Spartan HPC.",
        )
        self._ok(self._install("alpha", home=second_home))
        toolkit_manifest = self.repo / "toolkit.json"
        manifest = json.loads(toolkit_manifest.read_text(encoding="utf-8"))
        manifest["hooks"] = {"after_install": "echo unsafe"}
        self._write_json(toolkit_manifest, manifest)
        invalid = self._run("doctor", "--home", second_home)
        self.assertNotEqual(invalid.returncode, 0)

    # ---------- rollback ----------

    def test_injected_update_commit_failure_restores_existing_tree_and_state(self):
        """A failed replacement restores the prior managed tree and state bytes."""
        self._ok(self._install("alpha", agents=("omp",)))
        source_file = self._source_skill_path("alpha-skill") / "references" / "defaults.conf"
        source_file.write_text(
            source_file.read_text(encoding="utf-8") + "queue=debug\n",
            encoding="utf-8",
        )

        before = self._snapshot_without_mutation_control(self.home)
        state_before = self._state_path().read_bytes()
        spec = importlib.util.spec_from_file_location(
            "agent_toolkit_manage", self.repo / "manage.py"
        )
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)

        original_replace = module.os.replace
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            calls["count"] += 1
            # Update backs up the existing canonical tree first, then attempts
            # to install the replacement.  The latter failure must restore the
            # backup and leave the old state record untouched.
            if calls["count"] == 2:
                raise OSError("controlled commit failure")
            return original_replace(*args, **kwargs)

        with mock.patch.object(module.os, "replace", side_effect=fail_once):
            exit_code = None
            try:
                exit_code = module.main(
                    ["update", "--home", str(self.home)]
                )
            except SystemExit as raised:
                exit_code = raised.code
        self.assertGreaterEqual(calls["count"], 2)
        self.assertIsNotNone(exit_code)
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(before, self._snapshot_without_mutation_control(self.home))
        self.assertEqual(state_before, self._state_path().read_bytes())


if __name__ == "__main__":
    unittest.main()
