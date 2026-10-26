from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlmodel import Session

from .api_classes import TransactionCreateAPI, TransactionResponseAPI
from .database import get_session
from .queries import DuplicateTransactionError, create_transaction, get_transaction_api

api = FastAPI()


@api.exception_handler(DuplicateTransactionError)
async def duplicate_transaction_handler(
    request: Request, exc: DuplicateTransactionError
):
    return JSONResponse(
        status_code=409, content={"detail": str(exc), "id": exc.existing_id}
    )


@api.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    unknown_type = any(
        error["type"] == "literal_error" and error["loc"][-1] in {"type", "fee_kind"}
        for error in errors
    )
    return JSONResponse(
        status_code=400 if unknown_type else 422,
        content={"detail": jsonable_encoder(errors)},
    )


@api.post("/transactions", response_model=TransactionResponseAPI, status_code=201)
def post_transaction(
    transaction: TransactionCreateAPI, session: Annotated[Session, Depends(get_session)]
) -> TransactionResponseAPI:
    return create_transaction(session, transaction)


@api.get("/transactions/{transaction_id}", response_model=TransactionResponseAPI)
def read_transaction(
    transaction_id: int, session: Annotated[Session, Depends(get_session)]
) -> TransactionResponseAPI:
    transaction = get_transaction_api(session, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@api.get("/")
def hello():
    return {"message": "Welcome to the Task-b API"}
