# SCI OCR en internet con Cloudflare

Guía para publicar la app con **URL fija** (`https://ocr.tudominio.com`), **sin `arrancar.bat`**, **sin PC de oficina encendida** y **solo para el equipo**.

Usarás:

1. Un **VPS** (servidor pequeño en internet) con Docker.
2. **Cloudflare Tunnel** (conexión segura; no hace falta abrir puertos en el firewall).
3. **Cloudflare Access** (opcional pero recomendado): solo entran correos autorizados.

El código de la app **no cambia**; es el mismo `docker compose` que ya tienes.

---

## Qué necesitas antes de empezar

| Requisito | Notas |
|-----------|--------|
| Cuenta en [Cloudflare](https://dash.cloudflare.com/sign-up) | Gratis |
| Un **dominio** (ej. `tudespacho.com`) | Puede costar ~€10/año; el dominio debe usar **nameservers de Cloudflare** |
| Un **VPS** Linux | Mínimo **4 GB RAM**, 2 CPU, 40 GB disco (Hetzner CX22, DigitalOcean, etc.) |
| Acceso **SSH** al VPS | Lo da el proveedor al crear el servidor |

---

## Parte 1 — VPS: instalar Docker y la app

Conéctate por SSH (ejemplo):

```bash
ssh root@TU_IP_DEL_VPS
```

### 1.1 Instalar Docker (Ubuntu 24.04)

```bash
apt-get update
apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sh
```

### 1.2 Clonar el proyecto

```bash
cd /opt
git clone https://github.com/Edwin723-rgb/ocr-app.git
cd ocr-app
```

(Si el repo es privado, usa tu URL y token de Git.)

### 1.3 Configurar `.env`

```bash
cp deploy/env.example .env
nano .env
```

Cambia al menos:

```env
OCR_HTTP_PORT=8080
OCR_API_KEY=pon-una-clave-larga-y-secreta-aqui
MAX_UPLOAD_MB=300
```

Guarda (`Ctrl+O`, `Enter`, `Ctrl+X`).

### 1.4 Probar que la app corre (solo en el VPS)

```bash
docker compose up -d --build
curl -s http://127.0.0.1:8080/health
```

Deberías ver algo como `{"status":"ok",...}`.

---

## Parte 2 — Cloudflare Tunnel

El túnel conecta Cloudflare con tu VPS **sin exponer puertos** al internet abierto.

### 2.1 Dominio en Cloudflare

1. Entra a [Cloudflare Dashboard](https://dash.cloudflare.com).
2. **Add a site** → tu dominio → plan **Free**.
3. Cambia los **nameservers** del dominio (en GoDaddy, Namecheap, etc.) a los que te indique Cloudflare.
4. Espera a que el dominio quede **Active**.

### 2.2 Crear el túnel

1. Menú **Zero Trust** (o [one.dash.cloudflare.com](https://one.dash.cloudflare.com)).
2. Si es la primera vez, elige el plan **Zero Trust Free**.
3. **Networks** → **Tunnels** → **Create a tunnel**.
4. Nombre: `sci-ocr`.
5. Elige **Docker** como conector.
6. Copia el **token** que te muestra (empieza con algo largo; lo usarás una sola vez).

### 2.3 Public hostname (URL pública)

En la misma pantalla del túnel (o **Public Hostname** → Add):

| Campo | Valor |
|-------|--------|
| Subdomain | `ocr` (quedará `ocr.tudominio.com`) |
| Domain | tu dominio |
| Service type | HTTP |
| URL | `http://nginx:80` (cloudflared y nginx en el mismo `docker compose`) |

Guarda el hostname.

> Si instalas `cloudflared` **fuera** de Docker (directo en el VPS), usa `http://127.0.0.1:8080` en su lugar.

### 2.4 Arrancar cloudflared en el VPS

**Opción recomendada** — cloudflared junto a Docker (mismo proyecto):

En el VPS, edita `.env` y añade:

```env
CLOUDFLARE_TUNNEL_TOKEN=el-token-que-copiaste
```

Luego:

```bash
cd /opt/ocr-app
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build
```

Esto:

- Deja nginx escuchando **solo en localhost** (`127.0.0.1:8080`).
- Levanta el contenedor `cloudflared` que mantiene el túnel activo.

**Opción alternativa** — cloudflared instalado en el VPS (sin contenedor):

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
cloudflared service install TU_TOKEN_AQUI
systemctl enable --now cloudflared
```

Y en Public Hostname usa `http://127.0.0.1:8080`.

### 2.5 Probar

Desde tu casa u oficina, abre en el navegador:

```text
https://ocr.tudominio.com
```

Debería cargar la pantalla **Herramientas documentales** de SCI OCR.

En la web, escribe la **API Key** (`OCR_API_KEY`) una vez en la cabecera.

---

## Parte 3 — Solo miembros del despacho (Cloudflare Access)

1. Zero Trust → **Access** → **Applications** → **Add an application**.
2. Tipo: **Self-hosted**.
3. Application domain: `ocr.tudominio.com` (solo ese subdominio).
4. Policy: **Allow** → Include → **Emails** → lista los correos del equipo (o `@tudespacho.com` si todos usan el mismo dominio).
5. Guarda.

Ahora, al entrar a la URL, Cloudflare pide **correo + código** antes de ver la app. Después, dentro de la app, sigue pidiendo la **API Key** del despacho.

---

## Parte 4 — Instrucciones para el equipo

Envía esto por correo o Teams:

1. Abre **https://ocr.tudominio.com**
2. Inicia sesión con tu **correo del despacho** (Cloudflare Access).
3. En la parte superior, pega la **clave API** (te la damos por canal seguro). Queda guardada en el navegador.
4. **OCR de PDF** → sube el archivo → descarga **MD**.
5. **Historial OCR** → vuelve a descargar trabajos anteriores.

No instalar Python, Docker ni `arrancar.bat`.

---

## Comandos útiles (administrador)

```bash
cd /opt/ocr-app

# Ver logs OCR
docker compose logs -f ocr

# Ver logs túnel
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml logs -f cloudflared

# Reiniciar tras cambiar .env
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build

# Actualizar código
git pull
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build
```

---

## Seguridad y privacidad

- Los PDFs se guardan en el **disco del VPS** (volúmenes Docker `ocr-uploads`, `ocr-outputs`).
- No subas `.env` a Git (contiene claves).
- Deja `GEMINI_API_KEY` vacío si quieres OCR 100 % local en el servidor.
- Revisa espacio en disco cada mes; borra volúmenes viejos si hace falta.

---

## Problemas frecuentes

| Síntoma | Qué revisar |
|---------|-------------|
| 502 / no carga | `docker compose ps` — ¿ocr y nginx healthy? |
| Túnel down | `docker compose logs cloudflared` |
| API Key inválida | `.env` → `OCR_API_KEY` y reiniciar contenedor ocr |
| Archivo muy grande | `MAX_UPLOAD_MB` y `client_max_body_size` en `deploy/nginx.conf` (mismo número) |
| Access no deja entrar | Política de correos en Cloudflare Access |

---

## Resumen

| Antes | Después |
|-------|---------|
| `arrancar.bat` en tu PC | URL `https://ocr.tudominio.com` |
| PC oficina 24/7 | VPS en datacenter |
| Solo tú procesas | Todo el equipo, desde casa u oficina |

Cuando tengas **dominio en Cloudflare** y **VPS listo**, sigue Parte 1 → 2 → 3 en orden.
