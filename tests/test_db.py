"""app.storage.db — activity log and jobs.

test_add_activity_survives_readonly_db is a direct regression test for a
real bug: a Settings-page save was doing its real work successfully (the
override file write) and then 500ing the whole request because the
*audit-trail* write afterward failed on an unwritable database. Fixed by
making add_activity swallow its own sqlite3.Error instead of propagating
it — see the docstring on add_activity itself.
"""

from __future__ import annotations

from app.storage import db


def test_add_activity_and_list(isolated_env):
    db.init_db()
    db.add_activity("test", "hello world", level="info")
    rows = db.list_activity(limit=10)
    assert any(r["category"] == "test" and r["message"] == "hello world" for r in rows)


def test_add_activity_survives_readonly_db(isolated_env, sqlite_readonly):
    db.init_db()
    sqlite_readonly()

    # Must not raise — this is the exact bug: a failed audit-log write
    # should degrade gracefully, not take down the caller's request.
    db.add_activity("settings", "this write cannot succeed")


def test_jobs_lifecycle(isolated_env):
    db.init_db()
    job_id = db.create_job("scan")
    job = db.get_job(job_id)
    assert job["status"] == "pending"

    db.update_job(job_id, status="running", progress_current=50, progress_total=100)
    job = db.get_job(job_id)
    assert job["status"] == "running"
    assert job["progress_current"] == 50

    db.update_job(job_id, status="success", result={"scanned": 12})
    job = db.get_job(job_id)
    assert job["status"] == "success"
    assert job["result"] == {"scanned": 12}


def test_update_job_rejects_invalid_status(isolated_env):
    db.init_db()
    job_id = db.create_job("scan")
    try:
        db.update_job(job_id, status="not-a-real-status")
        assert False, "should have raised ValueError"
    except ValueError:
        pass
