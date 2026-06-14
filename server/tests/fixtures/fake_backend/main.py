from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import pedidos, clientes, auth

app = FastAPI(title="Fake Facturador Backend (fixture de tests)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pedidos.router)
app.include_router(clientes.router)
app.include_router(auth.router)
