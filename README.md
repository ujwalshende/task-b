# Transactions and invoices

## Linux setup

The commands below use Bash and require `curl`. Install uv using Astral's
[official installer](https://docs.astral.sh/uv/getting-started/installation/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

The `export` makes the default installation available in the current shell.
From the project root, install Python and the locked project dependencies:

```bash
uv python install 3.15
uv sync --locked --dev
```

The project requires Python 3.15 or newer. uv manages the interpreter and the
project's `.venv`, so no manual virtual-environment activation is needed.

## Run the service

```bash
uv run python main.py
```

Startup applies Alembic migrations before serving the API at
`http://127.0.0.1:8000`. SQLite data is stored in `app.db` in the project root. `app.db` gets automatically created when you start the api for the first time.
Interactive request and response schemas are available at
`http://127.0.0.1:8000/docs`. Stop the server with `Ctrl+C`.

## API examples

Run these commands from the project root in a second terminal while the server
is running.

`POST /transactions` accepts the transaction bodies in `fixtures/seed-payloads.json`,
with a single `metadata` object. A successful create returns `201` and the stored
transaction, including the server-generated integer `id`. Client-supplied IDs are
rejected. Unknown transaction types and Navan fee kinds return `400`; other invalid
bodies return `422`.

For example, post the flight fixture twice to see a `201` followed
by a `409` containing the original ID:

```bash
flight=$(
  uv run python -c 'import json; print(json.dumps(json.load(open("fixtures/seed-payloads.json"))["flight"]))'
)
curl -i http://127.0.0.1:8000/transactions \
  -H 'Content-Type: application/json' \
  --data "$flight"
curl -i http://127.0.0.1:8000/transactions \
  -H 'Content-Type: application/json' \
  --data "$flight"
```

A repeated `external_id` returns `409` with the existing `id`. The database unique
constraint also protects overlapping requests. Header, tax lines, fee lines, and
metadata are committed together, and failures roll back the whole write.

`GET /transactions/{id}` returns `200` with the stored transaction, including its
`id`, `type`, `metadata`, `tax_lines`, and `fee_lines`. A missing transaction returns
`404`.

```bash
curl -i http://127.0.0.1:8000/transactions/1
```

`GET /transactions` and `GET /invoices` return JSON arrays ordered by ascending
`id`. Both accept `limit` (default `50`, capped at `100`) and `offset` (default
`0`, non-negative). For example, `limit=1000` returns at most 100 records. Invalid
or non-positive limits and invalid or negative offsets return `422`. Empty pages
return `200` with `[]`.

```bash
curl -i 'http://127.0.0.1:8000/transactions?limit=25&offset=50'
curl -i 'http://127.0.0.1:8000/invoices?limit=25&offset=50'
```

Pagination uses database `ORDER BY id`, `LIMIT`, and `OFFSET` before rows are
materialized. Transaction children load in batches for the selected page.
Invoice lists use saved snapshot data and avoid loading source transactions.
Concurrent inserts/deletes can shift row offsets between requests.

The schema provides a unique index backing `transaction.external_id`, primary-key
lookups for transaction/invoice IDs, and
`ix_invoice_transaction_reference_transaction_id` for billing checks. See
`ARCHITECTURE.md` for the High volume tradeoffs.

`POST /invoices` accepts `{"transaction_ids": [1, 2]}`; a single-entry list is also
supported. Repeated IDs are deduplicated in request order. It returns `201` with
the invoice ID, currency, transaction snapshots, separate tax and fee rollups,
and a `totals` breakdown for flight, hotel, rail, trip fee, agent call fee, and
grand total. Absent categories have zero totals. The header's `total` is also
retained as the grand total.

Any missing transaction returns `404`; mixed currencies return `409`. Both leave
existing invoice membership and snapshots unchanged. Invoice creation locks the
SQLite write transaction before reading and commits the snapshot and membership
updates together.

`GET /invoices/{id}` returns the saved invoice, or `404` when missing. All invoice
amounts and metadata come from the saved snapshots, so later source transaction
changes cannot affect the invoice. Tax rollups group by name and rate, including
an absent rate as its own group. Fee rollups group by name.

```bash
curl -i http://127.0.0.1:8000/invoices \
  -H 'Content-Type: application/json' \
  --data '{"transaction_ids":[1,2]}'
curl -i http://127.0.0.1:8000/invoices/1
```

`PATCH /transactions/{id}` accepts any subset of `total`, `occurred_at`,
`tax_lines`, `fee_lines`, and `metadata`, and returns the updated transaction with
`200`. Omitted fields are preserved; provided lists and metadata replace the
complete previous values. Empty tax/fee lists clear them. Explicit null values
are rejected. `id`, `external_id`, `type`, and `currency` cannot be patched.

`DELETE /transactions/{id}` returns `204` and removes the transaction and all
its tax, fee, and metadata children. Missing records return `404`. Both PATCH
and DELETE return `409` without changes while any saved invoice includes the
transaction. The indexed `invoice_transaction_reference` table is the sole source
of invoice membership. `InvoiceDB.transactions` manages this many-to-many
relationship, and `TransactionDB.invoices` provides a read-only lookup of all
including invoices. Deleting one invoice leaves references from other invoices
intact. There is no single `invoice_id` on a transaction.

`PATCH /invoices/{id}` accepts the same `transaction_ids` body as invoice creation
and returns `200`. It replaces membership and rebuilds snapshots, rollups, and
totals from current stored transactions, preserving the invoice ID. Removed
transactions are detached and retained. Missing invoices or transactions return
`404`; mixed currencies return `409`, preserving the original invoice.

`DELETE /invoices/{id}` returns `204`, removes its snapshots and references, and
preserves the underlying transactions. A missing invoice returns `404`.

Each update/delete locks SQLite before reading and commits or rolls back the
entire operation. Overlapping operations are serialized; the last completed
PATCH wins, while a PATCH following deletion returns `404`.

```bash
curl -i -X PATCH http://127.0.0.1:8000/transactions/1 \
  -H 'Content-Type: application/json' \
  --data '{"total":"230.00"}'
curl -i -X PATCH http://127.0.0.1:8000/invoices/1 \
  -H 'Content-Type: application/json' \
  --data '{"transaction_ids":[2]}'
curl -i -X DELETE http://127.0.0.1:8000/invoices/1
curl -i -X DELETE http://127.0.0.1:8000/transactions/1
```

Monetary fields use `Decimal` with two decimal places and database `NUMERIC(12, 2)`
columns. JSON responses represent decimal amounts as strings to preserve precision.

## Tests

Run the readable acceptance suite from the project root:

```bash
uv run pytest test_required.py
```

The cases live in `tests/test_required.py`: `TestRequired` follows the assignment's
ten numbered requirements, and `TestAdditional` covers useful edge cases. A small
root entry point supports the command above. Shared fixtures in `conftest.py`
apply migrations to an isolated SQLite file for every test; the local `app.db`
is untouched. The suite uses FastAPI's HTTP test client, including real concurrent
requests for duplicate identity testing.

Run all tests with `uv run pytest`. Default discovery uses `tests/`, so the root
entry point does not duplicate the acceptance cases. Repository-local temporary
storage and disabled pytest caching are configured in `pyproject.toml`.

The HTTP tests use a temporary file-backed SQLite database and cover create/read
round trips for the fixtures, missing records, duplicate and concurrent requests,
validation, invoice totals and rollups, snapshot stability, update/delete billing
protections, overlapping writes, SQL pagination bounds, indexes, initial schema
creation, and rollback after transaction or invoice writes fail.
