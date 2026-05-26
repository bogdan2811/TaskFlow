from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from extensions import engine, Base
from models import chat, message, task, user
from routes.auth import router as auth_router
from routes.chats import router as chats_router
from routes.tasks import router as tasks_router
from routes.ws import router as ws_router

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.mount('/avatar_photos', StaticFiles(directory='avatar_photos'), name='avatar_photos')
app.include_router(auth_router)
app.include_router(chats_router)
app.include_router(tasks_router)
app.include_router(ws_router)

templates = Jinja2Templates(directory='templates')


@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse('home.html', {'request': request})


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse('login.html', {'request': request})


@app.get('/register', response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse('register.html', {'request': request})


@app.get('/dashboard', response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse('dashboard.html', {'request': request})


@app.get('/canvas', response_class=HTMLResponse)
def canvas_page(request: Request):
    return templates.TemplateResponse('canvas.html', {'request': request})


@app.get('/editaccount', response_class=HTMLResponse)
def edit_account_page(request: Request):
    return templates.TemplateResponse('editaccount.html', {'request': request})
