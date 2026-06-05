# SCI OCR en internet — guía desde cero

Para alguien que **no tiene dominio, VPS ni repo en GitHub**.  
Objetivo: **`https://ocr.tudespacho.com`**, equipo entra con el navegador, **sin `arrancar.bat`**, **sin PC de oficina encendida**.

Tiempo estimado la primera vez: **2–4 horas** (esperas de DNS incluidas).

---

## Panorama rápido

| Pieza | Para qué | Coste aprox. |
|-------|----------|--------------|
| **GitHub** (repo privado) | Guardar código y clonarlo en el VPS | Gratis |
| **Dominio** (ej. `scidespacho.com`) | URL bonita y HTTPS | ~€10–15/año |
| **Cloudflare** | DNS, HTTPS, túnel, acceso solo al equipo | Gratis |
| **VPS** (servidor Linux 24/7) | Donde corre el OCR con Docker | ~€4–8/mes |

**Total habitual:** unos **€5–10/mes** + dominio anual.

---

## Paso 0 — Qué vas a conseguir

```
[ Compañero en casa ]  ──►  https://ocr.tudominio.com
[ Compañero en oficina ] ──►       (solo navegador)
[ Tú ]                 ──►
                              Cloudflare (HTTPS + correo autorizado)
                                    ↓
                              VPS (Docker: SCI OCR)
```

---

# PARTE A — Subir tu código a GitHub (desde tu PC Windows)

Tu carpeta local: `c:\proyectos\ocr-app`

### A.1 Crear cuenta y repo

