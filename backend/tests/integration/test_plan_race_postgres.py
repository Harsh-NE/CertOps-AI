"""
The single-use guarantee of an approved renewal plan, tested on real PostgreSQL with many concurrent callers.

    docker run -d --name certops-pgtest -e POSTGRES_PASSWORD=pgtest -p 55432:5432 postgres:16-alpine
    PG_TEST_URL=postgresql+psycopg2://postgres:pgtest@localhost:55432/postgres pytest tests/integration/test_plan_race_postgres.py
"""
import os
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.agent.eval_fixtures import build_fixture
from app.database import Base
from app.renewal.service import Actor, RenewalError, RenewalService

URL = os.environ.get("PG_TEST_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="set PG_TEST_URL to run")]


@pytest.fixture()
def pg():
    engine = create_engine(URL, pool_size=16)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_eight_concurrent_callers_execute_an_approved_plan_exactly_once(pg):
    setup = pg()
    fx = build_fixture(setup)
    setup.close()
    ex = fx["executors"]["letsencrypt"]
    original = ex.execute

    def slow(cert, params):
        time.sleep(0.5)  # keeps the winner busy while the others arrive
        return original(cert, params)

    ex.execute = slow
    n = 8
    gate = threading.Barrier(n)
    outcomes, lock = [], threading.Lock()
    human = Actor("user", "alice", role="operator")

    def attempt():
        db = pg()
        try:
            gate.wait()
            result = RenewalService(db, fx["executors"]).execute(fx["plan_approved"], human).status.value
        except RenewalError as e:
            result = e.code
        finally:
            db.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert ex.exec_calls == 1, outcomes
    assert outcomes.count("auto_renewed") == 1
    assert set(outcomes) - {"auto_renewed"} <= {"plan_in_use", "plan_not_approved"}
    check = pg()
    assert check.get(models.RenewalPlan, fx["plan_approved"]).status == models.RenewalPlanStatus.EXECUTED
    assert check.query(models.RenewalTask).filter_by(dry_run=False, status=models.RenewalTaskStatus.AUTO_RENEWED).count() == 1
    check.close()
