# Architecture

FastAPI routes in `api.py` handle HTTP status codes and request validation.
`api_classes.py` contains typed request/response models and conversions.
`queries.py` owns database queries and transaction boundaries. SQLModel tables
live in `db_classes.py`, and Alembic creates the SQLite schema.

Transactions store shared scalar fields plus normalized tax/fee lines and
type-specific metadata children. The database enforces unique `external_id`
values. A duplicate insert rolls back its entire graph and returns the winning
transaction ID with `409`. Money uses `Decimal` with two decimal places and
`NUMERIC(12, 2)` columns; rates have five decimal places.

Invoices store independent JSON snapshots and separate tax and fee rollups.
Tax rollups group by exact name/rate, keeping an absent rate distinct from zero;
fee rollups group by exact name. Invoice totals are derived from frozen amounts.
The indexed reference table records every invoice containing each transaction,
so billing protection remains correct when several invoices share a transaction.
Invoice replacement rebuilds snapshots and membership together, while deletion
preserves source transactions.

## High volume

SQLite's single writer is the first bottleneck under concurrent PATCH/DELETE,
invoice creation, and duplicate POST requests. `BEGIN IMMEDIATE` locks before
read/check/write operations, avoiding races between billing checks and writes.
The database unique constraint resolves duplicate POST races. Each operation
commits or rolls back atomically; overlapping updates use last-write-wins.
Sustained write contention can exhaust SQLite's busy timeout.

Primary keys support point lookups. The unique index on `external_id` supports
duplicate lookup, and the index on reference `transaction_id` supports bounded
"is billed" checks without scanning invoice JSON.

Both list queries order by primary-key ID and execute `LIMIT`/`OFFSET` in SQL.
The default page is 50 and requests above 100 are capped at 100. Transaction
relationships use `selectin` loading to batch children for the bounded page.
Invoice list queries disable relationship loading and return their saved
snapshots, avoiding live transaction joins and unrelated metadata queries.
No total-count scan is needed for the array responses.

Deep offsets still require skipping rows, and large individual invoice snapshots
can dominate serialization and response size. Concurrent creates/deletes can
shift offset pages. Cursor pagination and summary responses would be useful
extensions if these become the next bottlenecks; higher sustained write traffic
would call for a database with concurrent writers.
