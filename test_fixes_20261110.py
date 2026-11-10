"""Regression tests for fixes of 2026-11-10: orphan topics, GS checkpoints, WS subprotocol, upload content checks."""
import io

import main as m
from test_backend_audit import audit_ids, auth, client, student, make_topic, apply, decide  # noqa: F401  (fixtures)


def test_head_can_approve_application_of_topic_without_active_supervisor(client):
    _, sh = student(client, "orphan")
    with m.SessionLocal() as db:
        topic = m.Topic(title="Orphan", title_ru="Orphan", supervisor_id=None, quota_total=1, quota_used=0, status="available")
        db.add(topic); db.commit(); tid = topic.id
    app_id = apply(client, sh, tid)
    head = auth(client, "head@test.com")
    assert decide(client, head, app_id).json()["status"] == "APPROVED"


def test_head_still_cannot_skip_active_supervisor(client):
    _, sh = student(client, "active")
    app_id = apply(client, sh, make_topic())
    assert decide(client, auth(client, "head@test.com"), app_id).status_code == 400


def test_gale_shapley_apply_creates_checkpoints(client):
    sid, sh = student(client, "gs")
    apply(client, sh, make_topic(quota=1))
    assert client.post("/api/allocation/gale-shapley?apply=true", headers=auth(client, "head@test.com")).status_code == 200
    with m.SessionLocal() as db:
        assert db.query(m.Checkpoint).filter_by(student_id=sid).count() == 4


def test_apply_to_missing_topic_is_404_even_if_priority_busy(client):
    _, sh = student(client, "missing")
    apply(client, sh, make_topic(), priority=1)
    r = client.post("/api/applications/apply", headers=sh, json={"topic_id": 99999, "priority": 1})
    assert r.status_code == 404


def test_websocket_accepts_token_in_subprotocol_only(client):
    sid, sh = student(client, "ws")
    token = sh["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/{sid}", subprotocols=["meta-auth", token]) as ws:
        assert ws.accepted_subprotocol == "meta-auth"


def test_upload_rejects_fake_archives_and_binary_txt(client):
    with m.SessionLocal() as db:
        cp = db.query(m.Checkpoint).filter_by(student_id=1).first().id
    h = auth(client)
    for name, body in (("a.zip", b"not a zip"), ("a.rar", b"not rar"), ("a.doc", b"plain"), ("a.txt", b"ab\x00cd")):
        r = client.post("/api/checkpoints/upload", headers=h, data={"checkpoint_id": cp}, files={"file": (name, io.BytesIO(body))})
        assert r.status_code == 400, (name, r.text)
