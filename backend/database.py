# backend/database.py

from pymongo import MongoClient, ASCENDING

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "codeaprediccion"

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Colecciones
users = db["users"]
projects = db["render_3d"]  # <--- ESTA ES LA NUEVA
files = db["files"]
mappings = db["mappings"]
renders = db["renders"]
assay_renders = db["assay_renders"]

users.create_index([("email", ASCENDING)], unique=True)
