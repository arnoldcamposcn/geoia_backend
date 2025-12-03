FROM python:3.12-slim

WORKDIR /app

# Instalar dependencias del sistema para compilar paquetes Python
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el backend
COPY backend/ ./backend/

# Exponer puerto
EXPOSE 8000

# Ejecutar FastAPI
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

