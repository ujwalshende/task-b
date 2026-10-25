"""Nested ID-free input/output schemas, preserving the four metadata field names."""
from typing import TYPE_CHECKING

from pydantic import model_validator
from sqlmodel import Field
from sqlmodel._compat import SQLModelConfig

from .base_classes import (
    TaxLineBase, FeeLineBase, FlightCouponBase, FlightMetadataBase,
    HotelAddressBase, HotelExtraBase, HotelMetadataBase, RailLegBase,
    RailMetadataBase, NavanFeeMetadataBase, TransactionBase,
)

if TYPE_CHECKING:
    from .db_classes import TransactionDB


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

    def to_db(self) -> "TransactionDB":
        """Create a fresh, unsaved ORM graph from this validated API input."""
        from .db_classes import (
            TransactionDB, TaxLineDB, FeeLineDB, FlightMetadataDB, FlightCouponDB,
            HotelMetadataDB, HotelAddressDB, HotelExtraDB, RailMetadataDB,
            RailLegDB, NavanFeeMetadataDB,
        )

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
