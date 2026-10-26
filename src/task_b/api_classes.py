"""Nested ID-free input/output schemas, preserving the four metadata field names."""
from decimal import Decimal
from typing import Self

from pydantic import field_validator, model_validator
from sqlmodel import Field, Session, SQLModel, col, select
from sqlmodel._compat import SQLModelConfig

from .base_classes import (
    TaxLineBase, FeeLineBase, FlightCouponBase, FlightMetadataBase,
    HotelAddressBase, HotelExtraBase, HotelMetadataBase, RailLegBase,
    RailMetadataBase, NavanFeeMetadataBase, TransactionBase, InvoiceBase,
)

from .db_classes import (
    TransactionDB, TaxLineDB, FeeLineDB, FlightMetadataDB, FlightCouponDB,
    HotelMetadataDB, HotelAddressDB, HotelExtraDB, RailMetadataDB,
    RailLegDB, NavanFeeMetadataDB, InvoiceDB,
)


class APIConfig:
    model_config = SQLModelConfig(from_attributes=True, populate_by_name=True)


class TaxLineAPI(TaxLineBase):
    pass


class FeeLineAPI(FeeLineBase):
    pass


class FlightCouponAPI(FlightCouponBase):
    pass


class FlightMetadataAPI(FlightMetadataBase):
    coupons: list[FlightCouponAPI]


class HotelAddressAPI(HotelAddressBase):
    pass


class HotelExtraAPI(HotelExtraBase):
    pass


class HotelMetadataAPI(HotelMetadataBase):
    address: HotelAddressAPI
    extras: list[HotelExtraAPI] = Field(default_factory=list)


class RailLegAPI(RailLegBase):
    pass


class RailMetadataAPI(RailMetadataBase):
    legs: list[RailLegAPI] = Field(min_length=1)


class NavanFeeMetadataAPI(NavanFeeMetadataBase):
    pass


TransactionMetadataAPI = FlightMetadataAPI | HotelMetadataAPI | RailMetadataAPI | NavanFeeMetadataAPI
METADATA_API_MODELS = {
    "flight": FlightMetadataAPI,
    "hotel": HotelMetadataAPI,
    "rail": RailMetadataAPI,
    "navan_fee": NavanFeeMetadataAPI,
}


class TransactionAPI(TransactionBase, table=False):
    model_config = SQLModelConfig(from_attributes=True, populate_by_name=True)

    tax_lines: list[TaxLineAPI]
    fee_lines: list[FeeLineAPI]
    flight_metadata: FlightMetadataAPI | None = None
    hotel_metadata: HotelMetadataAPI | None = None
    rail_metadata: RailMetadataAPI | None = None
    navan_fee_metadata: NavanFeeMetadataAPI | None = None

    @model_validator(mode="after")
    def validate_transaction_metadata(self):
        required_field = f"{self.type}_metadata"
        if getattr(self, required_field) is None:
            raise ValueError(f"{required_field} is required when type is '{self.type}'")
        for field_name in (
            "flight_metadata", "hotel_metadata", "rail_metadata", "navan_fee_metadata"
        ):
            if field_name != required_field and getattr(self, field_name) is not None:
                raise ValueError(f"{field_name} must be None when type is '{self.type}'")
        return self

    @classmethod
    def from_db(cls, db: TransactionDB) -> Self:
        """Validate a loaded ORM graph into an ID-free nested API schema."""
        return cls.model_validate(db, from_attributes=True)

    def to_db(self) -> TransactionDB:
        """Create a fresh, unsaved ORM graph from this validated API input."""
        data = self.model_dump(exclude={
            "tax_lines", "fee_lines", "flight_metadata", "hotel_metadata",
            "rail_metadata", "navan_fee_metadata",
        })
        db = TransactionDB(**data)
        db.tax_lines = [TaxLineDB(**x.model_dump()) for x in self.tax_lines]
        db.fee_lines = [FeeLineDB(**x.model_dump()) for x in self.fee_lines]
        if self.flight_metadata is not None:
            m = self.flight_metadata
            db.flight_metadata = FlightMetadataDB(
                **m.model_dump(exclude={"coupons"}),
                coupons=[FlightCouponDB(**c.model_dump()) for c in m.coupons],
            )
        if self.hotel_metadata is not None:
            m = self.hotel_metadata
            db.hotel_metadata = HotelMetadataDB(
                **m.model_dump(exclude={"address", "extras"}),
                address=HotelAddressDB(**m.address.model_dump()),
                extras=[HotelExtraDB(**e.model_dump()) for e in m.extras],
            )
        if self.rail_metadata is not None:
            m = self.rail_metadata
            db.rail_metadata = RailMetadataDB(
                legs=[RailLegDB(**leg.model_dump()) for leg in m.legs],
            ) # type: ignore
        if self.navan_fee_metadata is not None:
            db.navan_fee_metadata = NavanFeeMetadataDB(**self.navan_fee_metadata.model_dump())
        return db


