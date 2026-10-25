from sqlmodel import SQLModel, Field
from sqlalchemy import String


class Hero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(..., sa_type=String(64))
    secret_name: str = Field(..., sa_type=String(64))