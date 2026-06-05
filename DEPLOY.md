# SCI OCR — Despliegue por URL (navegador)

Guía para publicar la app en un servidor y que el equipo la use desde **cualquier dispositivo** (Windows, Mac, iPhone, Android) abriendo solo un enlace, sin instalar Python ni ejecutar `.bat`.

---

## Requisitos del servidor

- **Docker** y **Docker Compose** (V2)
- Mínimo recomendado: **2 CPU**, **4 GB RAM** (más RAM si procesan PDFs muy grandes o muchos usuarios a la vez)
- Disco: espacio para subidas temporales (`uploads` / `outputs`)

Opciones de hosting:

| Opción | Cuándo usarla |
|--------|----------------|
| **VPS** (Hetzner, DigitalOcean, Oracle, etc.) | URL fija `https://ocr.tudominio.com`, control total |
| **PC de la oficina** + [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) | Sin abrir puertos; el túnel apunta al puerto local de nginx |
| **VPS + Cloudflare Tunnel** (recomendado) | Sin PC en oficina; guía paso a paso en [deploy/CLOUDFLARE.md](CLOUDFLARE.md) |

No uses Vercel/Netlify para el backend: esta app necesita Tesseract, tiempo de CPU y archivos grandes (hasta 300 MB).

---

## 1. Instalación en el servidor

### 1.1 Clonar el proyecto

```bash
git clone https://github.com/Edwin723-rgb/ocr-app.git
cd ocr-app
```

### 1.2 Configurar variables de entorno

```bash
cp deploy/env.example .env
```

Edita `.env` en la raíz:

| Variable | Descripción |
|----------|-------------|
| `OCR_API_KEY` | **Obligatoria en producción.** Clave que cada usuario escribe una vez en la web. |
| `GEMINI_API_KEY` | Opcional. Mejora OCR difícil; requiere internet en el servidor. |
| `MAX_UPLOAD_MB` | Límite de subida (por defecto `300`). Si lo cambias, actualiza `client_max_body_size` en `deploy/nginx.conf`. |
| `OCR_HTTP_PORT` | Puerto en el host. Usa `80` en VPS; en pruebas locales `8080`. |
| `OCR_HEAVY_ASYNC_MB` | A partir de este tamaño (MB), el OCR corre en **segundo plano** con progreso por página (por defecto `15`). |
| `OCR_HEAVY_ASYNC_PAGES` | PDFs con al menos esta cantidad de páginas también van en segundo plano (por defecto `35`). |
| `OCR_MAX_CONCURRENT_JOBS` | Cuántos documentos pesados se procesan a la vez (`1` recomendado en servidores modestos). |

### Contraseñas y claves (dónde ponerlas)

| Qué | Dónde | ¿Obligatorio? |
|-----|--------|----------------|
| **Clave de acceso a la web** | `OCR_API_KEY` en `.env` (raíz del proyecto / servidor) | **Sí en producción** por URL pública. El usuario la escribe una vez en la cabecera de la app (campo API Key). |
| **Clave de Google Gemini** | `GEMINI_API_KEY` en el mismo `.env` | No. Mejora OCR difícil; no es contraseña de usuario. |
| **Uso solo en tu PC** | Deja `OCR_API_KEY=` vacío en `backend/.env` | Nadie te pedirá clave en `localhost`. |

No subas el archivo `.env` a Git. En Docker, las claves viven solo en el `.env` de la raíz (no dentro de la imagen).

### 1.3 Arrancar

```bash
docker compose up -d --build
```

Comprueba que responde:

```bash
curl http://localhost:8080/health
curl http://localhost:8080/config
```

(Sustituye `8080` por el puerto que hayas puesto en `OCR_HTTP_PORT`.)

### 1.4 HTTPS (recomendado)

En un VPS con dominio:

- **Caddy** o **nginx + Certbot** delante del puerto 80, o
- **Cloudflare** (proxy naranja) apuntando a tu IP con el túnel o puerto 80.

La app funciona en HTTP para pruebas; en producción usa siempre **HTTPS**.

---

## 2. Cloudflare Tunnel (PC de oficina sin abrir puertos)

1. Instala `cloudflared` en el PC donde corre Docker.
2. Crea un túnel y asigna un hostname, por ejemplo `ocr.tuempresa.com`.
3. En el panel del túnel, servicio HTTP → `http://localhost:8080` (o el `OCR_HTTP_PORT` que uses).
4. Deja `docker compose up -d` corriendo en ese PC.

