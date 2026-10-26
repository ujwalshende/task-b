from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .api_classes import TransactionCreateAPI, TransactionResponseAPI
from .db_classes import TransactionDB


class DuplicateTransactionError(Exception):
    def __init__(self, existing_id: int):
        self.existing_id = existing_id
        super().__init__("A transaction with this external_id already exists")


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
