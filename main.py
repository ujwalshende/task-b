import uvicorn
from alembic import command
from alembic.config import Config

from src.task_b.api import api


def run_migrations():
    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")


def main():
    # Run database migrations first.
    run_migrations()

    # Start FastAPI only after migrations succeed.
    uvicorn.run(
        api,
        host="127.0.0.1",
        port=8000,
    )


if __name__ == "__main__":
    main()
