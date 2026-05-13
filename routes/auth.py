import re
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from jose import jwt, JWTError
from passlib.context import CryptContext

from extensions import get_db
from models.user import User

router = APIRouter(prefix='/api/auth', tags=['auth'])

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')

JWT_ALGORITHM = 'HS256'
JWT_EXPIRE_MINUTES = 60

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    confirmPassword: str
    acceptTerms: bool


class LoginRequest(BaseModel):
    email: str
    password: str


class EditAccountRequest(BaseModel):
    currentPassword: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    newPassword: Optional[str] = None
    confirmNewPassword: Optional[str] = None


security = HTTPBearer()


def _jwt_secret() -> str:
    secret = os.environ.get('JWT_SECRET_KEY')
    if not secret:
        raise RuntimeError('JWT_SECRET_KEY environment variable is required')
    return secret


def _decode_token_subject(token: str) -> int:
    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
    return int(payload['sub'])


def _get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    try:
        user_id = _decode_token_subject(credentials.credentials)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail={'error': str(exc)})
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail={'error': 'Invalid or expired token'})

    user = db.query(User).filter_by(user_id=user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail={'error': 'User not found'})
    return user


def _validate_username(username: str):
    if len(username) > 20:
        return False, 'Provide a shorter username'
    if len(username) < 5 or ' ' in username:
        return False, 'Please enter a valid username'
    return True, None


def _validate_email(email: str):
    if len(email) > 254:
        return False, 'Provide a shorter email address'
    if len(email) < 5 or not _EMAIL_RE.match(email):
        return False, 'Please enter a valid email address'
    return True, None


def _validate_password(password: str):
    if len(password) < 8 or len(password) > 64:
        return False, 'Password must be between 8 and 64 characters'
    if ' ' in password:
        return False, 'Password must not contain spaces'
    if not re.search(r'[A-Z]', password):
        return False, 'Password must contain at least one uppercase letter'
    if not re.search(r'[a-z]', password):
        return False, 'Password must contain at least one lowercase letter'
    if not re.search(r'[0-9]', password):
        return False, 'Password must contain at least one digit'
    return True, None


@router.get('/health')
def health():
    return {'status': 'ok'}


@router.get('/me')
def get_me(current_user: User = Depends(_get_current_user)):
    return {'user': current_user.to_dict()}


@router.get('/users')
def get_users(db: Session = Depends(get_db), current_user: User = Depends(_get_current_user)):
    users = db.query(User).all()
    return [u.to_dict() for u in users]


@router.post('/register', status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    username_raw = body.username.strip()
    email_raw = body.email.strip().lower()
    password = body.password
    confirm_password = body.confirmPassword
    accept_terms = body.acceptTerms

    if not accept_terms:
        raise HTTPException(status_code=400, detail={'field': 'acceptTerms', 'error': 'You must accept the Terms & Conditions'})

    ok, err = _validate_username(username_raw)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'username', 'error': err})

    ok, err = _validate_email(email_raw)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'email', 'error': err})

    ok, err = _validate_password(password)
    if not ok:
        raise HTTPException(status_code=422, detail={'field': 'password', 'error': err})

    if password != confirm_password:
        raise HTTPException(status_code=422, detail={'field': 'confirmPassword', 'error': 'Passwords do not match'})

    if db.query(User).filter(func.lower(User.username) == username_raw.lower()).first():
        raise HTTPException(status_code=409, detail={'field': 'username', 'error': 'Username already exists'})

    if db.query(User).filter_by(email=email_raw).first():
        raise HTTPException(status_code=409, detail={'field': 'email', 'error': 'An account with this email already exists'})

    user = User(
        username=username_raw,
        email=email_raw,
        password_hash=pwd_context.hash(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {'message': 'Account created successfully', 'user': user.to_dict()}


@router.post('/login')
def login(body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    password = body.password

    if not email or not password:
        raise HTTPException(status_code=400, detail={'error': 'Missing fields'})

    user = db.query(User).filter_by(email=email).first()

    if not user or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail={'error': 'Invalid email or password'})

    try:
        token = jwt.encode(
            {'sub': str(user.user_id), 'exp': datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)},
            _jwt_secret(),
            algorithm=JWT_ALGORITHM,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail={'error': str(exc)})

    return {'message': 'Login successful', 'token': token, 'user': user.to_dict()}


@router.patch('/account')
def edit_account(
    body: EditAccountRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(_get_current_user),
):
    if body.newPassword or body.confirmNewPassword:
        if not body.currentPassword:
            raise HTTPException(status_code=400, detail={'field': 'currentPassword', 'error': 'Current password is required to change your password'})
        if not pwd_context.verify(body.currentPassword, current_user.password_hash):
            raise HTTPException(status_code=401, detail={'field': 'currentPassword', 'error': 'Incorrect password'})

    if body.username is not None:
        username_raw = body.username.strip()
        ok, err = _validate_username(username_raw)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'username', 'error': err})
        if db.query(User).filter(func.lower(User.username) == username_raw.lower(), User.user_id != current_user.user_id).first():
            raise HTTPException(status_code=409, detail={'field': 'username', 'error': 'Username already exists'})
        current_user.username = username_raw

    if body.email is not None:
        email_raw = body.email.strip().lower()
        ok, err = _validate_email(email_raw)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'email', 'error': err})
        if db.query(User).filter(User.email == email_raw, User.user_id != current_user.user_id).first():
            raise HTTPException(status_code=409, detail={'field': 'email', 'error': 'Email already exists'})
        current_user.email = email_raw

    if body.newPassword is not None:
        ok, err = _validate_password(body.newPassword)
        if not ok:
            raise HTTPException(status_code=422, detail={'field': 'newPassword', 'error': err})
        if body.newPassword != body.confirmNewPassword:
            raise HTTPException(status_code=422, detail={'field': 'confirmNewPassword', 'error': 'Passwords do not match'})
        current_user.password_hash = pwd_context.hash(body.newPassword)

    db.commit()
    db.refresh(current_user)

    return {'message': 'Account updated successfully', 'user': current_user.to_dict()}
