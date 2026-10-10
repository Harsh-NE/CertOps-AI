import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.agent.eval_fixtures import FakeRenewalExecutor, build_fixture
from app.database import Base
from app.renewal.base import ExecResult, SecretStoreError
from app.renewal.service import Actor, RenewalError, RenewalService
from app.timeutil import utcnow

HUMAN = Actor("user", "alice", role="operator")


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    db = factory()
    fx = build_fixture(db)
    cert = db.get(models.Certificate, fx["ids"]["checkout"])
    yield db, fx, cert, fx["executors"]["letsencrypt"]
    db.close()


def svc(env):
    db, fx, *_ = env
    return RenewalService(db, fx["executors"])


def test_plan_is_a_dry_run_that_records_history_and_executes_nothing(env):
    db, fx, cert, ex = env
    plan = svc(env).plan(cert, Actor("agent", "alice"))
    assert plan.status == models.RenewalPlanStatus.PENDING_APPROVAL and plan.created_by == "agent:alice"
    assert ex.exec_calls == 0
    dry = db.query(models.RenewalTask).filter_by(certificate_id=cert.id, dry_run=True).all()
    assert len(dry) == 1 and dry[0].status == models.RenewalTaskStatus.DRY_RUN


@pytest.mark.parametrize("actor", [Actor("agent", "alice", role="admin"), Actor("system", "cron", role="admin"),
                                   Actor("user", "bob", role="viewer")])
def test_only_a_signed_in_operator_can_approve(env, actor):
    plan = svc(env).plan(env[2], HUMAN)
    with pytest.raises(RenewalError) as e:
        svc(env).approve(plan.id, actor)
    assert e.value.code == "approval_requires_human"


def test_execution_requires_approval_and_is_single_use(env):
    db, fx, cert, ex = env
    s = svc(env)
    plan = s.plan(cert, Actor("agent", "alice"))
    with pytest.raises(RenewalError) as e:
        s.execute(plan.id, Actor("agent", "alice"))
    assert e.value.code == "approval_required" and ex.exec_calls == 0
    s.approve(plan.id, HUMAN)
    task = s.execute(plan.id, Actor("agent", "alice"))
    assert task.status == models.RenewalTaskStatus.AUTO_RENEWED and task.new_serial == "feedface" and ex.exec_calls == 1
    assert "approved by user:alice" in task.triggered_by
    with pytest.raises(RenewalError) as e:
        s.execute(plan.id, HUMAN)
    assert e.value.code == "plan_not_approved" and ex.exec_calls == 1


