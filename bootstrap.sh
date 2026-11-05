#!/usr/bin/env bash
# Fresh-machine entry point for agent-toolkit.
#
# Checks prerequisites, clones the toolkit if needed, shows the install plan
# (dry run), asks for confirmation, installs, then runs doctor.
#
# The repository is private, so either run it from a checkout:
#   git clone git@github.com:DavidSamuell/agent-toolkit.git ~/agent-toolkit
#   ~/agent-toolkit/bootstrap.sh --agent claude --agent omp --agent codex
# or fetch it with an authenticated GitHub CLI:
#   gh api repos/DavidSamuell/agent-toolkit/contents/bootstrap.sh \
#     -H "Accept: application/vnd.github.raw" | bash -s -- --agent claude --agent omp
set -euo pipefail

DEFAULT_REPO="git@github.com:DavidSamuell/agent-toolkit.git"
REPO="${AGENT_TOOLKIT_REPO:-$DEFAULT_REPO}"
DIR="${AGENT_TOOLKIT_DIR:-$HOME/agent-toolkit}"
REF="main"
ASSUME_YES=false
AGENTS=()
SELECTED_GROUPS=()

usage() {
  cat <<'EOF'
Usage: bootstrap.sh --agent <codex|omp|claude> [--agent ...] [options]

Options:
  --agent NAME   Harness to install for (repeatable, required)
  --group NAME   Group to install (repeatable; default: every group, skipping
                 harnesses a group does not support)
  --dir PATH     Toolkit checkout location (default: ~/agent-toolkit,
                 or $AGENT_TOOLKIT_DIR)
  --repo URL     Toolkit repository (default: SSH GitHub URL, or $AGENT_TOOLKIT_REPO)
  --ref NAME     Branch or tag to clone (default: main)
  --yes          Install without asking after the dry run
  -h, --help     Show this help

An existing checkout is used as it is; run `git pull --ff-only` and
`python3 manage.py update` yourself to update it.
EOF
}

log() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Everything runs inside main(), called on the last line, so a piped run
# (`... | bash -s --`) has read the whole script before any command could
# consume the rest of stdin.
main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent) [[ $# -ge 2 ]] || die "--agent needs a value"; AGENTS+=("$2"); shift 2 ;;
      --group) [[ $# -ge 2 ]] || die "--group needs a value"; SELECTED_GROUPS+=("$2"); shift 2 ;;
      --dir)   [[ $# -ge 2 ]] || die "--dir needs a value"; DIR="$2"; shift 2 ;;
      --repo)  [[ $# -ge 2 ]] || die "--repo needs a value"; REPO="$2"; shift 2 ;;
      --ref)   [[ $# -ge 2 ]] || die "--ref needs a value"; REF="$2"; shift 2 ;;
      --yes|-y) ASSUME_YES=true; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "unknown argument: $1" ;;
    esac
  done

  [[ ${#AGENTS[@]} -gt 0 ]] || { usage >&2; die "at least one --agent is required"; }
  for agent in "${AGENTS[@]}"; do
    case "$agent" in codex|omp|claude) ;; *) die "unknown agent: $agent (use codex, omp or claude)" ;; esac
  done

  # ---------- prerequisites ----------

  command -v git >/dev/null 2>&1 || die "git is required; install it and rerun"

  # On macOS without the Xcode command-line tools, /usr/bin/python3 is a stub
  # that opens an install dialog instead of running.  Detect that first.
  if [[ "$(uname -s)" == "Darwin" ]] && ! xcode-select -p >/dev/null 2>&1; then
    if ! command -v python3 >/dev/null 2>&1 || [[ "$(command -v python3)" == "/usr/bin/python3" ]]; then
      die "python3 needs the Xcode command-line tools on macOS: run 'xcode-select --install' (or install Python from python.org/Homebrew) and rerun"
    fi
  fi
  command -v python3 >/dev/null 2>&1 || die "python3 (3.8 or newer) is required; install it and rerun"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "python3 is $(python3 -V 2>&1); 3.8 or newer is required"

  # ---------- toolkit checkout ----------

  # Prefer the checkout this script lives in, when run from one.
  script_source="${BASH_SOURCE[0]:-}"
  if [[ -n "$script_source" && -f "$script_source" ]]; then
    script_dir="$(cd "$(dirname "$script_source")" && pwd -P)"
    if [[ -f "$script_dir/manage.py" && -f "$script_dir/toolkit.json" ]]; then
      DIR="$script_dir"
    fi
  fi

  if [[ -e "$DIR" ]]; then
    [[ -f "$DIR/manage.py" && -f "$DIR/toolkit.json" ]] \
      || die "$DIR exists but is not an agent-toolkit checkout; move it aside or pass --dir"
    log "using existing checkout: $DIR"
  else
    log "cloning $REPO ($REF) into $DIR"
    git clone --branch "$REF" -- "$REPO" "$DIR" \
      || die "clone failed; check your GitHub access (SSH key or 'gh auth login'), or pass --repo with an HTTPS URL"
  fi

  # ---------- plan, confirm, install ----------

  install_args=(install)
  if [[ ${#SELECTED_GROUPS[@]} -gt 0 ]]; then
    for group in "${SELECTED_GROUPS[@]}"; do install_args+=(--group "$group"); done
  else
    install_args+=(--all)
  fi
  for agent in "${AGENTS[@]}"; do install_args+=(--agent "$agent"); done

  cd "$DIR"
  log "available groups"
  python3 manage.py list
  log "install plan (dry run)"
  python3 manage.py "${install_args[@]}" --dry-run

  if [[ "$ASSUME_YES" != true ]]; then
    if [[ -r /dev/tty ]] && { : < /dev/tty; } 2>/dev/null; then
      printf 'Proceed with this install? [y/N] ' > /dev/tty
      read -r answer < /dev/tty || answer=""
      [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { log "nothing installed"; exit 0; }
    else
      log "no terminal to confirm on; rerun with --yes to install"
      exit 0
    fi
  fi

  log "installing"
  python3 manage.py "${install_args[@]}"
  log "checking the installation"
  python3 manage.py doctor
  log "done; restart any running agent sessions to pick up new skills"
}

main "$@"
