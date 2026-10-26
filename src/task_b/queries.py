from sqlmodel import Session, select

from .api_classes import TransactionAPI
from .db_classes import TransactionDB


def get_transaction_api(session: Session, transaction_id: int) -> TransactionAPI | None:
    """Load nested relationships before converting to API schema."""
    stmt = select(TransactionDB).where(TransactionDB.id == transaction_id)
    db = session.exec(stmt).first()
    return TransactionAPI.from_db(db) if db is not None else None
