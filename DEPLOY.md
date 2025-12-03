# 🚀 Guía de Deploy - GeoIA Backend

Esta guía te ayudará a desplegar el backend de GeoIA en producción usando Docker, Traefik y CI/CD con GitHub Actions.

## 📋 Requisitos Previos

- VPS con Docker y Docker Compose instalados
- Traefik configurado y corriendo con la red `proxy`
- Dominio `api.geoia.codeauni.com` apuntando al VPS
- Cuenta de GitHub con permisos para crear secrets
- GitHub Container Registry (GHCR) habilitado

---

## 🔧 Configuración en GitHub

### 1. Crear Secrets en GitHub

Ve a tu repositorio → **Settings** → **Secrets and variables** → **Actions** y crea:

- **`GHCR_TOKEN`**: Personal Access Token de GitHub con permisos `write:packages`
  - Generar en: Settings → Developer settings → Personal access tokens → Tokens (classic)
  - Scope necesario: `write:packages`
  
- **`VPS_HOST`**: IP o dominio de tu VPS (ej: `123.45.67.89` o `vps.tudominio.com`)
  
- **`VPS_KEY`**: Clave SSH privada para acceder al VPS como root
  - Generar con: `ssh-keygen -t ed25519 -C "github-actions"`
  - Agregar la clave pública al VPS: `cat ~/.ssh/id_ed25519.pub | ssh root@TU_VPS "cat >> ~/.ssh/authorized_keys"`
  - Copiar la clave privada completa (incluyendo `-----BEGIN OPENSSH PRIVATE KEY-----` y `-----END OPENSSH PRIVATE KEY-----`)

---

## 🖥️ Configuración en el VPS

### 2. Preparar el Directorio del Proyecto

```bash
# Conectarse al VPS
ssh root@TU_VPS

# Crear directorio del proyecto
mkdir -p /srv/geoia-backend
cd /srv/geoia-backend
```

### 3. Crear docker-compose.yml

Crea el archivo `/srv/geoia-backend/docker-compose.yml` con el siguiente contenido:

**⚠️ IMPORTANTE**: Reemplaza `TU_USUARIO` con tu usuario/organización de GitHub.

```yaml
version: "3.9"

services:
  api:
    image: ghcr.io/TU_USUARIO/geoia-backend:latest
    container_name: geoia_backend
    restart: always
    env_file: .env
    networks:
      - proxy
    depends_on:
      - mongo
    volumes:
      - uploads_data:/app/backend/uploads
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.geoia-api.rule=Host(`api.geoia.codeauni.com`)"
      - "traefik.http.routers.geoia-api.entrypoints=websecure"
      - "traefik.http.routers.geoia-api.tls.certresolver=letsencrypt"
      - "traefik.http.services.geoia-api.loadbalancer.server.port=8000"

  mongo:
    image: mongo:6
    container_name: geoia_mongo
    restart: always
    volumes:
      - mongo_data:/data/db
    networks:
      - proxy
    environment:
      MONGO_INITDB_DATABASE: geoia_db

networks:
  proxy:
    external: true

volumes:
  mongo_data:
  uploads_data:
```

### 4. Crear archivo .env

Crea el archivo `/srv/geoia-backend/.env` con las siguientes variables:

```bash
# MongoDB Configuration
MONGODB_URI=mongodb://geoia_mongo:27017/geoia_db

# JWT Secret Key (generar con: openssl rand -hex 32)
SECRET_KEY=TU_SECRET_KEY_GENERADA_AQUI

# Application Environment
APP_ENV=production

# CORS Origins (separados por comas)
ALLOWED_ORIGINS=https://geoia.codeauni.com,https://www.geoia.codeauni.com

# Token expiration time in minutes
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

**Generar SECRET_KEY seguro:**
```bash
openssl rand -hex 32
```

Copia el resultado y úsalo como valor de `SECRET_KEY` en el archivo `.env`.

### 5. Verificar Red de Traefik

Asegúrate de que la red `proxy` de Traefik existe:

```bash
docker network ls | grep proxy
```

Si no existe, créala:
```bash
docker network create proxy
```

---

## 🚀 Primer Deploy

### 6. Hacer Push al Repositorio

Una vez configurados los secrets en GitHub y los archivos en el VPS:

1. Haz commit y push de tus cambios a la rama `main`
2. GitHub Actions construirá automáticamente la imagen Docker
3. La imagen se publicará en GitHub Container Registry
4. El workflow desplegará automáticamente al VPS

### 7. Levantar Servicios Manualmente (Primera Vez)

Si prefieres hacer el primer deploy manualmente:

```bash
cd /srv/geoia-backend
docker-compose pull
docker-compose up -d
```

### 8. Verificar el Deploy

```bash
# Ver logs en tiempo real
docker-compose logs -f api

