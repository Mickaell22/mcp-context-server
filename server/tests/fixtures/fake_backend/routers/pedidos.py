from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Pedido
from schemas import PedidoCreate, PedidoUpdate

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


@router.post("")
def crear_pedido(data: PedidoCreate, db: Session = Depends(get_db)):
    pedido = Pedido(numero=data.numero, fecha=data.fecha, notas=data.notas)
    db.add(pedido)
    db.commit()
    db.refresh(pedido)
    return pedido


@router.put("/{pedido_id}")
def actualizar_pedido(pedido_id: int, data: PedidoUpdate, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    # BUG SEMBRADO (correctness): el guard `is not None` impide LIMPIAR campos.
    # Cuando el frontend manda notas=null para borrar la nota, este if la ignora
    # y la nota nunca se puede vaciar. Pasa igual con numero.
    if data.numero is not None:
        pedido.numero = data.numero
    if data.fecha is not None:
        pedido.fecha = data.fecha
    if data.notas is not None:
        pedido.notas = data.notas

    db.commit()
    db.refresh(pedido)
    return pedido


@router.delete("/{pedido_id}")
def eliminar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    db.delete(pedido)
    db.commit()
