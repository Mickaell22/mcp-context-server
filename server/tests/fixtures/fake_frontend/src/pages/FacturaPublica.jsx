import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getFacturaPublica } from '../api'

export default function FacturaPublica() {
  const { token } = useParams()
  const [data, setData] = useState(null)

  useEffect(() => {
    getFacturaPublica(token).then(({ data }) => setData(data))
  }, [token])

  if (!data) return <p>Cargando...</p>

  const itemsLlegados = data.items.filter((i) => i.llegado)

  return (
    <div className="factura">
      <h1>Factura #{data.pedido_numero}</h1>
      <table>
        <tbody>
          {data.items.map((item) => (
            <tr key={item.id}>
              <td>{item.articulo}</td>
              <td>${item.precio.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="totales">
        {/* BUG SEMBRADO (correctness): el subtotal solo suma los items llegados
            (data.subtotal viene calculado asi en el backend), pero la etiqueta
            muestra data.items.length (TODOS los items). Conteo no coincide con el
            monto. La comision de abajo si usa itemsLlegados.length. */}
        <div>subtotal ({data.items.length} items) ${data.subtotal.toFixed(2)}</div>
        <div>comision ({itemsLlegados.length} llegados) ${data.comision.toFixed(2)}</div>
        <div>total ${data.total.toFixed(2)}</div>
      </div>
    </div>
  )
}
