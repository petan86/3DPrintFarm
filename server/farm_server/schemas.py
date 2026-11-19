from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PrinterStatus = Literal[
    "offline", "idle", "queued", "heating", "leveling",
    "waiting_local_approval", "printing", "paused", "complete", "error",
]


class PrinterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    rack: Literal["A", "B", "C"] | None = None
    rack_slot: int | None = Field(default=None, ge=1, le=10)


class PrinterRegistered(BaseModel):
    id: str
    name: str
    api_key: str


class Telemetry(BaseModel):
    status: PrinterStatus
    progress: float = Field(default=0, ge=0, le=100)
    hotend_current: float | None = None
    hotend_target: float | None = None
    bed_current: float | None = None
    bed_target: float | None = None
    material: str | None = Field(default=None, max_length=64)


JobState = Literal[
    "downloading", "ready", "heating", "leveling",
    "waiting_local_approval", "printing", "paused", "complete", "error",
]


class JobStateUpdate(BaseModel):
    state: JobState
    progress: float = Field(default=0, ge=0, le=100)
    message: str | None = Field(default=None, max_length=256)


class MaterialUpdate(BaseModel):
    material: str = Field(min_length=1, max_length=64)
    color: str = Field(min_length=1, max_length=64)


class MaterialCatalogCreate(BaseModel):
    code: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=64)


class PreheatRequest(BaseModel):
    hotend: int = Field(ge=0, le=300)
    bed: int = Field(ge=0, le=130)

