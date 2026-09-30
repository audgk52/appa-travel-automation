"""Git / PII boundary verification (audit S1).

Lightweight `git check-ignore` checks: credential/data artifacts inside the Hotel
Ops dirs stay ignored, while Python source, tests, and the PRD remain trackable.
Skips cleanly when not run inside the git working tree.
"""
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]      # …/Travel Automation


def _is_git_repo():
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=REPO, capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _is_git_repo(), reason="not inside a git work tree")


def _ignored(relpath):
    return subprocess.run(["git", "check-ignore", "-q", relpath], cwd=REPO).returncode == 0


@pytest.mark.parametrize("relpath", [
    "05. Hotel Ops/hotelops_pg/.env",
    "05. Hotel Ops/hotelops_pg/service.pem",
    "05. Hotel Ops/hotelops_pg/api.key",
    "05. Hotel Ops/secret.docx",
    "05. Hotel Ops/real_rooming.csv",
    "05. Hotel Ops/creds.json",
])
def test_secrets_and_data_are_ignored(relpath):
    assert _ignored(relpath), f"{relpath} should be gitignored"


@pytest.mark.parametrize("relpath", [
    "05. Hotel Ops/hotelops_pg/spine.py",
    "05. Hotel Ops/tests/test_spine.py",
    "05. Hotel Ops/conftest.py",
    "05. Hotel Ops/PRD_HotelOps_PG_RoomingList.md",
])
def test_code_and_docs_remain_trackable(relpath):
    assert not _ignored(relpath), f"{relpath} should be trackable"
