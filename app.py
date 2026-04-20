import os
from fastapi import FastAPI
from extensions import engine, Base
from routes.auth import router as auth_router

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.include_router(auth_router)
