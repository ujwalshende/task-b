from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pycountry
from pydantic import AwareDatetime, ValidationInfo, field_validator, model_validator
from sqlalchemy import DECIMAL, String
from sqlmodel import Field, SQLModel
from sqlmodel.sql.sqltypes import UTCDateTime

# UTCDateTime keeps database round trips timezone-aware, including on SQLite.

# ---------- Reusable line models ----------


class TaxLine(SQLModel):
    name: str = Field(min_length=1, max_length=512, sa_type=String(512))
    rate: Decimal | None = Field(
        default=None, max_digits=12, decimal_places=5, sa_type=DECIMAL(12, 5)
    )
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class FeeLine(SQLModel):
    name: str = Field(min_length=1, max_length=512, sa_type=String(512))
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


# ---------- Metadata models ----------


class FlightCoupon(SQLModel):
    carrier: str | None = Field(default=None, max_length=512, sa_type=String(512))
    flight_number: str = Field(max_length=512, sa_type=String(512))
    from_: str = Field(alias="from", max_length=512, sa_type=String(512))
    to: str = Field(max_length=512, sa_type=String(512))
    departure: AwareDatetime = Field(sa_type=UTCDateTime())
    arrival: AwareDatetime = Field(sa_type=UTCDateTime())

    @model_validator(mode="after")
    def validate_flight_times(self):
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class FlightMetadata(SQLModel):
    origin: str = Field(max_length=512, sa_type=String(512))
    destination: str = Field(max_length=512, sa_type=String(512))
    fare: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))
    coupons: list[FlightCoupon]


class HotelAddress(SQLModel):
    street: str = Field(max_length=512, sa_type=String(512))
    city: str = Field(max_length=512, sa_type=String(512))
    country: str = Field(max_length=512, sa_type=String(512))


class HotelExtra(SQLModel):
    name: str = Field(max_length=512, sa_type=String(512))
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class HotelMetadata(SQLModel):
    name: str = Field(max_length=512, sa_type=String(512))
    address: HotelAddress
    check_in: date
    check_out: date
    nights: int = Field(gt=0)
    room_rate_per_night: Decimal = Field(
        max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2)
    )
    extras: list[HotelExtra] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stay(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")

        expected_nights = (self.check_out - self.check_in).days

        if self.nights != expected_nights:
            raise ValueError(
                f"nights must equal the difference between check_in "
                f"and check_out ({expected_nights} nights)"
            )

        return self


class RailLeg(SQLModel):
    origin: str = Field(max_length=512, sa_type=String(512))
    destination: str = Field(max_length=512, sa_type=String(512))
    departure: AwareDatetime = Field(sa_type=UTCDateTime())
    arrival: AwareDatetime = Field(sa_type=UTCDateTime())
    train_number: str | None = Field(default=None, max_length=512, sa_type=String(512))
    class_: Literal["standard", "first"] | None = Field(
        default=None, alias="class", max_length=512, sa_type=String(512)
    )

    @model_validator(mode="after")
    def validate_rail_times(self):
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class RailMetadata(SQLModel):
    legs: list[RailLeg] = Field(min_length=1)


class NavanFeeMetadata(SQLModel):
    fee_kind: Literal["trip_fee", "agent_call_fee"] = Field(
        max_length=512, sa_type=String(512)
    )
    description: str | None = Field(default=None, max_length=512, sa_type=String(512))
    related_external_id: str | None = Field(
        default=None, max_length=512, sa_type=String(512)
    )


# ---------- Metadata types ----------

TransactionMetadata = FlightMetadata | HotelMetadata | RailMetadata | NavanFeeMetadata

METADATA_MODELS: dict[str, type[TransactionMetadata]] = {
    "flight": FlightMetadata,
    "hotel": HotelMetadata,
    "rail": RailMetadata,
    "navan_fee": NavanFeeMetadata,
}


# ---------- Main transaction model ----------


class Transaction(SQLModel):
    type: Literal["flight", "hotel", "rail", "navan_fee"] = Field(
        max_length=512, sa_type=String(512)
    )
    external_id: str = Field(min_length=1, max_length=512, sa_type=String(512))
    currency: str = Field(min_length=3, max_length=3, sa_type=String(3))
    occurred_at: AwareDatetime = Field(sa_type=UTCDateTime())
    total: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))

    tax_lines: list[TaxLine]
    fee_lines: list[FeeLine]

    transaction_metadata: TransactionMetadata = Field(
        validation_alias="metadata",
        serialization_alias="metadata",
    )

    @field_validator("transaction_metadata", mode="before")
    @classmethod
    def validate_transaction_metadata(
        cls, value: Any, info: ValidationInfo
    ) -> TransactionMetadata:
        # `type` must be declared before metadata so it is validated first.
        metadata_model = METADATA_MODELS.get(str(info.data.get("type")))
        if metadata_model is None:
            raise ValueError(
                "A valid transaction type is required to validate metadata"
            )
        return metadata_model.model_validate(value)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if value != value.upper() or len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be exactly 3 uppercase letters")

        if pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError(f"Invalid ISO 4217 currency code: {value}")

        return value
