"""Acceptance tests mapped to the assignment's ten required scenarios.

Run from the project root: uv run pytest test_required.py
Shared database/client fixtures live in the root conftest.py.
"""

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import event, func, inspect
from sqlmodel import Session, select

from task_b.db_classes import FeeLineDB, NavanFeeMetadataDB, TaxLineDB, TransactionDB

SEED_KINDS = ("flight", "hotel", "rail", "trip_fee", "agent_call_fee")


class TestRequired:
    """The numbering below matches Tests (required) in the assignment."""

    @pytest.mark.parametrize("kind", SEED_KINDS)
    def test_01_create_and_read_each_transaction_type(
        self, client, seed_payloads, kind
    ):
        """Create all five seeds; GET preserves their complete type-specific details."""
        payload = seed_payloads[kind]
        created = create_transaction(client, payload)

        stored = read_transaction(client, created["id"])

        assert stored == created
        assert isinstance(stored["id"], int) and stored["id"] > 0
        assert stored["external_id"] == payload["external_id"]
        assert stored["type"] == payload["type"]
        assert stored["currency"] == payload["currency"]
        assert money(stored["total"]) == money(payload["total"])
        assert_lines_match(stored["tax_lines"], payload["tax_lines"])
        assert_lines_match(stored["fee_lines"], payload["fee_lines"])
        assert_metadata_matches(kind, stored["metadata"], payload["metadata"])

    def test_02_duplicate_external_id_returns_existing_id(
        self, client, seed_payloads, db_engine
    ):
        """A duplicate returns 409 with the original ID and creates no second row."""
        original = create_transaction(client, seed_payloads["flight"])
        duplicate = copy.deepcopy(seed_payloads["duplicate_flight"])
        duplicate.pop("comment")  # Fixture commentary is not part of the request body.

        response = client.post("/transactions", json=duplicate)

        assert response.status_code == 409
        assert response.json()["id"] == original["id"]
        assert row_count(db_engine, TransactionDB) == 1
        assert read_transaction(client, original["id"]) == original

    def test_03_trip_invoice_matches_expected_fixture(
        self, client, trip, expected_invoice
    ):
        """The complete trip invoices to 685.70 EUR with the expected rollups."""
        invoice = create_invoice(
            client, [transaction["id"] for transaction in trip.values()]
        )

        assert invoice["currency"] == expected_invoice["currency"]
        assert len(invoice["transactions"]) >= expected_invoice["line_count_minimum"]
        assert {key: money(value) for key, value in invoice["totals"].items()} == {
            key: money(value) for key, value in expected_invoice["totals"].items()
        }
        assert {line["type"] for line in invoice["transactions"]} == set(
            expected_invoice["transaction_types_included"]
        )
        assert {
            line["metadata"]["fee_kind"]
            for line in invoice["transactions"]
            if line["type"] == "navan_fee"
        } == set(expected_invoice["navan_fee_kinds_included"])
        assert tax_amounts(invoice["tax_rollup"]) == tax_amounts(
            expected_invoice["tax_rollup_example"]
        )
        assert fee_amounts(invoice["fee_rollup"]) == fee_amounts(
            expected_invoice["fee_rollup_example"]
        )
        assert read_invoice(client, invoice["id"]) == invoice

    def test_04_invoice_remains_frozen_after_source_total_changes(
        self, client, trip, db_engine
    ):
        """A source edit cannot change the saved flight contribution or trip total."""
        invoice = create_invoice(
            client, [transaction["id"] for transaction in trip.values()]
        )
        flight_id = trip["flight"]["id"]

        # Deliberately bypass the API: billed transactions correctly reject PATCH.
        with Session(db_engine) as session:
            flight = session.get(TransactionDB, flight_id)
            assert flight is not None
            flight.total = Decimal("999.00")
            session.commit()

        assert money(read_transaction(client, flight_id)["total"]) == Decimal("999.00")
        frozen = read_invoice(client, invoice["id"])
        assert frozen == invoice
        assert money(frozen["totals"]["flight"]) == Decimal("220.00")
        assert money(frozen["totals"]["grand_total"]) == Decimal("685.70")

    def test_05_mixed_currency_invoice_returns_409(self, client, seed_payloads):
        """EUR and USD transactions cannot be billed on the same invoice."""
        eur = create_transaction(client, seed_payloads["flight"])
        usd_payload = dict(seed_payloads["trip_fee"], currency="USD")
        usd = create_transaction(client, usd_payload)

        response = client.post(
            "/invoices", json={"transaction_ids": [eur["id"], usd["id"]]}
        )

        assert response.status_code == 409
        assert client.get("/invoices").json() == []

    def test_06_concurrent_duplicate_posts_store_one_transaction(
        self, client, seed_payloads, db_engine
    ):
        """Two overlapping HTTP creates resolve to one winner and one conflict."""
        start = threading.Barrier(2)

        def submit():
            start.wait(timeout=5)
            return client.post("/transactions", json=seed_payloads["flight"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit) for _ in range(2)]
            responses = [future.result(timeout=15) for future in futures]

        assert sorted(response.status_code for response in responses) == [201, 409]
        assert len({response.json()["id"] for response in responses}) == 1
        assert row_count(db_engine, TransactionDB) == 1

    def test_07_billed_transaction_patch_returns_409(self, client, billed_flight):
        """Rejected PATCH changes neither the transaction nor its invoice."""
        transaction, invoice = billed_flight

        response = client.patch(
            f"/transactions/{transaction['id']}",
            json={"total": "999.00", "tax_lines": []},
        )

        assert response.status_code == 409
        assert read_transaction(client, transaction["id"]) == transaction
        assert read_invoice(client, invoice["id"]) == invoice

    def test_08_billed_transaction_delete_returns_409(self, client, billed_flight):
        """A billed transaction remains readable after a rejected DELETE."""
        transaction, invoice = billed_flight

        response = client.delete(f"/transactions/{transaction['id']}")

        assert response.status_code == 409
        assert read_transaction(client, transaction["id"]) == transaction
        assert read_invoice(client, invoice["id"]) == invoice

    def test_09_deleting_invoice_preserves_all_source_transactions(self, client, trip):
        """DELETE removes the invoice while retaining every underlying transaction."""
        invoice = create_invoice(
            client, [transaction["id"] for transaction in trip.values()]
        )

        response = client.delete(f"/invoices/{invoice['id']}")

        assert response.status_code == 204
        assert client.get(f"/invoices/{invoice['id']}").status_code == 404
        for transaction in trip.values():
            assert read_transaction(client, transaction["id"]) == transaction

    def test_10_transaction_lists_have_default_and_maximum_page_sizes(
        self, client, seed_payloads
    ):
        """More than 100 rows demonstrate that default and explicit pages are bounded."""
        ids = []
        for number in range(105):
            payload = dict(seed_payloads["trip_fee"], external_id=f"volume-{number}")
            ids.append(create_transaction(client, payload)["id"])

        default_page = client.get("/transactions")
        large_page = client.get("/transactions", params={"limit": 1000})
        offset_page = client.get("/transactions", params={"limit": 10, "offset": 50})

        assert (
            default_page.status_code
            == large_page.status_code
            == offset_page.status_code
            == 200
        )
        assert [item["id"] for item in default_page.json()] == ids[:50]
        assert [item["id"] for item in large_page.json()] == ids[:100]
        assert [item["id"] for item in offset_page.json()] == ids[50:60]


