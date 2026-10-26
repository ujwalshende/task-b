from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlmodel import Session
from starlette.responses import Response

from .api_classes import (
    InvoiceAPI,
    InvoiceCreateAPI,
    InvoiceCurrencyConflictError,
    InvoiceTransactionsNotFoundError,
    TransactionCreateAPI,
    TransactionPatchAPI,
    TransactionResponseAPI,
)
from .database import get_session
from .queries import (
    MAX_OFFSET,
    BilledTransactionError,
    DuplicateTransactionError,
    ResourceNotFoundError,
    create_invoice,
    create_transaction,
    delete_invoice,
    delete_transaction,
    get_invoice_api,
    get_transaction_api,
    list_invoices_api,
    list_transactions_api,
    update_invoice,
    update_transaction,
)

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


@api.exception_handler(ResourceNotFoundError)
@api.exception_handler(InvoiceTransactionsNotFoundError)
async def not_found_handler(request: Request, exc: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@api.exception_handler(BilledTransactionError)
@api.exception_handler(InvoiceCurrencyConflictError)
async def billing_conflict_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@api.post("/transactions", response_model=TransactionResponseAPI, status_code=201)
def post_transaction(
    transaction: TransactionCreateAPI, session: Annotated[Session, Depends(get_session)]
) -> TransactionResponseAPI:
    return create_transaction(session, transaction)


@api.get("/transactions", response_model=list[TransactionResponseAPI])
def list_transactions(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, description="Page size; values above 100 are capped")] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> list[TransactionResponseAPI]:
    return list_transactions_api(session, limit=limit, offset=offset)


@api.get("/transactions/{transaction_id}", response_model=TransactionResponseAPI)
def read_transaction(
    transaction_id: int, session: Annotated[Session, Depends(get_session)]
) -> TransactionResponseAPI:
    transaction = get_transaction_api(session, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@api.post("/invoices", response_model=InvoiceAPI, status_code=201)
def post_invoice(
    invoice: InvoiceCreateAPI, session: Annotated[Session, Depends(get_session)]
) -> InvoiceAPI:
    return create_invoice(session, invoice)


@api.get("/invoices", response_model=list[InvoiceAPI])
def list_invoices(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, description="Page size; values above 100 are capped")] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> list[InvoiceAPI]:
    return list_invoices_api(session, limit=limit, offset=offset)


@api.get("/invoices/{invoice_id}", response_model=InvoiceAPI)
def read_invoice(
    invoice_id: int, session: Annotated[Session, Depends(get_session)]
) -> InvoiceAPI:
    invoice = get_invoice_api(session, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@api.patch("/transactions/{transaction_id}", response_model=TransactionResponseAPI)
def patch_transaction(
    transaction_id: int,
    patch: TransactionPatchAPI,
    session: Annotated[Session, Depends(get_session)],
) -> TransactionResponseAPI:
    try:
        return update_transaction(session, transaction_id, patch)
    except ValidationError as exc:
        raise RequestValidationError([
            {**error, "loc": ("body", *error["loc"])} for error in exc.errors()
        ]) from exc


@api.delete("/transactions/{transaction_id}", status_code=204)
def remove_transaction(
    transaction_id: int, session: Annotated[Session, Depends(get_session)]
) -> Response:
    delete_transaction(session, transaction_id)
    return Response(status_code=204)


@api.patch("/invoices/{invoice_id}", response_model=InvoiceAPI)
def patch_invoice(
    invoice_id: int,
    invoice: InvoiceCreateAPI,
    session: Annotated[Session, Depends(get_session)],
) -> InvoiceAPI:
    return update_invoice(session, invoice_id, invoice)


@api.delete("/invoices/{invoice_id}", status_code=204)
def remove_invoice(
    invoice_id: int, session: Annotated[Session, Depends(get_session)]
) -> Response:
    delete_invoice(session, invoice_id)
    return Response(status_code=204)


@api.get("/")
def hello():
    return {"message": "Welcome to the Task-b API"}
