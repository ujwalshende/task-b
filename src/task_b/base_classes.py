"""Shared scalar SQLModel fields and validations (no IDs or relationships)."""
from datetime import date
from decimal import Decimal
from typing import Literal

import pycountry
from pydantic import AwareDatetime, field_validator, model_validator
from sqlalchemy import DECIMAL, String
from sqlmodel import Field, SQLModel
from sqlmodel._compat import SQLModelConfig
from sqlmodel.sql.sqltypes import UTCDateTime


class TaxLineBase(SQLModel):
    name: str = Field(min_length=1, max_length=512, sa_type=String(512))
    rate: Decimal | None = Field(default=None, max_digits=12, decimal_places=5, sa_type=DECIMAL(12, 5))
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class FeeLineBase(SQLModel):
    name: str = Field(min_length=1, max_length=512, sa_type=String(512))
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class FlightCouponBase(SQLModel):
    model_config = SQLModelConfig(populate_by_name=True)
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


class FlightMetadataBase(SQLModel):
    origin: str = Field(max_length=512, sa_type=String(512))
    destination: str = Field(max_length=512, sa_type=String(512))
    fare: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class HotelAddressBase(SQLModel):
    street: str = Field(max_length=512, sa_type=String(512))
    city: str = Field(max_length=512, sa_type=String(512))
    country: str = Field(max_length=512, sa_type=String(512))


class HotelExtraBase(SQLModel):
    name: str = Field(max_length=512, sa_type=String(512))
    amount: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))


class HotelMetadataBase(SQLModel):
    name: str = Field(max_length=512, sa_type=String(512))
    check_in: date
    check_out: date
    nights: int = Field(gt=0)
    room_rate_per_night: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))

    @model_validator(mode="after")
    def validate_stay(self):
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        expected_nights = (self.check_out - self.check_in).days
        if self.nights != expected_nights:
            raise ValueError(f"nights must equal the difference between check_in and check_out ({expected_nights} nights)")
        return self


class RailLegBase(SQLModel):
    model_config = SQLModelConfig(populate_by_name=True)
    origin: str = Field(max_length=512, sa_type=String(512))
    destination: str = Field(max_length=512, sa_type=String(512))
    departure: AwareDatetime = Field(sa_type=UTCDateTime())
    arrival: AwareDatetime = Field(sa_type=UTCDateTime())
    train_number: str | None = Field(default=None, max_length=512, sa_type=String(512))
    class_: Literal["standard", "first"] | None = Field(default=None, alias="class", sa_type=String(512))

    @model_validator(mode="after")
    def validate_rail_times(self):
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        return self


class RailMetadataBase(SQLModel):
    pass


class NavanFeeMetadataBase(SQLModel):
    fee_kind: Literal["trip_fee", "agent_call_fee"] = Field(sa_type=String(512))
    description: str | None = Field(default=None, max_length=512, sa_type=String(512))
    related_external_id: str | None = Field(default=None, max_length=512, sa_type=String(512))


class TransactionBase(SQLModel):
    type: Literal["flight", "hotel", "rail", "navan_fee"] = Field(sa_type=String(512))
    external_id: str = Field(min_length=1, max_length=512, sa_type=String(512), unique=True)
    currency: str = Field(min_length=3, max_length=3, sa_type=String(3))
    occurred_at: AwareDatetime = Field(sa_type=UTCDateTime())
    total: Decimal = Field(max_digits=12, decimal_places=2, sa_type=DECIMAL(12, 2))

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if value != value.upper() or len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be exactly 3 uppercase letters")
        if pycountry.currencies.get(alpha_3=value) is None:
            raise ValueError(f"Invalid ISO 4217 currency code: {value}")
        return value
