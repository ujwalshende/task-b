"""Shared fixtures for the readable acceptance suite in tests/test_required.py."""

import json
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, create_engine

from alembic import command
from task_b.api import api
from task_b.database import enable_foreign_keys, get_session

PROJECT_ROOT = Path(__file__).resolve().parent
TRIP_KINDS = ("flight", "hotel", "rail", "trip_fee", "agent_call_fee")


@pytest.fixture
def seed_payloads():
    return json.loads((PROJECT_ROOT / "fixtures/seed-payloads.json").read_text())


@pytest.fixture
def expected_invoice():
    return json.loads((PROJECT_ROOT / "fixtures/expected-invoice.json").read_text())


@pytest.fixture
def db_engine(tmp_path):
    """Every test gets a real, freshly migrated SQLite file."""
    database_url = f"sqlite:///{(tmp_path / 'acceptance.db').as_posix()}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", enable_foreign_keys)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def client(db_engine):
    """Exercise HTTP routing/validation while keeping the local app.db untouched."""

    def test_session():
        with Session(db_engine) as session:
            yield session

    original_overrides = api.dependency_overrides.copy()
    api.dependency_overrides[get_session] = test_session
    try:
        with TestClient(api, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        api.dependency_overrides.clear()
        api.dependency_overrides.update(original_overrides)


@pytest.fixture
def trip(client, seed_payloads):
    """Create the five trip fixtures and return responses keyed by fixture name."""
    transactions = {}
    for kind in TRIP_KINDS:
        response = client.post("/transactions", json=seed_payloads[kind])
        assert response.status_code == 201, response.text
        transactions[kind] = response.json()
    return transactions


@pytest.fixture
def billed_flight(client, seed_payloads):
    response = client.post("/transactions", json=seed_payloads["flight"])
    assert response.status_code == 201, response.text
    transaction = response.json()

    response = client.post("/invoices", json={"transaction_ids": [transaction["id"]]})
    assert response.status_code == 201, response.text
    return transaction, response.json()
