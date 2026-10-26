"""Normalized SQLModel tables with ORM cascade deletes and selectin eager loading.

Import this module before accessing SQLModel.metadata in Alembic's env.py.
"""
from typing import Any

from sqlalchemy import JSON
from sqlmodel import Field, Relationship, SQLModel

from .base_classes import (
    FeeLineBase,
    FlightCouponBase,
    FlightMetadataBase,
    HotelAddressBase,
    HotelExtraBase,
    HotelMetadataBase,
    InvoiceBase,
    NavanFeeMetadataBase,
    RailLegBase,
    RailMetadataBase,
    TaxLineBase,
    TransactionBase,
)

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


class InvoiceTransactionReferenceDB(SQLModel, table=True):
    """Associate each saved invoice with all transactions included in its snapshot."""

    __tablename__ = "invoice_transaction_reference" # type: ignore
    invoice_id: int = Field(foreign_key="invoice.id", ondelete="CASCADE", primary_key=True)
    transaction_id: int = Field(foreign_key="transaction.id", ondelete="RESTRICT", primary_key=True, index=True)


class InvoiceDB(InvoiceBase, table=True):
    __tablename__ = "invoice" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    transaction_snapshots: list[dict[str, Any]] = Field(sa_type=JSON)
    tax_rollup: list[dict[str, Any]] = Field(sa_type=JSON)
    fee_rollup: list[dict[str, Any]] = Field(sa_type=JSON)
    transactions: list["TransactionDB"] = Relationship(
        link_model=InvoiceTransactionReferenceDB, sa_relationship_kwargs=EAGER
    )


class TransactionDB(TransactionBase, table=True):
    __tablename__ = "transaction" # type: ignore
    id: int | None = Field(default=None, primary_key=True)
    # Membership is edited through InvoiceDB.transactions; this collection is for lookup.
    invoices: list[InvoiceDB] = Relationship(
        link_model=InvoiceTransactionReferenceDB, sa_relationship_kwargs={"viewonly": True}
    )
    tax_lines: list[TaxLineDB] = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs=EAGER)
    fee_lines: list[FeeLineDB] = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs=EAGER)
    flight_metadata: FlightMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    hotel_metadata: HotelMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    rail_metadata: RailMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
    navan_fee_metadata: NavanFeeMetadataDB | None = Relationship(back_populates="transaction", cascade_delete=True, sa_relationship_kwargs={"lazy": "selectin", "uselist": False, "single_parent": True})
