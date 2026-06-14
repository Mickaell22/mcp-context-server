from datetime import date
from typing import Optional

from pydantic import BaseModel


class PedidoCreate(BaseModel):
    numero: Optional[int] = None
    fecha: date
    notas: Optional[str] = None


class PedidoUpdate(BaseModel):
    numero: Optional[int] = None
    fecha: Optional[date] = None
    notas: Optional[str] = None


class ClienteCreate(BaseModel):
    nombre: str
    comision_por_item: float = 0.0


class ClienteUpdate(BaseModel):
    nombre: Optional[str] = None
    comision_por_item: Optional[float] = None
