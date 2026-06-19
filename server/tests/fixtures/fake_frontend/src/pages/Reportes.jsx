import { useEffect, useState } from 'react'
import { getVentas, getGastos } from '../api'

export default function Reportes() {
  const [ventas, setVentas] = useState([])
  const [gastos, setGastos] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    async function cargar() {
      // BUG SEMBRADO (performance): waterfall — dos requests independientes en
      // serie. getGastos no depende de getVentas; deberian ir en Promise.all
      // para no encadenar latencias.
      const v = await getVentas()
      const g = await getGastos()
      setVentas(v.data)
      setGastos(g.data)
      setLoading(false)
    }
    cargar()
  }, [])

  // BUG SEMBRADO (performance): componente Fila definido DENTRO de Reportes.
  // Se recrea en cada render del padre y React remonta todo su subarbol en vez
  // de actualizarlo. Deberia vivir a nivel de modulo.
  function Fila({ row }) {
    return <tr><td>{row.fecha}</td><td>${row.monto}</td></tr>
  }

  if (loading) return <p>Cargando...</p>

  return (
    <div>
      {/* must_not_flag (performance): handler inline trivial. NO amerita
          useCallback — memoizar esto seria sobre-optimizar. */}
      <button onClick={() => setLoading(true)}>refrescar</button>
      <p>{ventas.length} ventas, {gastos.length} gastos</p>
      <table>
        <tbody>
          {ventas.map((row) => <Fila key={row.id} row={row} />)}
        </tbody>
      </table>
    </div>
  )
}