1. Entra a [github.com](https://github.com) e inicia sesión (o crea cuenta).
2. **New repository**
   - Name: `ocr-app`
   - **Private** (recomendado: código + configuración del despacho)
   - No marques “Add README” si ya tienes código local
3. Copia la URL del repo, ej. `https://github.com/TU_USUARIO/ocr-app.git`

### A.2 Subir el proyecto desde PowerShell

Abre PowerShell en la carpeta del proyecto:

```powershell
cd c:\proyectos\ocr-app
```

Comprueba que **no** vas a subir secretos:

```powershell
git status
```

No deben aparecer `.env`, claves API ni carpetas con PDFs de clientes. El `.gitignore` del proyecto ya excluye lo habitual; revisa que `backend/.env` no esté trackeado.

Si aún no es repo git:

```powershell
git init
git add .
git commit -m "SCI OCR - version inicial para despliegue"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/ocr-app.git
git push -u origin main
```

Si ya era repo git, solo:

```powershell
git remote add origin https://github.com/TU_USUARIO/ocr-app.git
git push -u origin main
```

GitHub pedirá login (navegador o token).  
**Listo:** el código ya no depende solo de tu PC.

---

# PARTE B — Dominio + Cloudflare

Necesitas un nombre (ej. `ocr.scidespacho.com` o `ocr.tudespacho.mx`).

### B.1 Comprar dominio

Opciones sencillas:

| Dónde | Notas |
|-------|--------|
| **[Cloudflare Registrar](https://www.cloudflare.com/products/registrar/)** | Precio al coste; DNS ya integrado (recomendado) |
| Namecheap, Porkbun, etc. | Luego mueves el dominio a Cloudflare (gratis) |

Elige un nombre corto que el equipo reconozca.

### B.2 Añadir dominio a Cloudflare

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Add a site**
2. Escribe tu dominio → plan **Free**
3. Cloudflare te da **2 nameservers** (ej. `ada.ns.cloudflare.com`)
4. En el panel donde compraste el dominio, **cambia los nameservers** a los de Cloudflare
5. Espera 15 min – 48 h (a veces 15 min basta). Estado: **Active**

### B.3 Activar Zero Trust (gratis)

1. Menú **Zero Trust** (o [one.dash.cloudflare.com](https://one.dash.cloudflare.com))
2. Crea el equipo (nombre del despacho) → plan **Free**

---

# PARTE C — Contratar VPS

Un VPS = una computadora Linux en un datacenter, encendida 24/7.

### C.1 Proveedor recomendado (principiante)

**[Hetzner Cloud](https://www.hetzner.com/cloud)** — buen precio en EU:

1. Crear cuenta y verificar identidad
2. **Add Server**
   - Location: Falkenstein o Nuremberg (EU)
   - Image: **Ubuntu 24.04**
   - Type: **CX22** (2 vCPU, **4 GB RAM**, 40 GB) — suficiente para OCR
   - SSH key: ver abajo (recomendado) o contraseña por email
3. Anota la **IP pública** (ej. `95.217.xxx.xxx`)

Alternativas: DigitalOcean Droplet 4GB, Vultr, etc. Misma idea.

### C.2 Crear llave SSH en Windows (recomendado)

En PowerShell:

```powershell
ssh-keygen -t ed25519 -C "tu-email@despacho.com"
```

Enter, Enter, Enter (ruta por defecto `C:\Users\TU\.ssh\id_ed25519`).

Muestra la clave pública para pegarla en Hetzner al crear el servidor:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copia la línea entera → en Hetzner **Add SSH Key**.

### C.3 Probar conexión

```powershell
ssh root@TU_IP_DEL_VPS
```

Si entras, ves un prompt tipo `root@ubuntu-...#`. **Ya estás dentro del servidor.**

---

# PARTE D — Instalar Docker y la app en el VPS

Todo lo siguiente es **dentro del VPS** (por SSH), salvo que diga lo contrario.

### D.1 Instalar Git y Docker

```bash
apt-get update
apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sh
docker compose version
```

### D.2 Clonar tu repo

**Repo privado** — usa un token de GitHub:

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Tokens (classic)**
2. Generate → marcar **`repo`** → copiar token (guárdalo)

En el VPS:

```bash
cd /opt
git clone https://TU_USUARIO:TU_TOKEN@github.com/TU_USUARIO/ocr-app.git
cd ocr-app
```

(Sustituye usuario y token; no compartas el token.)

### D.3 Crear `.env` de producción

```bash
cp deploy/env.example .env
nano .env
```

Cambia **al menos**:

```env
OCR_HTTP_PORT=8080
OCR_API_KEY=EligeUnaClaveLargaComo-SciOcr-2026-Xk9mP2
MAX_UPLOAD_MB=300
```

Genera una clave difícil (la escribirá el equipo una vez en la web).  
Guarda: `Ctrl+O`, Enter, `Ctrl+X`.

**Anota la `OCR_API_KEY`** en un gestor de contraseñas del despacho.

### D.4 Arrancar la app (prueba interna)

```bash
docker compose up -d --build
```

La primera vez tarda **varios minutos** (descarga e instala dependencias).

Comprueba:

```bash
curl -s http://127.0.0.1:8080/health
docker compose ps
```

Debe responder `{"status":"ok"...}` y contenedores `healthy`.

---

# PARTE E — Cloudflare Tunnel (URL pública)

Así no abres puertos peligrosos en el VPS; Cloudflare se conecta hacia dentro.

### E.1 Crear túnel

1. Zero Trust → **Networks** → **Tunnels** → **Create a tunnel**
2. Nombre: `sci-ocr`
3. Conector: **Docker**
4. Copia el **Tunnel token** (cadena larga)

### E.2 Public Hostname (tu URL)

En el asistente o **Public Hostname** → **Add a public hostname**:

| Campo | Valor |
|-------|--------|
| Subdomain | `ocr` |
| Domain | tu dominio comprado |
| Type | HTTP |
| URL | `http://nginx:80` |

Resultado: **`https://ocr.tudominio.com`**

Guarda. Cloudflare crea el registro DNS solo.

### E.3 Activar túnel en el VPS

En el VPS:

```bash
cd /opt/ocr-app
nano .env
```

Añade al final:

```env
CLOUDFLARE_TUNNEL_TOKEN=pega-aqui-el-token-del-paso-E1
```

Levanta app + túnel:

```bash
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml ps
```

Deberías ver `ocr`, `nginx` y `cloudflared` running.

### E.4 Probar desde tu casa

En el navegador de tu PC:

```text
https://ocr.tudominio.com
```

- Debe cargar **Herramientas documentales**
- Arriba: campo **API Key** → pega la `OCR_API_KEY` del `.env`
- Prueba subir un PDF pequeño

Si falla: en el VPS `docker compose logs cloudflared` y `docker compose logs ocr`.

---

# PARTE F — Solo miembros del despacho (Cloudflare Access)

1. Zero Trust → **Access** → **Applications** → **Add an application**
2. **Self-hosted**
3. Application domain: `ocr.tudominio.com`
4. **Add a policy** → Allow
5. Include → **Emails** → añade correos del equipo (uno por línea)  
   O **Email domain** → `@tudespacho.com` si todos usan ese dominio
6. Guardar

Ahora al abrir la URL, Cloudflare pide **correo + código** antes de ver la app.  
Dentro de la app sigue la **API Key** del despacho (segunda capa).

---

# PARTE G — Decirle al equipo

Copia y adapta:

---

**SCI OCR — acceso**

1. Abre: **https://ocr.tudominio.com**
2. Entra con tu **correo del despacho** (te llega un código).
3. Arriba, pega la **clave API**: `[LA_CLAVE_QUE_DEFINISTE]` (solo una vez).
4. **OCR de PDF** → sube archivo → descarga **Markdown (.md)**.
5. **Historial OCR** → vuelve a descargar trabajos anteriores.

No instalar nada. Funciona en Mac, Windows y celular.

Si no carga: avisar a [tu contacto].

---

# PARTE H — Mantenimiento (solo admin)

Conéctate por SSH:

```bash
ssh root@TU_IP
cd /opt/ocr-app
```

| Tarea | Comando |
|-------|---------|
| Ver logs | `docker compose logs -f ocr` |
| Reiniciar | `docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml restart` |
| Actualizar app | `git pull` luego `docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml up -d --build` |
| Espacio en disco | `df -h` y `docker system df` |

**Privacidad:** los PDFs quedan en el VPS. Revisa disco cada mes.  
**Gemini:** déjalo vacío en `.env` si quieres OCR 100 % local.

---

# Checklist final

- [ ] Repo en GitHub (privado)
- [ ] Dominio en Cloudflare (Active)
- [ ] VPS 4 GB RAM
- [ ] `docker compose` healthy en VPS
- [ ] Túnel Cloudflare + `https://ocr.tudominio.com` carga
- [ ] Cloudflare Access con correos del equipo
- [ ] API Key repartida por canal seguro
- [ ] Prueba OCR + descarga MD desde otra red (ej. datos del celular)

---

# Problemas frecuentes

| Problema | Solución |
|----------|----------|
| `git push` pide credenciales | Token de GitHub en lugar de contraseña |
| DNS no resuelve | Esperar propagación; revisar nameservers |
| 502 Bad Gateway | `docker compose ps` — esperar a que `ocr` esté healthy |
| Túnel offline | Revisar `CLOUDFLARE_TUNNEL_TOKEN` en `.env` |
| API Key inválida | Misma clave en `.env` y en la web; reiniciar `ocr` |
| Subida > 300 MB | Comprimir PDF o subir por partes (rangos) |

---

# Orden sugerido hoy

1. **GitHub** (Parte A) — 20 min  
2. **Dominio + Cloudflare** (Parte B) — 30 min + espera DNS  
3. **VPS** (Parte C) — 15 min  
4. **Docker + app** (Parte D) — 30 min  
5. **Túnel** (Parte E) — 20 min  
6. **Access + equipo** (Parte F–G) — 15 min  

Guía complementaria del túnel: [CLOUDFLARE.md](CLOUDFLARE.md)
