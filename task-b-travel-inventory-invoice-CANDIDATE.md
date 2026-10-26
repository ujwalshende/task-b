# Take-home B — Flight, hotel, rail, Navan fee transactions and invoice

**Deadline:** up to **3 days** from when you receive this assignment. You do not need to start the moment the email arrives.

**Expected effort:** about **4–8 hours** of focused work. This is not a 60-minute curl demo. We expect a small service you would defend in a code review: data model, duplicate identity, concurrent writes, invoice snapshot, tests.

**You complete only this task.** Do not build receipt OCR, auto-itemize, receipt upload, or inventory.

**Language:** Navan production code is mostly **Java**. Implement this task in **Java and Spring** (Spring Boot is fine) unless you have a strong reason not to. Another stack is not an automatic fail, but Java + Spring is what we will compare you against.

We care that the API runs, duplicates are rejected under concurrency, an invoice is a frozen snapshot, and the design is something you would ship — not a single file that does routing and storage in one class.

---

## Using an LLM (allowed)

You **may** use ChatGPT, Cursor, or any other LLM **to help you write the code**. Paste this brief and the fixtures into your local tools.

You **do not** need to call an LLM from the running service. Transactions and invoices are stored records. Do not generate invoice lines by asking a model at create or `GET` time.

If you *do* call a vendor for any optional helper:

- Put the key in an **environment variable** (or a `.env` file that is **gitignored**).
- README: how to set the variable, then run.
- **Do not commit API keys**, tokens, or credential files. A placeholder such as `YOUR_KEY` is fine. A real key in the repo is an automatic fail.

We will not ask you for your key. Scoring uses your README, tests, and the fixtures.

---

## Product context

Clients send **already-structured** Navan commerce transactions on **one** create API. Types are:

- **flight** — tickets with origin/destination, flight numbers, and **coupons** (segments)
- **hotel** — stay with property **name and address**
- **rail** — journeys with **legs** (not only a single origin/destination)
- **navan_fee** — Navan-owned charges on the trip, e.g. **trip fee**, **agent call fee** (phone support). These are first-class transactions, not a footnote on the flight.

A transaction can carry **taxes** and **fees** as their own lists (VAT vs YQ vs carrier surcharge vs breakfast extra). Type-specific details live in **`metadata`**.

**Data model (this is part of the exercise).** We want to see how you structure tables and models for:

- taxes and fees on a transaction (separate lists, not one mashed extras column)
- **rail** `legs` (a change of train is two legs)
- **hotel** property **name** and **address**
- **flight** **coupons**, origin/destination, **flight number**
- **`navan_fee` transactions** next to flight/hotel/rail: at least **trip fee** and **agent call fee** (fee for a support call)

Shared table + JSON, child tables per type, or STI are all acceptable if GET round-trips the structure and you can defend the choice in `ARCHITECTURE.md`.

Then you **create an invoice by transaction id** (one id, or several ids for a trip that might mix flight + hotel + rail + Navan fees). The invoice must be built from **what you stored**. If someone later changes a flight, an invoice that already included that flight must **not** change.

Identical transactions must **not** be saved twice — including two overlapping POSTs with the same `external_id`.

Clients will also **update and delete** transactions and invoices. An invoice that already billed a trip must stay frozen. Lists must stay cheap when volume is high.

---

## What to build

A small HTTP API backed by a **real database**. **SQLite** (a local `.db` file) is enough. Postgres, MySQL, or file-backed H2 are also fine.

**Do not** store transactions, tax lines, fee lines, or invoices in process memory (`dict`, `HashMap`, a list, or an in-memory-only engine such as `jdbc:h2:mem:`). Uniqueness of `external_id` and the invoice snapshot must live in the **database**.

No login, no UI, no OCR, no PDF renderer (JSON invoice is enough).

Split the work into seams a reviewer can follow: HTTP handlers, transaction persistence, duplicate identity, invoice snapshot/rollup. One god-class that owns all of that is not a complete solution even if the curls work.

### 1. Create a transaction (single API)

`POST /transactions`

Shared fields on every request:

| Field | Required | Meaning |
|---|---|---|
| `type` | yes | `flight` \| `hotel` \| `rail` \| `navan_fee` |
| `external_id` | yes | Client-supplied unique id (booking/PNR/reference) |
| `currency` | yes | e.g. `EUR` |
| `occurred_at` | yes | When the trip/stay/fee happened (ISO-8601) |
| `total` | yes | Gross total |
| `tax_lines` | yes | List of `{ name, rate?, amount }` — government/VAT-style; not a single header float. Empty list is allowed when there is no tax. |
| `fee_lines` | yes | List of `{ name, amount }` — carrier/merchant fees on **this** transaction (e.g. YQ-like surcharge, booking fee). Empty list allowed. Do not mash fees into `tax_lines`. |
| `metadata` | yes | Type-specific breakdown (see below) |

