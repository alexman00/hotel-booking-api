from datetime import date
from typing import Optional
import os

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Column, Date, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hotel_booking.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Reservation(Base):
    __tablename__ = "reservations"

    id = Column(Integer, primary_key=True, index=True)
    guest_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    room_number = Column(Integer, nullable=False)
    check_in = Column(Date, nullable=False)
    check_out = Column(Date, nullable=False)
    guests_count = Column(Integer, nullable=False)
    status = Column(String, nullable=False)


Base.metadata.create_all(bind=engine)


class ReservationCreate(BaseModel):
    guest_name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    room_number: int = Field(..., ge=1, le=9999)
    check_in: date
    check_out: date
    guests_count: int = Field(..., ge=1, le=10)
    status: str = Field(..., pattern="^(confirmed|cancelled|pending)$")


class ReservationUpdate(BaseModel):
    guest_name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    room_number: Optional[int] = Field(default=None, ge=1, le=9999)
    check_in: Optional[date] = None
    check_out: Optional[date] = None
    guests_count: Optional[int] = Field(default=None, ge=1, le=10)
    status: Optional[str] = Field(default=None, pattern="^(confirmed|cancelled|pending)$")


def validate_dates(check_in: date, check_out: date):
    if check_out <= check_in:
        raise HTTPException(
            status_code=400,
            detail="check_out must be after check_in"
        )


def room_is_double_booked(
    db,
    room_number: int,
    check_in: date,
    check_out: date,
    exclude_id: Optional[int] = None
):
    query = db.query(Reservation).filter(
        Reservation.room_number == room_number,
        Reservation.status != "cancelled",
        Reservation.check_in < check_out,
        Reservation.check_out > check_in
    )

    if exclude_id is not None:
        query = query.filter(Reservation.id != exclude_id)

    return query.first() is not None


@app.get("/")
def home():
    return {"message": "Hotel Booking API is running"}


@app.post("/reservations")
def create_reservation(reservation: ReservationCreate):
    validate_dates(reservation.check_in, reservation.check_out)

    db = SessionLocal()

    if room_is_double_booked(db, reservation.room_number, reservation.check_in, reservation.check_out):
        db.close()
        raise HTTPException(
            status_code=409,
            detail="Room already booked for that date range"
        )

    new_reservation = Reservation(
        guest_name=reservation.guest_name,
        email=reservation.email,
        room_number=reservation.room_number,
        check_in=reservation.check_in,
        check_out=reservation.check_out,
        guests_count=reservation.guests_count,
        status=reservation.status
    )

    db.add(new_reservation)
    db.commit()
    db.refresh(new_reservation)
    db.close()

    return {
        "message": "Reservation saved to database",
        "id": new_reservation.id
    }


@app.get("/reservations")
def get_all_reservations(
    guest_name: Optional[str] = Query(default=None),
    email: Optional[str] = Query(default=None),
    room_number: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    check_in_from: Optional[date] = Query(default=None),
    check_in_to: Optional[date] = Query(default=None),
    check_out_from: Optional[date] = Query(default=None),
    check_out_to: Optional[date] = Query(default=None)
):
    db = SessionLocal()
    query = db.query(Reservation)

    if guest_name:
        query = query.filter(Reservation.guest_name.ilike(f"%{guest_name}%"))
    if email:
        query = query.filter(Reservation.email.ilike(f"%{email}%"))
    if room_number is not None:
        query = query.filter(Reservation.room_number == room_number)
    if status:
        query = query.filter(Reservation.status == status)
    if check_in_from:
        query = query.filter(Reservation.check_in >= check_in_from)
    if check_in_to:
        query = query.filter(Reservation.check_in <= check_in_to)
    if check_out_from:
        query = query.filter(Reservation.check_out >= check_out_from)
    if check_out_to:
        query = query.filter(Reservation.check_out <= check_out_to)

    reservations = query.all()
    db.close()

    return reservations


@app.get("/reservations/{reservation_id}")
def get_reservation_by_id(reservation_id: int):
    db = SessionLocal()
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    db.close()

    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")

    return reservation


@app.put("/reservations/{reservation_id}")
def replace_reservation(reservation_id: int, reservation_data: ReservationCreate):
    db = SessionLocal()
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()

    if not reservation:
        db.close()
        raise HTTPException(status_code=404, detail="Reservation not found")

    validate_dates(reservation_data.check_in, reservation_data.check_out)

    if room_is_double_booked(
        db,
        reservation_data.room_number,
        reservation_data.check_in,
        reservation_data.check_out,
        exclude_id=reservation_id
    ):
        db.close()
        raise HTTPException(
            status_code=409,
            detail="Room already booked for that date range"
        )

    reservation.guest_name = reservation_data.guest_name
    reservation.email = reservation_data.email
    reservation.room_number = reservation_data.room_number
    reservation.check_in = reservation_data.check_in
    reservation.check_out = reservation_data.check_out
    reservation.guests_count = reservation_data.guests_count
    reservation.status = reservation_data.status

    db.commit()
    db.refresh(reservation)
    db.close()

    return {
        "message": "Reservation fully replaced",
        "data": reservation
    }


@app.patch("/reservations/{reservation_id}")
def patch_reservation(reservation_id: int, reservation_data: ReservationUpdate):
    db = SessionLocal()
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()

    if not reservation:
        db.close()
        raise HTTPException(status_code=404, detail="Reservation not found")

    update_data = reservation_data.model_dump(exclude_unset=True)

    new_check_in = update_data.get("check_in", reservation.check_in)
    new_check_out = update_data.get("check_out", reservation.check_out)
    new_room_number = update_data.get("room_number", reservation.room_number)
    new_status = update_data.get("status", reservation.status)

    validate_dates(new_check_in, new_check_out)

    if new_status != "cancelled" and room_is_double_booked(
        db,
        new_room_number,
        new_check_in,
        new_check_out,
        exclude_id=reservation_id
    ):
        db.close()
        raise HTTPException(
            status_code=409,
            detail="Room already booked for that date range"
        )

    for key, value in update_data.items():
        setattr(reservation, key, value)

    db.commit()
    db.refresh(reservation)
    db.close()

    return {
        "message": "Reservation partially updated",
        "data": reservation
    }


@app.post("/reservations/{reservation_id}/cancel")
def cancel_reservation(reservation_id: int):
    db = SessionLocal()
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()

    if not reservation:
        db.close()
        raise HTTPException(status_code=404, detail="Reservation not found")

    reservation.status = "cancelled"
    db.commit()
    db.refresh(reservation)
    db.close()

    return {
        "message": f"Reservation {reservation_id} cancelled successfully",
        "data": reservation
    }


@app.delete("/reservations/{reservation_id}")
def delete_reservation(reservation_id: int):
    db = SessionLocal()
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()

    if not reservation:
        db.close()
        raise HTTPException(status_code=404, detail="Reservation not found")

    db.delete(reservation)
    db.commit()
    db.close()

    return {"message": f"Reservation {reservation_id} deleted successfully"}