class TestAdditional:
    """Useful validation, update/delete, precision, and persistence edge cases."""

    @pytest.mark.parametrize("resource", ["transactions", "invoices"])
    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    def test_missing_resources_return_404(self, client, resource, method):
        body = (
            {"total": "1.00"}
            if resource == "transactions"
            else {"transaction_ids": [1]}
        )
        options = {"json": body} if method == "patch" else {}

        response = getattr(client, method)(f"/{resource}/99999", **options)

        assert response.status_code == 404

    @pytest.mark.parametrize("field", ["type", "fee_kind"])
    def test_unknown_transaction_types_and_fee_kinds_return_400(
        self, client, seed_payloads, field
    ):
        payload = copy.deepcopy(seed_payloads["trip_fee"])
        if field == "type":
            payload["type"] = "unknown"
        else:
            payload["metadata"]["fee_kind"] = "unknown"

        assert client.post("/transactions", json=payload).status_code == 400
        assert client.get("/transactions").json() == []

    def test_rail_metadata_cannot_patch_a_flight(self, client, seed_payloads):
        flight = create_transaction(client, seed_payloads["flight"])

        response = client.patch(
            f"/transactions/{flight['id']}",
            json={"metadata": seed_payloads["rail"]["metadata"]},
        )

        assert response.status_code == 422
        assert read_transaction(client, flight["id"]) == flight

    @pytest.mark.parametrize("kind", SEED_KINDS)
    def test_unbilled_transaction_updates_metadata_and_preserves_identity(
        self, client, seed_payloads, kind
    ):
        original = create_transaction(client, seed_payloads[kind])
        metadata = copy.deepcopy(seed_payloads[kind]["metadata"])
        if kind == "flight":
            metadata["coupons"][0]["flight_number"] = "UPDATED"
        elif kind == "hotel":
            metadata["address"]["street"] = "Updated street"
        elif kind == "rail":
            metadata["legs"] = [metadata["legs"][0]]
        else:
            metadata["description"] = "Updated fee"

        response = client.patch(
            f"/transactions/{original['id']}",
            json={
                "total": "42.25",
                "metadata": metadata,
                "tax_lines": [],
                "fee_lines": [],
            },
        )

        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["id"] == original["id"]
        assert updated["external_id"] == original["external_id"]
        assert updated["occurred_at"] == original["occurred_at"]
        assert money(updated["total"]) == Decimal("42.25")
        assert updated["tax_lines"] == updated["fee_lines"] == []
        assert_metadata_matches(kind, updated["metadata"], metadata)
        assert read_transaction(client, original["id"]) == updated

    def test_unbilled_delete_removes_tax_and_fee_children(
        self, client, seed_payloads, db_engine
    ):
        flight = create_transaction(client, seed_payloads["flight"])

        assert client.delete(f"/transactions/{flight['id']}").status_code == 204

        assert client.get(f"/transactions/{flight['id']}").status_code == 404
        assert row_count(db_engine, TransactionDB) == 0
        assert row_count(db_engine, TaxLineDB) == row_count(db_engine, FeeLineDB) == 0

    def test_repeated_invoice_ids_are_counted_once(self, client, seed_payloads):
        fee = create_transaction(client, seed_payloads["trip_fee"])

        invoice = create_invoice(client, [fee["id"], fee["id"]])

        assert invoice["transaction_ids"] == [fee["id"]]
        assert len(invoice["transactions"]) == 1
        assert money(invoice["totals"]["grand_total"]) == Decimal("25.00")
        assert money(invoice["totals"]["flight"]) == 0

    def test_invoice_patch_replaces_sources_and_releases_removed_transaction(
        self, client, billed_flight, seed_payloads
    ):
        flight, original_invoice = billed_flight
        hotel = create_transaction(client, seed_payloads["hotel"])

        response = client.patch(
            f"/invoices/{original_invoice['id']}",
            json={"transaction_ids": [hotel["id"]]},
        )

        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["id"] == original_invoice["id"]
        assert updated["transaction_ids"] == [hotel["id"]]
        assert money(updated["totals"]["grand_total"]) == Decimal("331.70")
        assert read_invoice(client, updated["id"]) == updated
        assert (
            client.patch(
                f"/transactions/{flight['id']}", json={"total": "1.00"}
            ).status_code
            == 200
        )
        assert client.delete(f"/transactions/{hotel['id']}").status_code == 409

    @pytest.mark.parametrize("failure", ["missing_id", "mixed_currency"])
    def test_failed_invoice_patch_preserves_original_snapshot(
        self, client, billed_flight, seed_payloads, failure
    ):
        flight, invoice = billed_flight
        other_id, expected_status = 99999, 404
        if failure == "mixed_currency":
            usd = create_transaction(
                client, dict(seed_payloads["trip_fee"], currency="USD")
            )
            other_id, expected_status = usd["id"], 409

        response = client.patch(
            f"/invoices/{invoice['id']}",
            json={"transaction_ids": [flight["id"], other_id]},
        )

        assert response.status_code == expected_status
        assert read_invoice(client, invoice["id"]) == invoice

    def test_deleting_one_of_two_invoices_does_not_unbill_transaction(
        self, client, billed_flight
    ):
        flight, first_invoice = billed_flight
        second_invoice = create_invoice(client, [flight["id"]])

        assert client.delete(f"/invoices/{second_invoice['id']}").status_code == 204

        assert client.delete(f"/transactions/{flight['id']}").status_code == 409
        assert read_invoice(client, first_invoice["id"]) == first_invoice

    @pytest.mark.parametrize("resource", ["transactions", "invoices"])
    def test_empty_lists_return_200_and_an_empty_array(self, client, resource):
        response = client.get(f"/{resource}")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.parametrize("resource", ["transactions", "invoices"])
    @pytest.mark.parametrize("parameters", [{"limit": 0}, {"offset": -1}])
    def test_invalid_pagination_is_rejected(self, client, resource, parameters):
        assert client.get(f"/{resource}", params=parameters).status_code == 422

    def test_invoice_pages_are_bounded_sql_queries_over_saved_snapshots(
        self, client, seed_payloads, db_engine
    ):
        fee = create_transaction(client, seed_payloads["trip_fee"])
        for _ in range(105):
            create_invoice(client, [fee["id"]])
        with Session(db_engine) as session:
            stored = session.get(TransactionDB, fee["id"])
            assert stored is not None
            stored.total = Decimal("999.00")
            session.commit()

        statements = []

        def record_sql(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(db_engine, "before_cursor_execute", record_sql)
        try:
            response = client.get("/invoices", params={"limit": 1000})
        finally:
            event.remove(db_engine, "before_cursor_execute", record_sql)

        assert response.status_code == 200
        assert len(response.json()) == 100
        assert all(
            money(invoice["totals"]["grand_total"]) == Decimal("25.00")
            for invoice in response.json()
        )
        assert len(statements) == 1  # No live transaction/metadata queries.
        assert "LIMIT" in statements[0].upper() and "OFFSET" in statements[0].upper()

    def test_child_insert_failure_rolls_back_the_entire_transaction(
        self, client, seed_payloads, db_engine
    ):
        with db_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER reject_metadata BEFORE INSERT ON navan_fee_metadata "
                "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        payload = copy.deepcopy(seed_payloads["trip_fee"])
        payload["tax_lines"] = [{"name": "VAT", "amount": "1.00"}]
        payload["fee_lines"] = [{"name": "Booking", "amount": "2.00"}]

        response = client.post("/transactions", json=payload)

        assert response.status_code == 500
        for model in (TransactionDB, TaxLineDB, FeeLineDB, NavanFeeMetadataDB):
            assert row_count(db_engine, model) == 0

    def test_rollups_separate_fees_and_distinct_tax_rates(self, client, seed_payloads):
        ids = []
        for kind in ("trip_fee", "agent_call_fee"):
            payload = copy.deepcopy(seed_payloads[kind])
            payload["tax_lines"] = [
                {"name": "Shared", "amount": "0.10"},
                {"name": "Shared", "rate": "0", "amount": "0.20"},
            ]
            payload["fee_lines"] = [{"name": "Shared", "amount": "0.30"}]
            ids.append(create_transaction(client, payload)["id"])

        invoice = create_invoice(client, ids)

        assert len(invoice["tax_rollup"]) == 2
        assert tax_amounts(invoice["tax_rollup"]) == {
            ("Shared", None): Decimal("0.20"),
            ("Shared", Decimal(0)): Decimal("0.40"),
        }
        assert fee_amounts(invoice["fee_rollup"]) == {"Shared": Decimal("0.60")}

    def test_schema_enforces_identity_and_indexed_billing_lookup(self, db_engine):
        schema = inspect(db_engine)
        assert schema.get_pk_constraint("transaction")["constrained_columns"] == ["id"]
        assert schema.get_pk_constraint("invoice")["constrained_columns"] == ["id"]
        assert any(
            item["column_names"] == ["external_id"]
            for item in schema.get_unique_constraints("transaction")
        )
        assert any(
            item["column_names"] == ["transaction_id"]
            for item in schema.get_indexes("invoice_transaction_reference")
        )

    def test_client_cannot_choose_the_transaction_id(self, client, seed_payloads):
        payload = dict(seed_payloads["trip_fee"], id=123)
        assert client.post("/transactions", json=payload).status_code == 422

    def test_money_with_more_than_two_decimal_places_is_rejected(
        self, client, seed_payloads
    ):
        payload = dict(seed_payloads["trip_fee"], total="0.001")
        assert client.post("/transactions", json=payload).status_code == 422


# Small assertion/request helpers keep the scenarios above focused on behavior.


def money(value):
    return Decimal(str(value))


def create_transaction(client, payload):
    response = client.post("/transactions", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_invoice(client, transaction_ids):
    response = client.post("/invoices", json={"transaction_ids": transaction_ids})
    assert response.status_code == 201, response.text
    return response.json()


def read_transaction(client, transaction_id):
    response = client.get(f"/transactions/{transaction_id}")
    assert response.status_code == 200, response.text
    return response.json()


def read_invoice(client, invoice_id):
    response = client.get(f"/invoices/{invoice_id}")
    assert response.status_code == 200, response.text
    return response.json()


def row_count(engine, model):
    with Session(engine) as session:
        return session.exec(select(func.count()).select_from(model)).one()


def assert_lines_match(actual, expected):
    assert len(actual) == len(expected)
    for stored, source in zip(actual, expected, strict=True):
        assert stored["name"] == source["name"]
        assert money(stored["amount"]) == money(source["amount"])
        if source.get("rate") is not None:
            assert money(stored["rate"]) == money(source["rate"])


def assert_metadata_matches(kind, actual, expected):
    if kind == "flight":
        assert actual["origin"] == expected["origin"]
        assert actual["destination"] == expected["destination"]
        assert money(actual["fare"]) == money(expected["fare"])
        assert actual["coupons"] == expected["coupons"]
    elif kind == "hotel":
        for field in ("name", "address", "check_in", "check_out", "nights"):
            assert actual[field] == expected[field]
        assert money(actual["room_rate_per_night"]) == money(
            expected["room_rate_per_night"]
        )
        assert_lines_match(actual["extras"], expected.get("extras", []))
    elif kind == "rail":
        assert actual["legs"] == expected["legs"]
    else:
        assert actual == expected


def tax_amounts(lines):
    return {
        (
            line["name"],
            money(line["rate"]) if line.get("rate") is not None else None,
        ): money(line["amount"])
        for line in lines
    }


def fee_amounts(lines):
    return {line["name"]: money(line["amount"]) for line in lines}
