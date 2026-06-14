from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Cliente
from schemas import ClienteCreate, ClienteUpdate

router = APIRouter(prefix="/clientes", tags=["clientes"])


@router.get("")
def listar_clientes(db: Session = Depends(get_db)):
    return db.query(Cliente).filter(Cliente.deleted_at.is_(None)).all()


@router.post("")
def crear_cliente(data: ClienteCreate, db: Session = Depends(get_db)):
    cliente = Cliente(nombre=data.nombre, comision_por_item=data.comision_por_item)
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return cliente


@router.put("/{cliente_id}")
def actualizar_cliente(cliente_id: int, data: ClienteUpdate, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    cambios = data.model_dump(exclude_unset=True)
    for campo, valor in cambios.items():
        setattr(cliente, campo, valor)
    db.commit()
    db.refresh(cliente)
    return cliente
