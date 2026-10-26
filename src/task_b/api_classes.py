"""Transaction and invoice request, response, and snapshot schemas."""
from decimal import Decimal
from typing import Self

from pydantic import (
    AwareDatetime,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)
from sqlmodel import Field, Session, SQLModel, col, select
from sqlmodel._compat import SQLModelConfig

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
from .db_classes import (
    FeeLineDB,
    FlightCouponDB,
    FlightMetadataDB,
    HotelAddressDB,
    HotelExtraDB,
    HotelMetadataDB,
    InvoiceDB,
    InvoiceTransactionReferenceDB,
    NavanFeeMetadataDB,
    RailLegDB,
    RailMetadataDB,
    TaxLineDB,
    TransactionDB,
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


class TransactionCreateAPI(TransactionBase):
    """Public transaction input using the assignment's single metadata field."""

    model_config = SQLModelConfig(extra="forbid", populate_by_name=True)

    tax_lines: list[TaxLineAPI]
    fee_lines: list[FeeLineAPI]
    transaction_metadata: TransactionMetadataAPI = Field(alias="metadata")

    @field_validator("transaction_metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object, info: ValidationInfo) -> TransactionMetadataAPI:
        metadata_model = METADATA_API_MODELS.get(str(info.data.get("type")))
        if metadata_model is None:
            raise ValueError("A valid transaction type is required to validate metadata")
        return metadata_model.model_validate(value)

    def to_db(self) -> TransactionDB:
        """Create a fresh, unsaved ORM graph from this validated request."""
        db = TransactionDB(**self.model_dump(include=set(TransactionBase.model_fields)))
        db.tax_lines = [TaxLineDB(**tax.model_dump()) for tax in self.tax_lines]
        db.fee_lines = [FeeLineDB(**fee.model_dump()) for fee in self.fee_lines]
        metadata = self.transaction_metadata
        if isinstance(metadata, FlightMetadataAPI):
            db.flight_metadata = FlightMetadataDB(
                **metadata.model_dump(exclude={"coupons"}),
                coupons=[FlightCouponDB(**coupon.model_dump()) for coupon in metadata.coupons],
            )
        elif isinstance(metadata, HotelMetadataAPI):
            db.hotel_metadata = HotelMetadataDB(
                **metadata.model_dump(exclude={"address", "extras"}),
                address=HotelAddressDB(**metadata.address.model_dump()),
                extras=[HotelExtraDB(**extra.model_dump()) for extra in metadata.extras],
            )
        elif isinstance(metadata, RailMetadataAPI):
            db.rail_metadata = RailMetadataDB(
                legs=[RailLegDB(**leg.model_dump()) for leg in metadata.legs],
            ) # type: ignore
        else:
            db.navan_fee_metadata = NavanFeeMetadataDB(**metadata.model_dump())
        return db


class TransactionPatchAPI(SQLModel):
    """Partial transaction update; provided lists and metadata replace existing values."""

    model_config = SQLModelConfig(extra="forbid", populate_by_name=True)

    total: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    occurred_at: AwareDatetime | None = None
    tax_lines: list[TaxLineAPI] | None = None
    fee_lines: list[FeeLineAPI] | None = None
    transaction_metadata: dict[str, object] | None = Field(default=None, alias="metadata")

    @model_validator(mode="after")
    def reject_explicit_null(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class TransactionResponseAPI(TransactionCreateAPI):
    """Stored transaction, including its server-generated primary key."""

    id: int = Field(gt=0)

    @classmethod
    def from_db(cls, db: TransactionDB) -> Self:
        """Validate a stored transaction directly into the public response."""
        for kind in METADATA_API_MODELS:
            field_name = f"{kind}_metadata"
            if kind != db.type and getattr(db, field_name) is not None:
                raise ValueError(f"{field_name} must be None when type is '{db.type}'")
        data = db.model_dump(include=set(TransactionBase.model_fields))
        data["metadata"] = getattr(db, f"{db.type}_metadata")
        data["tax_lines"] = db.tax_lines
        data["fee_lines"] = db.fee_lines
        data["id"] = db.id
        return cls.model_validate(data, from_attributes=True)


class InvoiceTransactionAPI(TransactionCreateAPI):
    """A frozen transaction, with its original database identity."""

    transaction_id: int = Field(gt=0)

    @model_validator(mode="before")
    @classmethod
    def read_saved_metadata(cls, value: object) -> object:
        # Earlier snapshots stored four metadata attributes instead of one metadata field.
        if isinstance(value, dict) and "metadata" not in value and "transaction_metadata" not in value:
            data = dict(value)
            for kind in METADATA_API_MODELS:
                metadata = data.pop(f"{kind}_metadata", None)
                if kind == data.get("type"):
                    data["metadata"] = metadata
                elif metadata is not None:
                    raise ValueError(f"{kind}_metadata must be None when type is '{data.get('type')}'")
            return data
        return value

    @classmethod
    def from_db(cls, db: TransactionDB) -> Self:
        transaction = TransactionResponseAPI.from_db(db)
        return cls.model_validate({
            **transaction.model_dump(exclude={"id"}),
            "transaction_id": transaction.id,
        })


class InvoiceTransactionsNotFoundError(LookupError):
    """At least one transaction requested for an invoice does not exist."""


class InvoiceCurrencyConflictError(ValueError):
    """An invoice cannot combine transactions in different currencies."""


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

    def to_db(self, session: Session, *, existing: InvoiceDB | None = None) -> InvoiceDB:
        """Build or replace an invoice snapshot from existing stored transactions."""
        stored = session.exec(
            select(TransactionDB).where(col(TransactionDB.id).in_(self.transaction_ids))
        ).all()
        by_id = {transaction.id: transaction for transaction in stored}
        missing = [transaction_id for transaction_id in self.transaction_ids if transaction_id not in by_id]
        if missing:
            raise InvoiceTransactionsNotFoundError(f"Transactions not found: {missing}")
        transactions = [by_id[transaction_id] for transaction_id in self.transaction_ids]
        currencies = {transaction.currency for transaction in transactions}
        if len(currencies) != 1:
            raise InvoiceCurrencyConflictError("All invoice transactions must use the same currency")

        snapshots = [
            InvoiceTransactionAPI.from_db(by_id[transaction_id])
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
        transaction_snapshots = [snapshot.model_dump(mode="json", by_alias=True) for snapshot in invoice.transactions]
        tax_rollup = [tax.model_dump(mode="json") for tax in invoice.tax_rollup]
        fee_rollup = [fee.model_dump(mode="json") for fee in invoice.fee_rollup]
        if existing is None: # create a new DBObject
            db = InvoiceDB(
                currency=invoice.currency,
                total=invoice.total,
                transaction_snapshots=transaction_snapshots,
                tax_rollup=tax_rollup,
                fee_rollup=fee_rollup,
            )
        else:
            db = existing
            db.currency = invoice.currency
            db.total = invoice.total
            db.transaction_snapshots = transaction_snapshots
            db.tax_rollup = tax_rollup
            db.fee_rollup = fee_rollup
        db.transactions = transactions
        references = {reference.transaction_id: reference for reference in db.snapshot_references}
        updated_references = []
        for transaction_id in self.transaction_ids:
            reference = references.get(transaction_id)
            if reference is None:
                reference = InvoiceTransactionReferenceDB(transaction_id=transaction_id, invoice=db)
            updated_references.append(reference)
        db.snapshot_references = updated_references
        return db


class InvoiceTotalsAPI(SQLModel):
    flight: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    hotel: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    rail: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    trip_fee: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    agent_call_fee: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)
    grand_total: Decimal = Field(default=Decimal("0.00"), max_digits=12, decimal_places=2)


class InvoiceAPI(InvoiceBase):
    """Invoice response with frozen transactions, totals, and rollups."""

    id: int | None = None
    transaction_ids: list[int] = Field(min_length=1)
    transactions: list[InvoiceTransactionAPI] = Field(min_length=1)
    tax_rollup: list[TaxLineAPI]
    fee_rollup: list[FeeLineAPI]

    @computed_field
    @property
    def totals(self) -> InvoiceTotalsAPI:
        """Build the fixture's breakdown from frozen transaction amounts."""
        amounts = dict.fromkeys(InvoiceTotalsAPI.model_fields, Decimal("0.00"))
        for transaction in self.transactions:
            metadata = transaction.transaction_metadata
            kind = metadata.fee_kind if isinstance(metadata, NavanFeeMetadataAPI) else transaction.type
            amounts[kind] += transaction.total
            amounts["grand_total"] += transaction.total
        return InvoiceTotalsAPI.model_validate(amounts)

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
