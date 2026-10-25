from fastapi import FastAPI, Depends
from sqlmodel import Session, select

from .database import get_session
from .db_models import Hero

api = FastAPI()


@api.get("/")
def hello():
    return {"message": "Welcome to the Task-b API"}


@api.post("/heroes", response_model=Hero)
def create_hero(
    hero: Hero,
    session: Session = Depends(get_session),
):
    session.add(hero)
    session.commit()
    session.refresh(hero)
    return hero


@api.get("/heroes", response_model=list[Hero])
def get_heroes(
    session: Session = Depends(get_session),
):
    return session.exec(select(Hero)).all()