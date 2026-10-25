"""Normalized SQLModel tables with ORM cascade deletes and selectin eager loading.

Import this module before accessing SQLModel.metadata in Alembic's env.py.
"""
from sqlmodel import Field, Relationship, SQLModel
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from .base_classes import (
    TaxLineBase, FeeLineBase, FlightCouponBase, FlightMetadataBase,
    HotelAddressBase, HotelExtraBase, HotelMetadataBase, RailLegBase,
    RailMetadataBase, NavanFeeMetadataBase, TransactionBase,
)
from .api_classes import TransactionAPI

# SQLModel uses 'sa_relationship_kwargs', not 'sa_kwargs', for loader options.
# 'selectin' eager loading batches collections and avoids most N+1 queries.
EAGER = {"lazy": "selectin"}


class TaxLineDB(TaxLineBase, table=True):
    __tablename__ = "tax_line" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, index=True)
    transaction: "TransactionDB" = Relationship(back_populates="tax_lines", sa_relationship_kwargs=EAGER)


class FeeLineDB(FeeLineBase, table=True):
    __tablename__ = "fee_line" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, index=True)
    transaction: "TransactionDB" = Relationship(back_populates="fee_lines", sa_relationship_kwargs=EAGER)


class FlightCouponDB(FlightCouponBase, table=True):
    __tablename__ = "flight_coupon" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    flight_metadata_id: int = Field(foreign_key="flight_metadata.id", ondelete="CASCADE", nullable=False, index=True)
    flight_metadata: "FlightMetadataDB" = Relationship(back_populates="coupons", sa_relationship_kwargs=EAGER)


class FlightMetadataDB(FlightMetadataBase, table=True):
    __tablename__ = "flight_metadata" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, unique=True)
    transaction: "TransactionDB" = Relationship(back_populates="flight_metadata", sa_relationship_kwargs=EAGER)
    coupons: list[FlightCouponDB] = Relationship(back_populates="flight_metadata", cascade_delete=True, sa_relationship_kwargs=EAGER)


class HotelAddressDB(HotelAddressBase, table=True):
    __tablename__ = "hotel_address" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    hotel_metadata_id: int = Field(foreign_key="hotel_metadata.id", ondelete="CASCADE", nullable=False, unique=True)
    hotel_metadata: "HotelMetadataDB" = Relationship(back_populates="address", sa_relationship_kwargs=EAGER)


class HotelExtraDB(HotelExtraBase, table=True):
    __tablename__ = "hotel_extra" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    hotel_metadata_id: int = Field(foreign_key="hotel_metadata.id", ondelete="CASCADE", nullable=False, index=True)
    hotel_metadata: "HotelMetadataDB" = Relationship(back_populates="extras", sa_relationship_kwargs=EAGER)


class HotelMetadataDB(HotelMetadataBase, table=True):
    __tablename__ = "hotel_metadata" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, unique=True)
    transaction: "TransactionDB" = Relationship(back_populates="hotel_metadata", sa_relationship_kwargs=EAGER)
    address: HotelAddressDB = Relationship(back_populates="hotel_metadata", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    extras: list[HotelExtraDB] = Relationship(back_populates="hotel_metadata", cascade_delete=True, sa_relationship_kwargs=EAGER)


class RailLegDB(RailLegBase, table=True):
    __tablename__ = "rail_leg" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    rail_metadata_id: int = Field(foreign_key="rail_metadata.id", ondelete="CASCADE", nullable=False, index=True)
    rail_metadata: "RailMetadataDB" = Relationship(back_populates="legs", sa_relationship_kwargs=EAGER)


class RailMetadataDB(RailMetadataBase, table=True):
    __tablename__ = "rail_metadata" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, unique=True)
    transaction: "TransactionDB" = Relationship(back_populates="rail_metadata", sa_relationship_kwargs=EAGER)
    legs: list[RailLegDB] = Relationship(back_populates="rail_metadata", cascade_delete=True, sa_relationship_kwargs=EAGER)


class NavanFeeMetadataDB(NavanFeeMetadataBase, table=True):
    __tablename__ = "navan_fee_metadata" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="CASCADE", nullable=False, unique=True)
    transaction: "TransactionDB" = Relationship(back_populates="navan_fee_metadata", sa_relationship_kwargs=EAGER)


class TransactionDB(TransactionBase, table=True):
    __tablename__ = "transaction" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    tax_lines: list[TaxLineDB] = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs=EAGER)
    fee_lines: list[FeeLineDB] = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs=EAGER)
    flight_metadata: FlightMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    hotel_metadata: HotelMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    rail_metadata: RailMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    navan_fee_metadata: NavanFeeMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})

    def to_api(self) -> TransactionAPI:
        """Validate this loaded ORM graph into an ID-free nested API schema."""
        return TransactionAPI.model_validate(self, from_attributes=True)


def get_transaction_api(session: Session, transaction_id: int) -> TransactionAPI | None:
    """Load nested relationships before converting to API schema."""
    stmt = select(TransactionDB).where(TransactionDB.id == transaction_id)
    db = session.exec(stmt).first()
    return db.to_api() if db is not None else None
