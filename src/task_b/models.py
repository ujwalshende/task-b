from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field as PydanticField, model_validator, field_validator
from sqlmodel import Field, SQLModel
import pycountry


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
    type: Literal["flight"]
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
    type: Literal["hotel"]
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
    type: Literal["rail"]
    legs: list[RailLeg] = Field(min_length=1)


class NavanFeeMetadata(SQLModel):
    type: Literal["navan_fee"]
    fee_kind: Literal["trip_fee", "agent_call_fee"]
    description: str
    related_external_id: str


# ---------- Discriminated union ----------

TransactionMetadata = Annotated[
    FlightMetadata | HotelMetadata | RailMetadata | NavanFeeMetadata,
    PydanticField(discriminator="type"),
]


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

    @model_validator(mode="after")
    def validate_metadata_type(self):
        if self.type != self.transaction_metadata.type:
            raise ValueError(
                f"Transaction type '{self.type}' does not match "
                f"metadata type '{self.transaction_metadata.type}'"
            )

        return self
    
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
