# backend/database.py

from pymongo import MongoClient, ASCENDING
import os
from dotenv import load_dotenv
from urllib.parse import urlparse

load_dotenv()

# Obtener URI de MongoDB desde variables de entorno
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/codeaprediccion")

# Extraer nombre de base de datos de la URI si está presente
parsed_uri = urlparse(MONGODB_URI)
if parsed_uri.path and parsed_uri.path.strip("/"):
    DB_NAME = parsed_uri.path.strip("/")
else:
    DB_NAME = os.getenv("MONGO_DB_NAME", "codeaprediccion")

# Conectar a MongoDB (MongoClient maneja automáticamente la URI completa)
client = MongoClient(MONGODB_URI)
db = client[DB_NAME]

# Colecciones
users = db["users"]
projects = db["render_3d"]  # <--- ESTA ES LA NUEVA
files = db["files"]
mappings = db["mappings"]
renders = db["renders"]
assay_renders = db["assay_renders"]

users.create_index([("email", ASCENDING)], unique=True)