def test_concurrent_executions_of_one_plan_run_the_renewal_once(tmp_path):
    """Two threads with SEPARATE connections (a file database) race to execute the same approved plan."""
    engine = create_engine(f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    setup = factory()
    fx = build_fixture(setup)
    ex = fx["executors"]["letsencrypt"]
    original = ex.execute

    def slow(c, p):
        time.sleep(0.3)  # long enough for the second caller to arrive while the first is running
        return original(c, p)

    ex.execute = slow
    plan_id = fx["plan_approved"]
    setup.close()
    gate = threading.Barrier(2)
    outcomes = []

    def attempt():
        db = factory()
        try:
            gate.wait()
            outcomes.append(RenewalService(db, fx["executors"]).execute(plan_id, HUMAN).status.value)
        except RenewalError as err:
            outcomes.append(err.code)
        finally:
            db.close()

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert ex.exec_calls == 1
    assert sorted(outcomes) in (["auto_renewed", "plan_in_use"], ["auto_renewed", "plan_not_approved"])


def test_expired_approval_cannot_be_used(env):
    db, fx, cert, ex = env
    s = svc(env)
    plan = s.plan(cert, HUMAN)
    s.approve(plan.id, HUMAN)
    plan.expires_at = utcnow().replace(year=2020)
    db.commit()
    with pytest.raises(RenewalError) as e:
        s.execute(plan.id, HUMAN)
    assert e.value.code == "plan_expired" and ex.exec_calls == 0


def test_plan_bound_to_the_certificate_state(env):
    db, fx, cert, ex = env
    s = svc(env)
    plan = s.plan(cert, HUMAN)
    s.approve(plan.id, HUMAN)
    cert.fingerprint_sha256 = "f" * 64  # the certificate was replaced after the plan was approved
    db.commit()
    with pytest.raises(RenewalError) as e:
        s.execute(plan.id, HUMAN)
    assert e.value.code == "plan_stale" and ex.exec_calls == 0


def test_non_executable_plans_cannot_be_approved(env):
    db, fx, *_ = env
    acm = db.get(models.Certificate, fx["ids"]["acm"])
    plan = svc(env).plan(acm, HUMAN)
    assert plan.executable is False and "ACM" in plan.not_executable_reason
    with pytest.raises(RenewalError) as e:
        svc(env).approve(plan.id, HUMAN)
    assert e.value.code == "not_executable"


def test_rejected_plans_stay_dead(env):
    s = svc(env)
    plan = s.plan(env[2], HUMAN)
    s.reject(plan.id, HUMAN)
    with pytest.raises(RenewalError):
        s.approve(plan.id, HUMAN)
    with pytest.raises(RenewalError):
        s.execute(plan.id, HUMAN)


@pytest.mark.parametrize("result,expected", [
    (ExecResult(False, error="CA said no"), models.RenewalTaskStatus.FAILED),
    (ExecResult(False, action_taken=False, error="not applicable"), models.RenewalTaskStatus.SKIPPED),
])
def test_failures_are_recorded_honestly(env, result, expected):
    db, fx, cert, ex = env
    ex.execute = lambda c, p: result
    s = svc(env)
    plan = s.plan(cert, HUMAN)
    s.approve(plan.id, HUMAN)
    task = s.execute(plan.id, HUMAN)
    assert task.status == expected and "error" in task.notes
    assert db.get(models.RenewalPlan, plan.id).status == models.RenewalPlanStatus.FAILED


def test_an_executor_crash_is_recorded_not_raised(env):
    db, fx, cert, ex = env

    def boom(c, p):
        raise RuntimeError("kaboom")

    ex.execute = boom
    s = svc(env)
    plan = s.plan(cert, HUMAN)
    s.approve(plan.id, HUMAN)
    task = s.execute(plan.id, HUMAN)
    assert task.status == models.RenewalTaskStatus.FAILED and "kaboom" in task.notes


def test_certificate_without_provider_cannot_be_planned(env):
    db, fx, cert, ex = env
    cert.renewal_provider = None
    with pytest.raises(RenewalError) as e:
        svc(env).plan(cert, HUMAN)
    assert e.value.code == "no_provider"


def test_secret_store_errors_are_a_distinct_type():
    assert issubclass(SecretStoreError, Exception) and FakeRenewalExecutor().name == "letsencrypt"


def test_repeated_plan_requests_reuse_the_open_plan(env):
    db, fx, cert, ex = env
    s = svc(env)
    first = s.plan(cert, Actor("agent", "alice"))
    again = s.plan(cert, Actor("agent", "alice"))
    assert again.id == first.id
    assert db.query(models.RenewalPlan).filter_by(certificate_id=cert.id).count() == 1
    assert db.query(models.RenewalTask).filter_by(certificate_id=cert.id, dry_run=True).count() == 1  # no history spam


def test_an_actor_cannot_flood_the_approval_queue(env, monkeypatch):
    from app.config import settings
    db, fx, cert, ex = env
    monkeypatch.setattr(settings, "RENEWAL_MAX_OPEN_PLANS_PER_ACTOR", 2)
    s = svc(env)
    agent = Actor("agent", "mallory")
    ids = [fx["ids"][k] for k in ("pay", "checkout", "legacy")]
    s.plan(db.get(models.Certificate, ids[0]), agent)
    s.plan(db.get(models.Certificate, ids[1]), agent)
    with pytest.raises(RenewalError) as e:
        s.plan(db.get(models.Certificate, ids[2]), agent)
    assert e.value.code == "too_many_open_plans"
    s.plan(db.get(models.Certificate, ids[2]), Actor("agent", "someone-else"))  # other actors are unaffected


def test_a_finished_plan_does_not_block_a_new_one(env):
    db, fx, cert, ex = env
    s = svc(env)
    first = s.plan(cert, HUMAN)
    s.reject(first.id, HUMAN)
    assert s.plan(cert, HUMAN).id != first.id