**`metadata` by type** (minimum — persist and return these; extra fields are fine):

| `type` | `metadata` must include |
|---|---|
| `flight` | `origin`, `destination` (IATA or city), `fare`, and **`coupons`**: each coupon has `flight_number`, `carrier` (optional), `from`, `to`, `departure`, `arrival`. Round-trips have more than one coupon. |
| `hotel` | **`name`**, **`address`** (`street`, `city`, `country` at least), `check_in`, `check_out`, `nights`, `room_rate_per_night`. Optional `extras`. |
| `rail` | **`legs`**: each leg has `origin`, `destination`, `departure`, `arrival`, optional `train_number` / `class`. A through journey with a change is two legs — not a single OD pair only. |
| `navan_fee` | **`fee_kind`**: at least `trip_fee` and `agent_call_fee` (agent fee for a support call). Optional `description`, `related_external_id` (booking the fee hangs off). |

Unknown `type` or unknown `navan_fee.fee_kind`: **400**. Do not store an empty dummy metadata object. Do not store a flight as a hotel blob.

We care that **tables and models** can hold this without one untyped `varchar metadata` that you never query. JSON column for type-specific fields is acceptable if taxes/fees/identity are real columns/tables and GET round-trips the structure.

Return the stored transaction including a server `id`.

The write of header + `tax_lines` + `fee_lines` + `metadata` must be **atomic** (all persist or none).

### Identity, concurrency, and money (required)

**Duplicates — do not insert a second row**

If a transaction with the same `external_id` already exists:

- Do **not** save another record.
- Return **409 Conflict** (or **200** with the existing resource — pick one and document it).
- Response must include the **existing** `id`.

Posting the same body twice must still result in **exactly one** stored transaction.

**Concurrent create:** two overlapping `POST /transactions` with the same `external_id` must not create two rows. Put a **unique constraint** on `external_id` and handle the conflict. A transactional upsert is fine. `id = len(map) + 1` is not. Do not add Redis, Kafka, or a lock service.

**Money:** use a decimal type or integer minor units. Document the scale. Do not treat IEEE `float` addition as good enough for invoice totals.

### 2. Read a transaction

`GET /transactions/{id}` — return stored record including `type`, `metadata`, `tax_lines`, `fee_lines`. **404** if missing.

### 3. Create an invoice by transaction id

`POST /invoices`

Body (either shape is fine; support **at least** a single id):

```json
{ "transaction_id": "txn_123" }
```

or several ids on one invoice (a trip):

```json
{ "transaction_ids": ["txn_123", "txn_456"] }
```

- Load those transactions; **404** if any id is missing.
- **Copy** type, totals, `tax_lines`, `fee_lines`, and `metadata` onto the invoice (**snapshot**). Later edits to the transaction must not change this invoice.
- Invoice-level **tax rollup** and **fee rollup** (separate lists), grouped by name/rate as you document.
- Totals = sum of included transactions. Mixed currencies: **409**.
- The same transaction id listed twice on one invoice: **409** or dedupe to one line — pick one and document it. Do not silently double-count.
- Do not generate invoice lines by asking an LLM at create or `GET` time.

`GET /invoices/{id}` — header, included transaction snapshots, **tax rollup**, **fee rollup**, totals.

### 4. Update and delete (required)

These must not corrupt money or un-freeze an invoice.

**Transactions**

| Method | Path | Behavior |
|---|---|---|
| `PATCH` | `/transactions/{id}` | Update stored fields (`total`, `tax_lines`, `fee_lines`, `metadata`, `occurred_at` as you support). **404** if missing. If this transaction is already on **any** invoice, return **409** and persist nothing (the invoice snapshot stays frozen; do not silently rewrite billed money). |
| `DELETE` | `/transactions/{id}` | **404** if missing. If the transaction is on any invoice, **409** and delete nothing. Otherwise delete the row and its tax/fee lines atomically. |

**Invoices**

| Method | Path | Behavior |
|---|---|---|
| `PATCH` | `/invoices/{id}` | Replace the set of `transaction_ids` (same body shape as create). Rebuild the **snapshot** from **current** stored transactions in one atomic write. **404** if the invoice or any id is missing. Mixed currencies: **409**, invoice unchanged. Existing snapshot must not become a live join. |
| `DELETE` | `/invoices/{id}` | **404** if missing. Delete the invoice and its snapshot lines. **Do not** delete the underlying transactions. |

