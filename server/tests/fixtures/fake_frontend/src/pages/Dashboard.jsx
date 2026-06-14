import { useEffect, useState } from 'react'
import { getPedidos, deletePedido } from '../api'

export default function Dashboard() {
  const [pedidos, setPedidos] = useState([])
  const [loading, setLoading] = useState(true)

  const cargar = async () => {
    try {
      const { data } = await getPedidos()
      setPedidos(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { cargar() }, [])

  const handleDelete = async (id) => {
    if (!confirm('¿Eliminar este pedido?')) return
    await deletePedido(id)
    cargar()
  }

  if (loading) return <p>Cargando...</p>

  return (
    <table>
      <tbody>
        {pedidos.map((p) => (
          <tr key={p.id}>
            <td>#{p.numero}</td>
            <td>{p.fecha}</td>
            <td><button aria-label="Eliminar pedido" onClick={() => handleDelete(p.id)}>×</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
