"""Regression for #154423: retry decisions use current diagnostics, not stale prose."""

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_RATE_LIMIT_COOLDOWN_SECONDS", "0")
    monkeypatch.setattr(kbd.time, "time", lambda: 1_800_000_000)
    kb.init_db()
    with kbc.connect() as conn:
        yield conn


def _failed_run(conn, tid, error, outcome="spawn_failed"):
    lane = "review" if kb.get_task(conn, tid).status == "review" else "ready"
    claim = kb.claim_review_task if lane == "review" else kb.claim_task
    assert claim(conn, tid)
    with kb.write_txn(conn):
        kb._end_run(conn, tid, outcome=outcome, error=error)
        conn.execute(
            "UPDATE tasks SET status=?, claim_lock=NULL, claim_expires=NULL, "
            "last_failure_error=? WHERE id=?", (lane, error, tid),
        )


def test_review_handoff_supersedes_quota_stamp_but_not_reviewer_auth_failure(board):
    tid = kb.create_task(board, title="handoff", assignee="worker")
    _failed_run(board, tid, "429 quota exhausted", outcome="rate_limited")
    # The handoff and subsequent reviewer failure deliberately share a timestamp.
    with kb.write_txn(board):
        board.execute("UPDATE task_runs SET ended_at=ended_at-1 WHERE task_id=?", (tid,))
    assert kb.request_review(board, tid, summary="ready", reviewer="reviewer")
    assert kb.get_task(board, tid).last_failure_error == "429 quota exhausted"
    assert kbd.check_respawn_guard(board, tid, lane="review") is None

    # A newer reviewer failure is still a blocker: review is not a blanket exemption.
    _failed_run(board, tid, "403 forbidden")
    assert kbd.check_respawn_guard(board, tid, lane="review") == "blocker_auth"


@pytest.mark.parametrize("error", [
    "gh auth setup-git doesn't help; operator re-auths gh",
    "checking the quota wall implementation",
    "subscription documentation; billing examples",
    "pid 1 exited. Worker's last output: 403 forbidden; quota exhausted",
])
def test_worker_prose_is_not_a_blocker_diagnosis(board, error):
    tid = kb.create_task(board, title="prose", assignee="worker")
    _failed_run(board, tid, error)
    assert kbd.check_respawn_guard(board, tid) is None


@pytest.mark.parametrize("error", [
    "401 unauthorized", "403 forbidden", "authentication error from provider",
    "quota exhausted", "insufficient_quota", "exceeded your current quota",
    "billing limit reached", "token expired", "invalid api key",
])
def test_current_provider_diagnosis_remains_guarded(board, error):
    tid = kb.create_task(board, title="provider failure", assignee="worker")
    _failed_run(board, tid, error)
    assert kbd.check_respawn_guard(board, tid) == "blocker_auth"
