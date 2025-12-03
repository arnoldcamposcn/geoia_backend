# backend/auth.py
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
from dotenv import load_dotenv

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr
from jose import JWTError, jwt
from passlib.context import CryptContext

from .database import users  # colección de Mongo

load_dotenv()

# ===== CONFIGURACIÓN JWT =====
SECRET_KEY = os.getenv("SECRET_KEY", "cambia_esto_por_un_secret_largo_y_secreto")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")) 

# ===== PASSWORD HASH =====
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    # Convertimos a bytes primero (como bcrypt lo espera)
    password_bytes = password.encode("utf-8")

    # Truncamos si excede 72 bytes
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]

    # Ahora convertimos de vuelta a str para passlib
    safe_password = password_bytes.decode("utf-8", errors="ignore")
    
    return pwd_context.hash(safe_password)

# ===== SCHEMAS =====
class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: str
    nombre_empresa: str

class UserPublic(UserBase):
    id: str
    nombre_empresa: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    email: Optional[str] = None

# ===== OAUTH2 BEARER =====
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ===== UTILS =====
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_user_by_email(email: str) -> Optional[dict]:
    return users.find_one({"email": email})

def authenticate_user(email: str, password: str) -> Optional[dict]:
    user = get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    return user

# ===== DEPENDENCIA: USUARIO ACTUAL =====
def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token inválido o expirado",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception

    user = get_user_by_email(token_data.email)
    if user is None:
        raise credentials_exception

    return UserPublic(
        id=str(user["_id"]),
        email=user["email"],
        nombre_empresa=user.get("nombre_empresa", "")
    )

# ===== SCHEMA PARA PERFIL =====
class UserProfile(BaseModel):
    nombre_empresa: str
    created_at: datetime

# ===== ROUTER DE AUTENTICACIÓN =====
router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/register", response_model=UserPublic, status_code=201)
def register(user_in: UserCreate):
    # Ver si ya existe
    if get_user_by_email(user_in.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ya existe un usuario con ese email",
        )

    doc = {
        "email": user_in.email,
        "hashed_password": get_password_hash(user_in.password),
        "nombre_empresa": user_in.nombre_empresa,
        "created_at": datetime.now(timezone.utc),
    }
    result = users.insert_one(doc)
    return UserPublic(
        id=str(result.inserted_id),
        email=user_in.email,
        nombre_empresa=user_in.nombre_empresa
    )


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # form_data.username contiene el email
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user["email"]})
    return Token(access_token=access_token)


@router.get("/profile", response_model=UserProfile)
def get_profile(current_user: UserPublic = Depends(get_current_user)):
    """Obtiene el perfil del usuario autenticado"""
    user = get_user_by_email(current_user.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado"
        )
    
    return UserProfile(
        nombre_empresa=user.get("nombre_empresa", ""),
        created_at=user.get("created_at", datetime.now(timezone.utc))
    )
