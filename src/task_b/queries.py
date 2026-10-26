from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import raiseload
from sqlmodel import Session, col, select

from .api_classes import (
    FlightMetadataAPI,
    HotelMetadataAPI,
    InvoiceAPI,
    InvoiceCreateAPI,
    RailMetadataAPI,
    TransactionCreateAPI,
    TransactionPatchAPI,
    TransactionResponseAPI,
)
from .db_classes import (
    FeeLineDB,
    FlightCouponDB,
    HotelExtraDB,
    InvoiceDB,
    InvoiceTransactionReferenceDB,
    RailLegDB,
    TaxLineDB,
    TransactionDB,
)

MAX_OFFSET = 2**63 - 1


class DuplicateTransactionError(Exception):
    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__("A transaction with this external_id already exists")


class ResourceNotFoundError(LookupError):
    pass


class BilledTransactionError(ValueError):
    pass


@contextmanager
def _write_transaction(session: Session):
    try:
        # Serialize the read/check/write sequence, including concurrent invoice creation.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield
        session.commit()
    except Exception:
        session.rollback()
        raise


def _require_transaction(session: Session, transaction_id: int) -> TransactionDB:
    db = session.get(TransactionDB, transaction_id)
    if db is None:
        raise ResourceNotFoundError("Transaction not found")
    return db


def _require_invoice(session: Session, invoice_id: int) -> InvoiceDB:
    db = session.get(InvoiceDB, invoice_id)
    if db is None:
        raise ResourceNotFoundError("Invoice not found")
    return db


def _ensure_unbilled(session: Session, db: TransactionDB) -> None:
    reference = session.exec(
        select(InvoiceTransactionReferenceDB.transaction_id)
        .where(InvoiceTransactionReferenceDB.transaction_id == db.id)
        .limit(1)
    ).first()
    if reference is not None:
        raise BilledTransactionError("Transaction is already on an invoice")


def create_transaction(
    session: Session, transaction: TransactionCreateAPI
) -> TransactionResponseAPI:
    """Persist the entire transaction graph atomically; the unique key resolves races."""
    db = transaction.to_db()
    session.add(db)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(TransactionDB).where(
                TransactionDB.external_id == transaction.external_id
            )
        ).first()
        if existing is None or existing.id is None:
            raise
        raise DuplicateTransactionError(existing.id) from None
    session.refresh(db)
    return TransactionResponseAPI.from_db(db)


def get_transaction_api(
    session: Session, transaction_id: int
) -> TransactionResponseAPI | None:
    """Load nested relationships before converting to API schema."""
    stmt = select(TransactionDB).where(TransactionDB.id == transaction_id)
    db = session.exec(stmt).first()
    return TransactionResponseAPI.from_db(db) if db is not None else None


def _page_bounds(limit: int, offset: int) -> tuple[int, int]:
    if limit < 1 or not 0 <= offset <= MAX_OFFSET:
        raise ValueError("limit must be positive and offset must fit a non-negative SQLite integer")
    return min(limit, 100), offset


def list_transactions_api(
    session: Session, *, limit: int = 50, offset: int = 0
) -> list[TransactionResponseAPI]:
    limit, offset = _page_bounds(limit, offset)
    statement = select(TransactionDB).order_by(col(TransactionDB.id)).limit(limit).offset(offset)
    return [TransactionResponseAPI.from_db(db) for db in session.exec(statement).all()]


def list_invoices_api(
    session: Session, *, limit: int = 50, offset: int = 0
) -> list[InvoiceAPI]:
    limit, offset = _page_bounds(limit, offset)
    statement = (
        select(InvoiceDB)
        .options(raiseload("*"))
        .order_by(col(InvoiceDB.id))
        .limit(limit)
        .offset(offset)
    )
    return [InvoiceAPI.from_db(db) for db in session.exec(statement).all()]


def create_invoice(session: Session, invoice: InvoiceCreateAPI) -> InvoiceAPI:
    """Freeze stored transactions and attach them in one SQLite write transaction."""
    with _write_transaction(session):
        db = invoice.to_db(session)
        session.add(db)
        session.flush()
        session.refresh(db)
        response = InvoiceAPI.from_db(db)
    return response


def get_invoice_api(session: Session, invoice_id: int) -> InvoiceAPI | None:
    db = session.get(InvoiceDB, invoice_id)
    return InvoiceAPI.from_db(db) if db is not None else None


def update_transaction(
    session: Session, transaction_id: int, patch: TransactionPatchAPI
) -> TransactionResponseAPI:
    with _write_transaction(session):
        db = _require_transaction(session, transaction_id)
        _ensure_unbilled(session, db)
        data = TransactionResponseAPI.from_db(db).model_dump(exclude={"id"}, by_alias=True)
        data.update(patch.model_dump(exclude_unset=True, by_alias=True))
        validated = TransactionCreateAPI.model_validate(data)
        fields = patch.model_fields_set
        if "total" in fields:
            db.total = validated.total
        if "occurred_at" in fields:
            db.occurred_at = validated.occurred_at
        if "tax_lines" in fields:
            db.tax_lines = [TaxLineDB(**tax.model_dump()) for tax in validated.tax_lines]
        if "fee_lines" in fields:
            db.fee_lines = [FeeLineDB(**fee.model_dump()) for fee in validated.fee_lines]
        if "transaction_metadata" in fields:
            current_metadata = getattr(db, f"{db.type}_metadata")
            metadata = validated.transaction_metadata
            current_metadata.sqlmodel_update(metadata.model_dump(exclude={"coupons", "address", "extras", "legs"}))
            if isinstance(metadata, FlightMetadataAPI):
                current_metadata.coupons = [FlightCouponDB(**coupon.model_dump()) for coupon in metadata.coupons]
            elif isinstance(metadata, HotelMetadataAPI):
                current_metadata.address.sqlmodel_update(metadata.address.model_dump())
                current_metadata.extras = [HotelExtraDB(**extra.model_dump()) for extra in metadata.extras]
            elif isinstance(metadata, RailMetadataAPI):
                current_metadata.legs = [RailLegDB(**leg.model_dump()) for leg in metadata.legs]
        session.flush()
        session.refresh(db)
        response = TransactionResponseAPI.from_db(db)
    return response


def delete_transaction(session: Session, transaction_id: int) -> None:
    with _write_transaction(session):
        db = _require_transaction(session, transaction_id)
        _ensure_unbilled(session, db)
        session.delete(db)


def update_invoice(session: Session, invoice_id: int, invoice: InvoiceCreateAPI) -> InvoiceAPI:
    with _write_transaction(session):
        db = _require_invoice(session, invoice_id)
        invoice.to_db(session, existing=db)
        session.flush()
        session.refresh(db)
        response = InvoiceAPI.from_db(db)
    return response


def delete_invoice(session: Session, invoice_id: int) -> None:
    with _write_transaction(session):
        session.delete(_require_invoice(session, invoice_id))
