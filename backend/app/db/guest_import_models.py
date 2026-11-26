"""Idempotency keys for explicit copies of browser-local trips."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.database import Base


class GuestTripImport(Base):
    __tablename__ = "guest_trip_imports"
    __table_args__ = (UniqueConstraint("user_id", "guest_id", "guest_version", name="uq_guest_trip_import"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    guest_id: Mapped[str] = mapped_column(String(80))
    guest_version: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"))