# Ver estado de contenedores
docker-compose ps

# Verificar que el servicio responde
curl https://api.geoia.codeauni.com/docs
```

---

## 🔍 Comandos Útiles para Mantenimiento

### Ver Logs

```bash
# Logs en tiempo real
docker-compose logs -f api

# Logs de MongoDB
docker-compose logs -f mongo

# Últimas 100 líneas
docker-compose logs --tail=100 api
```

### Reiniciar Servicios

```bash
# Reiniciar solo la API
docker-compose restart api

# Reiniciar todos los servicios
docker-compose restart

# Reconstruir y levantar
docker-compose up -d --force-recreate api
```

### Acceder a Contenedores

```bash
# Entrar al contenedor de la API
docker exec -it geoia_backend bash

# Entrar al contenedor de MongoDB
docker exec -it geoia_mongo bash

# Acceder a MongoDB shell
docker exec -it geoia_mongo mongosh geoia_db
```

### Backup de MongoDB

```bash
# Crear backup
docker exec geoia_mongo mongodump --out /data/backup/$(date +%Y%m%d_%H%M%S)

# Restaurar backup
docker exec -i geoia_mongo mongorestore /data/backup/NOMBRE_BACKUP
```

### Limpieza

```bash
# Detener y eliminar contenedores (los datos persisten en volúmenes)
docker-compose down

# Eliminar contenedores y volúmenes (⚠️ CUIDADO: Elimina datos)
docker-compose down -v

# Limpiar imágenes no utilizadas
docker system prune -a
```

---

## 🔒 Seguridad

### Variables de Entorno

- ✅ **NUNCA** commits el archivo `.env` al repositorio
- ✅ Usa `SECRET_KEY` fuerte (mínimo 32 caracteres aleatorios)
- ✅ Restringe `ALLOWED_ORIGINS` a tus dominios de producción
- ✅ Usa conexión segura a MongoDB (en producción, considera autenticación)

### Firewall

Asegúrate de que el VPS tenga el firewall configurado:

```bash
# Permitir solo puertos necesarios
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (Traefik)
ufw allow 443/tcp   # HTTPS (Traefik)
ufw enable
```

---

## 📝 Estructura de Archivos en el VPS

```
/srv/geoia-backend/
├── docker-compose.yml  # Configuración de servicios
├── .env                # Variables de entorno (NO en git)
└── (volúmenes Docker)
    ├── mongo_data/     # Datos de MongoDB
    └── uploads_data/   # Archivos subidos por usuarios
```

---

## 🐛 Troubleshooting

### El servicio no responde

1. Verifica los logs: `docker-compose logs -f api`
2. Verifica que Traefik detecta el servicio: `docker ps | grep traefik`
3. Verifica el DNS: `nslookup api.geoia.codeauni.com`
4. Verifica que la red `proxy` existe: `docker network inspect proxy`

### Error de conexión a MongoDB

1. Verifica que MongoDB está corriendo: `docker-compose ps mongo`
2. Verifica los logs: `docker-compose logs mongo`
3. Verifica la variable `MONGODB_URI` en `.env`

### Error de autenticación JWT

1. Verifica que `SECRET_KEY` está configurada en `.env`
2. Asegúrate de que es la misma en todas las instancias
3. Reinicia el servicio después de cambiar `SECRET_KEY`

### La imagen no se construye

1. Verifica los logs de GitHub Actions
2. Verifica que `GHCR_TOKEN` está configurado correctamente
3. Verifica que el Dockerfile está en la raíz del repositorio

---

## 📚 Recursos Adicionales

- [Documentación de Docker](https://docs.docker.com/)
- [Documentación de Traefik](https://doc.traefik.io/traefik/)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)

---

## ✅ Checklist de Deploy

- [ ] Secrets configurados en GitHub (GHCR_TOKEN, VPS_HOST, VPS_KEY)
- [ ] Directorio `/srv/geoia-backend` creado en el VPS
- [ ] `docker-compose.yml` creado con la imagen correcta
- [ ] `.env` creado con todas las variables necesarias
- [ ] `SECRET_KEY` generada y configurada
- [ ] Red `proxy` de Traefik existe
- [ ] Dominio `api.geoia.codeauni.com` apunta al VPS
- [ ] Primer push a `main` realizado
- [ ] Servicios levantados y funcionando
- [ ] Logs verificados sin errores
- [ ] Endpoint accesible en `https://api.geoia.codeauni.com/docs`

---

**¡Listo!** Tu backend debería estar funcionando en producción. 🎉