Two overlapping PATCH/DELETE on the same id must not tear a row (use the DB transaction / row lock / unique keys you already have). Last-write-wins is acceptable if it is still atomic; a torn `tax_lines` or `fee_lines` list is not.

### 5. Lists under volume (required)

Assume this API will take **high create/update/list traffic** (many bookings per day, many concurrent clients). You do not need Kafka, Redis, or a load-test harness.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/transactions` | Paginated list. Require `limit` (default 50, **max 100**) and `offset` or a cursor. **Never** return the whole table unbounded. |
| `GET` | `/invoices` | Same pagination rules. |

**Database / volume (must be true in the schema and README, not only a comment):**

- Unique index on `external_id`.
- Indexes that make `GET /transactions/{id}`, `GET /invoices/{id}`, and “is this transaction on an invoice?” cheap (e.g. index on invoice-line `transaction_id`).
- List queries must use `LIMIT` (or equivalent). `SELECT * FROM transactions` with no bound is a fail.
- Do not load all rows into a list in the application to “paginate in memory.”
- In `ARCHITECTURE.md`, one short section **High volume**: what breaks first under concurrent PATCH + duplicate POST + large lists, and what you did (indexes, pagination, constraints). Do not add Prometheus/nginx as a substitute.

`GET /health` is welcome.

---

## Fixtures

Use `fixtures/task-b/seed-payloads.json` as example `POST /transactions` bodies (flight, hotel, rail, Navan fees).  
`expected-invoice.json` is the expected invoice **shape** if you invoice the trip seeds (amounts should match; server ids will differ).

Also add a curl that **POSTs the same `external_id` twice** and shows that only one row exists.

---

## Tests (required)

Automated tests are **required**. README must include **one command** that runs them (for example `./mvnw test`, `./gradlew test`, `pytest`).

Minimum coverage — we will fail the submission if these are missing:

1. **Create** flight, hotel, rail, **and** two `navan_fee` kinds (`trip_fee`, `agent_call_fee`) from the seed payloads. GET returns coupons / hotel address / rail legs / `fee_kind` — not an empty metadata object.
2. **Duplicate `external_id`:** second POST does not insert a second row; 409 or 200 with the existing `id`.
3. **Invoice by transaction id(s):** a trip invoice that includes flight + hotel + rail + both Navan fees; totals match `expected-invoice.json` (grand total `685.70` EUR).
4. **Snapshot:** after the invoice exists, change the stored flight `total`; `GET` invoice still shows flight contribution `220.00`.
5. **Mixed currency:** invoice of EUR + USD returns **409**.
6. **Concurrent duplicate create:** two overlapping POSTs with the same `external_id` still leave **one** row. A unique-constraint conflict test, a lock test, or an equivalent race test is acceptable. A comment in README is not.
7. **PATCH transaction** after it is on an invoice returns **409**; invoice snapshot totals unchanged.
8. **DELETE transaction** that is on an invoice returns **409**; row still exists.
9. **DELETE invoice** removes the invoice; the transactions still `GET`.
10. **GET `/transactions`** without an unbounded dump: default page size 50 and a request with `limit=1000` is capped or **400**.

You choose the test framework. Hitting the real HTTP API is preferred over only unit-testing JSON maps.

---

## Out of scope

Receipts, OCR, auto-itemize, inventory, auth, UI, PDF, payments, policy, live GDS/booking providers, Redis/Kafka, distributed lock managers, Prometheus/nginx.

A unique **database** constraint on `external_id`, invoice snapshot tables, update/delete 409 rules, pagination/indexes for volume, and the test list above **are in scope.**

---

## Submit

1. Git repo URL + commit SHA.
2. `README.md`: how to **run the API** in one command, how to **run tests** in one command, plus example curls for: create (each type), duplicate create, get/list transaction, patch/delete transaction (including 409 when invoiced), create invoice by id, get/list invoice, patch/delete invoice.
3. `ARCHITECTURE.md` (one page is enough): `external_id` uniqueness, concurrent POSTs, invoice snapshot, **tables and models** for taxes vs fees and for type-specific data (flight coupons / OD / flight number vs hotel name+address vs rail legs vs `navan_fee` trip/agent-call), **and High volume** (indexes, pagination, what fails first under load).

A correct, tested create API + duplicate rejection + snapshot invoice beats an unfinished “billing platform.”
