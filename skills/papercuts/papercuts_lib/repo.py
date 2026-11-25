"""Which repository a papercut is about: host/owner/name, never credentials."""
import os
import subprocess

from .model import PapercutsError, normal_repo

MISSING = ("Cannot tell which repository this papercut is about. Run from a Git checkout with an "
           "'origin' remote, pass --repo HOST/OWNER/NAME (or a remote URL), set PAPERCUTS_REPO, "
           "or use --repo none for a papercut not tied to a repository.")


def _git(cwd, *args):
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def detect(cwd=None):
    """Remote URL of 'origin', or of the only remote; None outside a checkout or if ambiguous."""
    cwd = cwd or os.getcwd()
    url = _git(cwd, "remote", "get-url", "origin")
    if not url:
        remotes = (_git(cwd, "remote") or "").split()
        if len(remotes) == 1:
            url = _git(cwd, "remote", "get-url", remotes[0])
    return url or None


def resolve(flag=None, cwd=None):
    """--repo, then PAPERCUTS_REPO, then the Git remote of cwd. Fails rather than guessing."""
    if flag is not None:
        return normal_repo(flag)
    if os.environ.get("PAPERCUTS_REPO"):
        return normal_repo(os.environ["PAPERCUTS_REPO"])
    url = detect(cwd)
    if url is None:
        raise PapercutsError(MISSING)
    try:
        return normal_repo(url)
    except PapercutsError as exc:
        raise PapercutsError(f"The Git remote is not a recognisable repository. {MISSING}") from exc
