from contextlib import contextmanager
import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)


is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)

logger.info("database.engine_configured dialect=%s sqlite_foreign_keys=%s", "sqlite" if is_sqlite else "other", is_sqlite)

if is_sqlite:
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        logger.debug("database.sqlite_foreign_keys_enabled")

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db() -> None:
    started = time.perf_counter()
    logger.info("database.schema_init_started")
    Base.metadata.create_all(bind=engine)

    if is_sqlite:
        with engine.begin() as connection:
            columns = connection.exec_driver_sql("PRAGMA table_info(users)").fetchall()
            if "name" not in {column[1] for column in columns}:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN name VARCHAR(100)")
                logger.info("database.sqlite_migration_added users.name")

            # Older databases predate the composite owner constraints and lack
            # the required unique parent key for sessions(id, user_id).
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_id_user "
                "ON sessions(id, user_id)"
            )
            logger.info("database.sqlite_migration_verified sessions.id_user_unique")

    logger.info("database.schema_init_completed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)


@contextmanager
def get_session():
    """Yield one unit-of-work and commit it only when the caller succeeds."""
    db = SessionLocal()
    started = time.perf_counter()
    logger.debug("database.unit_of_work_started")
    try:
        yield db
        db.commit()
        logger.debug("database.transaction_committed elapsed_ms=%.1f", (time.perf_counter() - started) * 1000)
    except BaseException as exc:
        db.rollback()
        # Log the exception class but not SQLAlchemy's exception text/parameters,
        # which can contain values from customer messages.
        logger.error("database.transaction_rolled_back error_type=%s elapsed_ms=%.1f", type(exc).__name__, (time.perf_counter() - started) * 1000)
        raise
    finally:
        db.close()
        logger.debug("database.session_closed")
