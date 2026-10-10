"""HTTP-level tests: plan -> human approve -> execute, role gates, and the agent endpoint."""
import pytest

from app import models
from app.agent.eval_fixtures import FakeRenewalExecutor
from app.agent.testing import KeywordPolicyModel
from app.database import SessionLocal
from app.main import app
from app.renewal.service import RenewalService
from app.routers.renewals import get_service


@pytest.fixture()
def fake_executors():
    ex = {"letsencrypt": FakeRenewalExecutor()}

    def override():
        db = SessionLocal()
        try:
            yield RenewalService(db, ex)
        finally:
            db.close()

    app.dependency_overrides[get_service] = override
    yield ex
    app.dependency_overrides.pop(get_service, None)


def make_cert(name="api-renew.test", provider="letsencrypt"):
    db = SessionLocal()
    try:
        c = models.Certificate(common_name=name, renewal_provider=provider, fingerprint_sha256="ab" * 32)
        db.add(c)
        db.commit()
        return c.id
    finally:
        db.close()


def test_full_flow_with_human_approval(client, admin_headers, fake_executors):
    cid = make_cert()
    plan = client.post(f"/api/renewals/{cid}/plan", headers=admin_headers).json()
    assert plan["status"] == "pending_approval" and plan["executable"] and plan["steps"]
    r = client.post(f"/api/renewals/plans/{plan['id']}/execute", headers=admin_headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "approval_required"
    assert fake_executors["letsencrypt"].exec_calls == 0
    approved = client.post(f"/api/renewals/plans/{plan['id']}/approve", headers=admin_headers).json()
    assert approved["status"] == "approved" and approved["approved_by"] == "user:admin"
    task = client.post(f"/api/renewals/plans/{plan['id']}/execute", headers=admin_headers).json()
    assert task["status"] == "auto_renewed" and task["new_serial"] == "feedface" and task["dry_run"] is False
    again = client.post(f"/api/renewals/plans/{plan['id']}/execute", headers=admin_headers)
    assert again.status_code == 409 and fake_executors["letsencrypt"].exec_calls == 1
    got = client.get(f"/api/renewals/plans/{plan['id']}", headers=admin_headers).json()
    assert got["status"] == "executed" and got["result"]["success"] is True


def test_viewers_cannot_plan_approve_or_execute(client, admin_headers, viewer_headers, fake_executors):
    cid = make_cert("viewer-block.test")
    plan = client.post(f"/api/renewals/{cid}/plan", headers=admin_headers).json()
    for path in (f"/api/renewals/{cid}/plan", f"/api/renewals/plans/{plan['id']}/approve",
                 f"/api/renewals/plans/{plan['id']}/execute"):
        assert client.post(path, headers=viewer_headers).status_code == 403
    assert client.get("/api/renewals/plans", headers=viewer_headers).status_code == 200


def test_the_old_confirm_flag_endpoint_is_gone(client, admin_headers):
    cid = make_cert("old-endpoint.test")
    r = client.post(f"/api/renewals/{cid}/renew-now?dry_run=false&confirm=true", headers=admin_headers)
    assert r.status_code in (404, 405)


def test_plan_errors_map_to_http_codes(client, admin_headers, fake_executors):
    assert client.post("/api/renewals/plans/nope/approve", headers=admin_headers).status_code == 404
    cid = make_cert("no-provider.test", provider=None)
    r = client.post(f"/api/renewals/{cid}/plan", headers=admin_headers)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "no_provider"


def test_agent_endpoint_end_to_end(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.agent.llm.get_chat_model", lambda model=None: KeywordPolicyModel())
    r = client.post("/api/agent/query", json={"question": "Show me all expired certificates"}, headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["used_tools"] == ["search_certificates"] and body["configured"] and "tool_results" not in body
    audit = client.get("/api/agent/tool-calls", headers=admin_headers).json()
    assert audit[0]["tool"] == "search_certificates" and audit[0]["actor"] == "agent:admin"


def test_agent_for_a_viewer_is_read_only(client, viewer_headers, monkeypatch):
    monkeypatch.setattr("app.agent.llm.get_chat_model", lambda model=None: KeywordPolicyModel())
    r = client.post("/api/agent/query", json={"question": "Create a renewal plan for anything.example.com"},
                    headers=viewer_headers).json()
    assert [t["status"] for t in r["tool_calls"]] == ["denied"]
    assert client.get("/api/agent/tool-calls", headers=viewer_headers).status_code == 403


def test_agent_without_a_key_says_so(client, admin_headers, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    body = client.post("/api/agent/query", json={"question": "hello"}, headers=admin_headers).json()
    assert body["configured"] is False and "GEMINI_API_KEY" in body["answer"]
    assert client.get("/api/health").json()["ai_agent_enabled"] in (True, False)


def test_unconfigured_agent_answers_without_loading_the_agent_stack(client, admin_headers, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("run_agent_query must not be reached without a model")

    monkeypatch.setattr("app.agent.llm.get_chat_model", lambda model=None: None)
    monkeypatch.setattr("app.agent.agent.run_agent_query", boom)
    body = client.post("/api/agent/query", json={"question": "hi"}, headers=admin_headers).json()
    assert body["configured"] is False and body["used_tools"] == []


def test_changing_the_renewal_provider_refreshes_the_stored_risk_score(client, admin_headers):
    from datetime import timedelta

    from app.timeutil import utcnow
    db = SessionLocal()
    c = models.Certificate(common_name="rescore.test", not_after=utcnow() + timedelta(days=5), risk_score=-1.0)
    db.add(c)
    db.commit()
    cid = c.id
    db.close()
    r = client.post(f"/api/certificates/{cid}/toggle-auto-renew?provider=letsencrypt", headers=admin_headers).json()
    assert r["auto_renew_enabled"] is True and r["renewal_provider"] == "letsencrypt"
    assert r["risk_score"] != -1.0 and r["last_validated_at"] is not None