El equipo abre `https://ocr.tuempresa.com` desde cualquier red.

---

## 3. Comandos útiles (administrador)

```bash
# Ver logs
docker compose logs -f ocr

# Reiniciar tras cambiar .env
docker compose up -d --build

# Detener
docker compose down

# Actualizar código
git pull
docker compose up -d --build
```

---

## 4. Instrucciones para el equipo (uso diario)

### Acceder

1. Abre el navegador (Chrome, Safari, Edge, Firefox).
2. Entra a la URL que te dio el administrador, por ejemplo:  
   `https://ocr.tuempresa.com`
3. Si pide **API Key**, escríbela una vez (la misma que `OCR_API_KEY` en el servidor). Queda guardada en el navegador hasta que pulses **Limpiar**.

### Procesar un documento

1. Arrastra un archivo o pulsa la zona de subida.
2. Formatos: PDF, PNG, JPG, JPEG, TIFF, BMP, WEBP.
3. Respeta el **límite de MB** que muestra el badge (lo define el servidor).
4. Elige **modo OCR** e **idioma**.
5. Pulsa **Procesar documento** y espera (PDFs grandes pueden tardar varios minutos).
6. Copia el texto o descarga **.txt**, **.md**, **.pdf**, **.docx**.

### En el celular (iPhone / Android)

1. Abre la misma URL en Safari o Chrome.
2. **iPhone:** Compartir → **Añadir a pantalla de inicio**.  
   **Android:** Menú ⋮ → **Instalar aplicación** o **Añadir a pantalla de inicio**.
3. Usa el icono como acceso directo; sigue siendo la web (fácil de depurar si algo falla).

### Si algo falla

| Síntoma | Qué hacer |
|---------|-----------|
| No carga la página | Comprobar URL y VPN; avisar al admin (`docker compose logs ocr`). |
| “Error de red” | Servidor apagado o URL incorrecta. |
| “API Key inválida” | Pedir la clave correcta al admin. |
| Archivo demasiado grande | Comprimir el PDF (p. ej. ilovepdf.com) o dividir el documento. |
| Tarda mucho | Normal en PDFs escaneados largos; verás progreso por página. No cerrar la pestaña. |
| “En cola” | Hay otro documento pesado procesándose; espera tu turno. |

Para soporte técnico, el admin puede revisar:

- `GET /health` → `{"status":"ok",...}`
- `GET /config` → límites y si Gemini está activo
- Logs: `docker compose logs -f ocr`

---

## 5. Uso local en Windows (opcional)

Sigue disponible sin Docker:

1. `instalar.bat`
2. `arrancar.bat` → `http://127.0.0.1:8000/`

Eso es solo para quien trabaje en su PC; el equipo en Mac/móvil debe usar la **URL del servidor**.

---

## 6. Seguridad y privacidad

- Define siempre `OCR_API_KEY` en producción.
- Los archivos subidos se procesan en el servidor; borra volúmenes si hace falta: `docker volume rm ...` (ver `docker volume ls`).
- No subas `.env` con claves a Git (el `.env` de la raíz debe estar solo en el servidor).

---

## 7. Trabajos pesados (rendimiento)

- El límite de **300 MB** se mantiene; la mejora es **cómo** se procesa, no subir más el tope por defecto.
- Archivos **≥ 15 MB** o PDFs **≥ 35 páginas** se encolan en segundo plano: la web no se congela y muestra avance (`Página X de Y`).
- En un VPS de **4 GB RAM**, deja `OCR_MAX_CONCURRENT_JOBS=1`. Si tienes **8 GB+** y pocos usuarios, puedes probar `2`.
- `OCR_CHUNK_PAGES=8` reduce picos de memoria en PDFs muy largos (valor por defecto actual).
- Nginx ya permite esperas largas (`proxy_read_timeout 3600s`); no hace falta cambiar nada para documentos de cientos de páginas salvo que dividas el PDF por tu cuenta.

Forzar siempre segundo plano: `OCR_ASYNC_ALWAYS=1` en `.env`.  
Forzar modo clásico (una sola petición larga): añade `?async=0` a la URL de la API o sube archivos pequeños.

---

## 8. Ajustar límite de peso

1. En `.env`: `MAX_UPLOAD_MB=300` (o el valor acordado).
2. En `deploy/nginx.conf`: `client_max_body_size 300m;` (mismo número + `m`).
3. `docker compose up -d --build`

La interfaz lee el límite real desde `/config` automáticamente.