class InvoiceTransactionAPI(TransactionAPI):
    """A frozen transaction, with its original database identity."""

    transaction_id: int = Field(gt=0)


class InvoiceCreateAPI(SQLModel):
    """Invoice creation or replacement input, containing only transaction IDs."""

    model_config = SQLModelConfig(extra="forbid")

    transaction_ids: list[int] = Field(min_length=1)

    @field_validator("transaction_ids")
    @classmethod
    def validate_transaction_ids(cls, value: list[int]) -> list[int]:
        if any(transaction_id <= 0 for transaction_id in value):
            raise ValueError("transaction_ids must contain positive integers")
        # Keep request order while counting each transaction only once.
        return list(dict.fromkeys(value))

    def to_db(self, session: Session) -> InvoiceDB:
        """Build an unsaved invoice snapshot from existing stored transactions."""
        stored = session.exec(
            select(TransactionDB).where(col(TransactionDB.id).in_(self.transaction_ids))
        ).all()
        by_id = {transaction.id: transaction for transaction in stored}
        missing = [transaction_id for transaction_id in self.transaction_ids if transaction_id not in by_id]
        if missing:
            raise LookupError(f"Transactions not found: {missing}")
        transactions = [by_id[transaction_id] for transaction_id in self.transaction_ids]
        currencies = {transaction.currency for transaction in transactions}
        if len(currencies) != 1:
            raise ValueError("All invoice transactions must use the same currency")

        snapshots = [
            InvoiceTransactionAPI(
                transaction_id=transaction_id, **TransactionAPI.from_db(by_id[transaction_id]).model_dump()
            )
            for transaction_id in self.transaction_ids
        ]
        taxes: dict[tuple[str, Decimal | None], Decimal] = {}
        fees: dict[str, Decimal] = {}
        for transaction in snapshots:
            for tax in transaction.tax_lines:
                key = (tax.name, tax.rate)
                taxes[key] = taxes.get(key, Decimal("0.00")) + tax.amount
            for fee in transaction.fee_lines:
                fees[fee.name] = fees.get(fee.name, Decimal("0.00")) + fee.amount

        invoice = InvoiceAPI(
            currency=transactions[0].currency,
            total=sum((transaction.total for transaction in snapshots), Decimal("0.00")),
            transaction_ids=list(self.transaction_ids),
            transactions=snapshots,
            tax_rollup=[
                TaxLineAPI(name=name, rate=rate, amount=amount)
                for (name, rate), amount in taxes.items()
            ],
            fee_rollup=[FeeLineAPI(name=name, amount=amount) for name, amount in fees.items()],
        )
        return InvoiceDB(
            currency=invoice.currency,
            total=invoice.total,
            transaction_snapshots=[snapshot.model_dump(mode="json") for snapshot in invoice.transactions],
            tax_rollup=[tax.model_dump(mode="json") for tax in invoice.tax_rollup],
            fee_rollup=[fee.model_dump(mode="json") for fee in invoice.fee_rollup],
            transactions=transactions,
        )


class InvoiceAPI(InvoiceBase):
    """Invoice response with frozen transactions, totals, and rollups."""

    id: int | None = None
    transaction_ids: list[int] = Field(min_length=1)
    transactions: list[InvoiceTransactionAPI] = Field(min_length=1)
    tax_rollup: list[TaxLineAPI]
    fee_rollup: list[FeeLineAPI]

    @classmethod
    def from_db(cls, db: InvoiceDB) -> Self:
        """Read the stored snapshot, independently of live transaction changes."""
        return cls.model_validate({
            "id": db.id,
            "currency": db.currency,
            "total": db.total,
            "transaction_ids": [snapshot["transaction_id"] for snapshot in db.transaction_snapshots],
            "transactions": db.transaction_snapshots,
            "tax_rollup": db.tax_rollup,
            "fee_rollup": db.fee_rollup,
        })
