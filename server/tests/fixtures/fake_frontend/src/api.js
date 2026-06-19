import axios from 'axios'

const api = axios.create({ baseURL: import.meta.env.VITE_API_URL })

export const getPedidos = () => api.get('/pedidos')

export const crearPedido = (data) => api.post('/pedidos', data)

// BUG SEMBRADO (contracts): manda notas/numero como null para limpiarlos, pero el
// backend (routers/pedidos.py PUT) los ignora con `if x is not None`. El campo nunca
// se borra. Mismatch de contrato front<->back en nullability.
export const updatePedido = (id, { numero, fecha, notas }) =>
  api.put(`/pedidos/${id}`, {
    numero: numero !== '' ? Number(numero) : null,
    fecha,
    notas: notas || null,
  })

export const deletePedido = (id) => api.delete(`/pedidos/${id}`)

export const getClientes = () => api.get('/clientes')

export const getVentas = () => api.get('/ventas')

export const getGastos = () => api.get('/gastos')
