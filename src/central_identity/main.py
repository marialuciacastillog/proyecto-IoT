# Etapa A: servicio de identidad (central, puerto 8008).
# - Guarda los usuarios con la contraseña cifrada (bcrypt): nunca se guarda la contraseña tal cual.
# - POST /auth/login entrega un token firmado que dura TOKEN_HOURS horas.
# - Solo el Administrador Global crea usuarios y les da sus credenciales.
# - Después de 5 intentos fallidos la cuenta se bloquea 5 minutos.
import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
from auth import currentUser, requireRoles
from roles import ADMIN_GLOBAL, ALL_ROLES, TOKEN_ISSUER, WAREHOUSE_ROLES

databaseUrl = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5440/central_db")
privateKeyPath = os.environ.get("JWT_PRIVATE_KEY_PATH", "/secrets/jwt_private.pem")
tokenHours = int(os.environ.get("TOKEN_HOURS", "2"))
maxFailedAttempts = 5
lockMinutes = 5
minPasswordLength = 8

app = FastAPI(title="Identidad: usuarios, login y roles (Etapa A)")

createTablesSql = """
    CREATE TABLE IF NOT EXISTS users (
        id              SERIAL PRIMARY KEY,
        username        VARCHAR(50) UNIQUE NOT NULL,
        password_hash   VARCHAR(100) NOT NULL,
        full_name       VARCHAR(120) NOT NULL,
        role            VARCHAR(20) NOT NULL,
        warehouse_id    VARCHAR(20),
        active          BOOLEAN NOT NULL DEFAULT TRUE,
        failed_attempts INTEGER NOT NULL DEFAULT 0,
        locked_until    TIMESTAMPTZ,
        last_login_at   TIMESTAMPTZ,
        created_by      VARCHAR(50),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
"""

publicColumns = "id, username, full_name, role, warehouse_id, active, last_login_at, created_by, created_at, updated_at"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str
    full_name: str = Field(..., min_length=3, max_length=120)
    role: str
    warehouse_id: Optional[str] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    warehouse_id: Optional[str] = None
    active: Optional[bool] = None


class PasswordReset(BaseModel):
    new_password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def logJson(level, event, fields):
    line = {"timestamp": datetime.now(timezone.utc).isoformat(), "service": "identity", "level": level, "event": event}
    line.update(fields)
    print(json.dumps(line, ensure_ascii=False), flush=True)


def connectDatabase():
    connection = None
    while connection is None:
        try:
            connection = psycopg2.connect(databaseUrl)
        except Exception as error:
            logJson("ERROR", "database_unavailable", {"error": str(error)})
            time.sleep(3)
    return connection


def hashPassword(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def checkPassword(password, passwordHash):
    return bcrypt.checkpw(password.encode(), passwordHash.encode())


# Reglas de contraseña: mínimo 8 caracteres, con al menos una letra y un número
def validatePassword(password):
    if len(password) < minPasswordLength or not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        raise HTTPException(status_code=400, detail="La contraseña debe tener mínimo 8 caracteres, con letras y números")


# Los roles de bodega necesitan su bodega; los globales no deben tener una
def validateRoleAndWarehouse(role, warehouseId):
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail="Rol inválido. Opciones: " + ", ".join(ALL_ROLES))
    if role in WAREHOUSE_ROLES and not warehouseId:
        raise HTTPException(status_code=400, detail="El rol " + role + " necesita warehouse_id (por ejemplo wh-001)")
    if role in WAREHOUSE_ROLES and not re.fullmatch(r"wh-\d{3}", warehouseId):
        raise HTTPException(status_code=400, detail="warehouse_id inválido. Formato: wh-001")
    if role not in WAREHOUSE_ROLES and warehouseId:
        raise HTTPException(status_code=400, detail="El rol " + role + " no va asociado a una bodega")


def buildToken(user):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": user["username"],
        "role": user["role"],
        "warehouse_id": user["warehouse_id"],
        "name": user["full_name"],
        "iss": TOKEN_ISSUER,
        "iat": now,
        "exp": now + timedelta(hours=tokenHours),
        "jti": uuid.uuid4().hex
    }
    with open(privateKeyPath) as keyFile:
        token = jwt.encode(claims, keyFile.read(), algorithm="RS256")
    return token


# Al arrancar: crea la tabla y, si no hay ningún usuario, crea el primer Administrador Global
@app.on_event("startup")
def startup():
    connection = connectDatabase()
    with connection.cursor() as cursor:
        cursor.execute(createTablesSql)
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            username = os.environ.get("ADMIN_USERNAME", "admin")
            password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
            if password == "":
                logJson("ERROR", "bootstrap_skipped", {"message": "Falta ADMIN_INITIAL_PASSWORD, no se creó el admin"})
            else:
                cursor.execute("""INSERT INTO users (username, password_hash, full_name, role, created_by)
                                  VALUES (%s, %s, %s, %s, 'sistema')""",
                               (username, hashPassword(password), "Administrador Global", ADMIN_GLOBAL))
                logJson("INFO", "bootstrap_admin_created", {"username": username})
    connection.commit()
    connection.close()


@app.get("/health")
def health():
    connection = connectDatabase()
    connection.close()
    return {"status": "ok"}


