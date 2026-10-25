from fastapi import FastAPI, Depends
from sqlmodel import Session, select

from .database import get_session


api = FastAPI()


@api.get("/")
def hello():
    return {"message": "Welcome to the Task-b API"}
