"""Persistent SQLAlchemy models for RaktaSetu (SQLite locally, PostgreSQL in production)."""
from datetime import datetime, timezone
import os
from pathlib import Path
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


def _database_url():
    url = os.environ.get("DATABASE_URL", "sqlite:///data/blood_connect.db")
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+" not in url.split(":", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("sqlite:///") and ":memory:" not in url:
        filename = url[len("sqlite:///"):]
        Path(filename).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return url


DATABASE_URL = _database_url()
_engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

class Base(DeclarativeBase):
    pass


def utcnow():
    """Return a naive UTC timestamp for consistent SQLite/PostgreSQL storage."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Account(Base):
    __tablename__ = "rs_accounts"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False, unique=True, index=True)
    mobile = Column(String(30), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(24), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    donor_profile = relationship("DonorProfile", back_populates="account", uselist=False, cascade="all, delete-orphan")

class DonorProfile(Base):
    __tablename__ = "rs_donor_profiles"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("rs_accounts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    blood_group = Column(String(3), nullable=False, index=True)
    platelet_count = Column(Integer)
    is_nss_volunteer = Column(Boolean, nullable=False, default=False)
    location_name = Column(String(180), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    available = Column(Boolean, nullable=False, default=True)
    account = relationship("Account", back_populates="donor_profile")

class PatientRequest(Base):
    __tablename__ = "rs_patient_requests"
    id = Column(Integer, primary_key=True)
    requester_id = Column(Integer, ForeignKey("rs_accounts.id"), nullable=False, index=True)
    patient_name = Column(String(120), nullable=False)
    blood_group_needed = Column(String(3), nullable=False)
    units_needed = Column(Integer, nullable=False, default=1)
    hospital_name = Column(String(180), nullable=False)
    location_name = Column(String(180), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    urgency = Column(String(20), nullable=False, default="High")
    contact_mobile = Column(String(30), nullable=False)
    status = Column(String(24), nullable=False, default="searching", index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    alerts = relationship("Alert", back_populates="patient_request", cascade="all, delete-orphan")

class Alert(Base):
    __tablename__ = "rs_alerts"
    __table_args__ = (UniqueConstraint("patient_id", "donor_id", name="uq_rs_alert_patient_donor"),)
    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey("rs_patient_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    donor_id = Column(Integer, ForeignKey("rs_donor_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    distance_km = Column(Float, nullable=False)
    status = Column(String(24), nullable=False, default="pending", index=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    accepted_at = Column(DateTime)
    confirmed_at = Column(DateTime)
    patient_request = relationship("PatientRequest", back_populates="alerts")
    donor = relationship("DonorProfile")

class Notification(Base):
    __tablename__ = "rs_notifications"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("rs_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("rs_patient_requests.id", ondelete="SET NULL"))
    alert_id = Column(Integer, ForeignKey("rs_alerts.id", ondelete="SET NULL"))
    title = Column(String(140), nullable=False)
    body = Column(Text, nullable=False)
    kind = Column(String(30), nullable=False, default="info")
    is_read = Column(Boolean, nullable=False, default=False)
    email_status = Column(String(30), nullable=False, default="not_configured")
    created_at = Column(DateTime, nullable=False, default=utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