@app.post("/auth/login")
def login(request: LoginRequest):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM users WHERE username = %s FOR UPDATE", (request.username,))
            user = cursor.fetchone()
            problem = ""
            if user is None or not user["active"]:
                problem = "Usuario o contraseña incorrectos"
            elif user["locked_until"] is not None and user["locked_until"] > datetime.now(timezone.utc):
                problem = "Cuenta bloqueada por intentos fallidos. Intenta más tarde"
            elif not checkPassword(request.password, user["password_hash"]):
                problem = "Usuario o contraseña incorrectos"
                attempts = user["failed_attempts"] + 1
                lockedUntil = None
                if attempts >= maxFailedAttempts:
                    lockedUntil = datetime.now(timezone.utc) + timedelta(minutes=lockMinutes)
                    attempts = 0
                cursor.execute("UPDATE users SET failed_attempts = %s, locked_until = %s WHERE id = %s",
                               (attempts, lockedUntil, user["id"]))
            if problem == "":
                cursor.execute("""UPDATE users SET failed_attempts = 0, locked_until = NULL,
                                  last_login_at = clock_timestamp() WHERE id = %s""", (user["id"],))
        connection.commit()
    finally:
        connection.close()
    if problem != "":
        logJson("WARNING", "login_failed", {"username": request.username, "reason": problem})
        raise HTTPException(status_code=401, detail=problem)
    logJson("INFO", "login_ok", {"username": user["username"], "role": user["role"]})
    return {
        "access_token": buildToken(user),
        "token_type": "bearer",
        "expires_in_seconds": tokenHours * 3600,
        "username": user["username"],
        "role": user["role"],
        "warehouse_id": user["warehouse_id"]
    }


@app.get("/auth/me")
def me(user=Depends(currentUser)):
    return user


@app.post("/auth/change-password")
def changePassword(request: PasswordChange, user=Depends(currentUser)):
    validatePassword(request.new_password)
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT id, password_hash FROM users WHERE username = %s", (user["username"],))
            row = cursor.fetchone()
            if row is None or not checkPassword(request.current_password, row["password_hash"]):
                raise HTTPException(status_code=400, detail="La contraseña actual no es correcta")
            cursor.execute("UPDATE users SET password_hash = %s, updated_at = clock_timestamp() WHERE id = %s",
                           (hashPassword(request.new_password), row["id"]))
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "password_changed", {"username": user["username"]})
    return {"message": "Contraseña actualizada"}


# ----- Gestión de usuarios: solo el Administrador Global -----

@app.post("/users", status_code=201)
def createUser(request: UserCreate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    warehouseId = request.warehouse_id.lower() if request.warehouse_id else None
    validateRoleAndWarehouse(request.role, warehouseId)
    validatePassword(request.password)
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT 1 FROM users WHERE username = %s", (request.username,))
            if cursor.fetchone() is not None:
                raise HTTPException(status_code=409, detail="Ya existe un usuario con ese username")
            cursor.execute("INSERT INTO users (username, password_hash, full_name, role, warehouse_id, created_by) "
                           "VALUES (%s, %s, %s, %s, %s, %s) RETURNING " + publicColumns,
                           (request.username, hashPassword(request.password), request.full_name,
                            request.role, warehouseId, admin["username"]))
            created = cursor.fetchone()
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "user_created", {"username": request.username, "role": request.role,
                                     "warehouse_id": warehouseId, "created_by": admin["username"]})
    return created


@app.get("/users")
def listUsers(role: Optional[str] = None, warehouse_id: Optional[str] = None, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT " + publicColumns + " FROM users WHERE (%s IS NULL OR role = %s) "
                           "AND (%s IS NULL OR warehouse_id = %s) ORDER BY id",
                           (role, role, warehouse_id, warehouse_id))
            users = cursor.fetchall()
    finally:
        connection.close()
    return users


@app.patch("/users/{user_id}")
def updateUser(user_id: int, request: UserUpdate, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    connection = connectDatabase()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,))
            user = cursor.fetchone()
            if user is None:
                raise HTTPException(status_code=404, detail="Usuario no encontrado")
            if user["username"] == admin["username"] and (request.active is False or (request.role and request.role != ADMIN_GLOBAL)):
                raise HTTPException(status_code=400, detail="No puedes desactivarte ni quitarte el rol a ti mismo")
            role = request.role or user["role"]
            warehouseId = user["warehouse_id"]
            if request.role is not None or request.warehouse_id is not None:
                warehouseId = request.warehouse_id.lower() if request.warehouse_id else None
                validateRoleAndWarehouse(role, warehouseId)
            fullName = request.full_name or user["full_name"]
            active = user["active"] if request.active is None else request.active
            cursor.execute("UPDATE users SET full_name = %s, role = %s, warehouse_id = %s, active = %s, "
                           "updated_at = clock_timestamp() WHERE id = %s RETURNING " + publicColumns,
                           (fullName, role, warehouseId, active, user_id))
            updated = cursor.fetchone()
        connection.commit()
    finally:
        connection.close()
    logJson("INFO", "user_updated", {"user_id": user_id, "by": admin["username"], "role": role, "active": active})
    return updated


@app.post("/users/{user_id}/reset-password")
def resetPassword(user_id: int, request: PasswordReset, admin=Depends(requireRoles(ADMIN_GLOBAL))):
    validatePassword(request.new_password)
    connection = connectDatabase()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE users SET password_hash = %s, failed_attempts = 0, locked_until = NULL, "
                           "updated_at = clock_timestamp() WHERE id = %s", (hashPassword(request.new_password), user_id))
            found = cursor.rowcount == 1
        connection.commit()
    finally:
        connection.close()
    if not found:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    logJson("INFO", "password_reset", {"user_id": user_id, "by": admin["username"]})
    return {"message": "Contraseña restablecida"}
