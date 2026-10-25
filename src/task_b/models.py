from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pycountry
from pydantic import AwareDatetime, ValidationInfo, field_validator, model_validator
from sqlmodel import Field, SQLModel

# ---------- Reusable line models ----------


class TaxLine(SQLModel):
    name: str = Field(min_length=1)
    rate: Decimal | None = None
    amount: Decimal


class FeeLine(SQLModel):
    name: str = Field(min_length=1)
    amount: Decimal


# ---------- Metadata models ----------


class FlightCoupon(SQLModel):
    carrier: str
    flight_number: str
    from_: str = Field(alias="from")
    to: str
    departure: AwareDatetime
    arrival: AwareDatetime

    @model_validator(mode="after")
    def validate_flight_times(self):
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class FlightMetadata(SQLModel):
    origin: str
    destination: str
    fare: Decimal
    coupons: list[FlightCoupon]


class HotelAddress(SQLModel):
    street: str
    city: str
    country: str


class HotelExtra(SQLModel):
    name: str
    amount: Decimal


class HotelMetadata(SQLModel):
    name: str
    address: HotelAddress
    check_in: date
    check_out: date
    nights: int = Field(gt=0)
    room_rate_per_night: Decimal
    extras: list[HotelExtra]

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
    origin: str
    destination: str
    departure: AwareDatetime
    arrival: AwareDatetime
    train_number: str
    class_: Literal["standard", "first"] = Field(alias="class")

    @model_validator(mode="after")
    def validate_rail_times(self):
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class RailMetadata(SQLModel):
    legs: list[RailLeg] = Field(min_length=1)


class NavanFeeMetadata(SQLModel):
    fee_kind: Literal["trip_fee", "agent_call_fee"]
    description: str
    related_external_id: str


# ---------- Metadata types ----------

TransactionMetadata = FlightMetadata | HotelMetadata | RailMetadata | NavanFeeMetadata

METADATA_MODELS: dict[str, type[TransactionMetadata]] = {
    "flight": FlightMetadata,
    "hotel": HotelMetadata,
    "rail": RailMetadata,
    "navan_fee": NavanFeeMetadata,
}


# ---------- Main transaction model ----------


class Transactions(SQLModel):
    type: Literal["flight", "hotel", "rail", "navan_fee"]
    external_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    occurred_at: AwareDatetime
    total: Decimal

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
            raise ValueError("A valid transaction type is required to validate metadata")
        return metadata_model.model_validate(value)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Currency must be a valid ISO 4217 code")

        if value != value.upper() or len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be exactly 3 uppercase letters")

        if pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError(f"Invalid ISO 4217 currency code: {value}")

        return value
