# 📡 API Documentation - Backend Endpoints

Documentación completa de todos los endpoints del backend con ejemplos de testing.


**Base URL:** `http://localhost:3000` (desarrollo)

---

## 🔍 Índice de Endpoints

1. [GET /](#1-get--root)
2. [GET /health](#2-get-health)
3. [POST /process](#3-post-process)
4. [GET /status/:jobId](#4-get-statusjobid)
5. [GET /jobs](#5-get-jobs)
6. [GET /user/:userId/credits](#6-get-useruseridcredits)

---

## 1. GET / (Root)

**Descripción:** Información general de la API

### Request
```bash
curl http://localhost:3000/
```

### Response (200 OK)
```json
{
  "name": "YouTube Viral Content Engine",
  "version": "1.0.0",
  "endpoints": {
    "POST /process": "Submit YouTube URL for processing",
    "GET /status/:jobId": "Check job status and results",
    "GET /jobs": "List all jobs",
    "GET /health": "Health check"
  }
}
```

---

## 2. GET /health

**Descripción:** Health check del servidor

### Request
```bash
curl http://localhost:3000/health
```

### Response (200 OK)
```json
{
  "status": "ok",
  "timestamp": "2026-01-09T15:00:00.000Z"
}
```

---

## 3. POST /process

**Descripción:** Enviar un video de YouTube para procesamiento

**⚠️ Rate Limit:** 5 requests cada 15 minutos por IP

### Request Headers
```
Content-Type: application/json
```

### Request Body
```json
{
  "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "userId": "uuid-del-usuario-aqui"
}
```

### Ejemplo con cURL
```bash
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "userId": "123e4567-e89b-12d3-a456-426614174000"
  }'
```

### Ejemplo con PowerShell
```powershell
$body = @{
    videoUrl = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    userId = "123e4567-e89b-12d3-a456-426614174000"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:3000/process" `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

### Response (201 Created) - Éxito
```json
{
  "success": true,
  "jobId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "message": "Job queued for processing"
}
```

### Response (400 Bad Request) - URL inválida
```json
{
  "error": "Invalid YouTube URL"
}
```

### Response (402 Payment Required) - Sin créditos
```json
{
  "error": "Insufficient credits",
  "message": "No tienes créditos disponibles. Por favor recarga para continuar."
}
```

### Response (409 Conflict) - Duplicado
```json
{
  "error": "Duplicate job",
  "message": "Este video ya fue procesado recientemente",
  "existingJobId": "existing-job-uuid"
}
```

### Response (429 Too Many Requests) - Rate limit
```json
{
  "error": "Too many requests",
  "message": "Has excedido el límite de solicitudes. Por favor espera 15 minutos."
}
```

### Response (500 Server Error)
```json
{
  "error": "Server error",
  "message": "Error al verificar tus créditos. Por favor intenta de nuevo."
}
```

---

## 4. GET /status/:jobId

**Descripción:** Consultar el estado y resultados de un job

### Request
```bash
# Reemplaza JOB_ID con el ID real
curl http://localhost:3000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Response (200 OK) - Job en proceso
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "videoTitle": "Rick Astley - Never Gonna Give You Up",
  "status": "processing",
  "current_step": "analyzing",
  "progress_percentage": 50,
  "errorMessage": null,
  "createdAt": "2026-01-09T15:00:00.000Z",
  "updatedAt": "2026-01-09T15:02:30.000Z",
  "results": []
}
```

### Response (200 OK) - Job completado
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "videoTitle": "Rick Astley - Never Gonna Give You Up",
  "status": "completed",
  "current_step": "completed",
  "progress_percentage": 100,
  "errorMessage": null,
  "createdAt": "2026-01-09T15:00:00.000Z",
  "updatedAt": "2026-01-09T15:10:00.000Z",
  "results": [
    {
      "id": "result-uuid-1",
      "type": "twitter_thread",
      "content": "🎵 Los momentos más virales de este video...",
      "clip_url": "https://storage.supabase.co/clips/job-id/clip_1.mp4",
      "start_time": 45,
      "end_time": 75,
      "hook": "La reacción cuando escucha el coro",
      "emotional_trigger": "Nostalgia",
      "moment_index": 1,
      "score_hook": 9,
      "score_retention": 8,
      "score_shareability": 10
    }
  ]
}
```

### Response (404 Not Found)
```json
{
  "error": "Job not found"
}
```

---

## 5. GET /jobs

**Descripción:** Listar todos los jobs (últimos 50)

**⚠️ Nota:** En producción, este endpoint debería estar protegido (solo admin) o filtrado por usuario.

### Request
```bash
curl http://localhost:3000/jobs
```

### Response (200 OK)
```json
[
  {
    "id": "job-uuid-1",
    "user_id": "user-uuid-1",
    "video_url": "https://youtube.com/watch?v=...",
    "video_title": "Video Title",
    "status": "completed",
    "current_step": "completed",
    "progress_percentage": 100,
    "error_message": null,
    "created_at": "2026-01-09T15:00:00.000Z",
    "updated_at": "2026-01-09T15:10:00.000Z"
  },
  {
    "id": "job-uuid-2",
    "user_id": "user-uuid-2",
    "video_url": "https://youtube.com/watch?v=...",
    "video_title": "Another Video",
    "status": "processing",
    "current_step": "transcribing",
    "progress_percentage": 30,
    "error_message": null,
    "created_at": "2026-01-09T15:05:00.000Z",
    "updated_at": "2026-01-09T15:07:00.000Z"
  }
]
```

---

## 6. GET /user/:userId/credits

**Descripción:** Consultar créditos y suscripción de un usuario

### Request
```bash
# Reemplaza USER_ID con el UUID real
curl http://localhost:3000/user/123e4567-e89b-12d3-a456-426614174000/credits
```

### Response (200 OK)
```json
{
  "credits": 5,
  "subscription": "free"
}
```

### Response (404 Not Found)
```json
{
  "error": "User not found"
}
```

### Response (501 Not Implemented)
```json
{
  "error": "Supabase not configured"
}
```

---

## 🧪 Guía de Testing

### Escenario 1: Flujo Completo Exitoso

```bash
# 1. Crear un job
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "userId": "tu-user-id-aqui"
  }'

# Respuesta: {"success": true, "jobId": "abc-123", ...}

# 2. Consultar estado (repetir cada 10s hasta que status = "completed")
curl http://localhost:3000/status/abc-123

# 3. Ver resultados cuando status = "completed"
curl http://localhost:3000/status/abc-123
```

### Escenario 2: Validación de URL

```bash
# URL inválida (debe retornar 400)
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{"videoUrl": "https://google.com", "userId": "user-id"}'

# Sin URL (debe retornar 400)
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{"userId": "user-id"}'
```

### Escenario 3: Sin Créditos

```bash
# 1. En Supabase, establece credits = 0 para tu usuario
# 2. Intenta procesar un video
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "userId": "tu-user-id-sin-creditos"
  }'

# Debe retornar 402: "No tienes créditos disponibles..."
```

### Escenario 4: Duplicados

```bash
# 1. Procesa un video
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=test123",
    "userId": "user-id"
  }'

# 2. Espera a que complete (status = "completed")

# 3. Intenta procesarlo de nuevo
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=test123",
    "userId": "user-id"
  }'

# Debe retornar 409: "Este video ya fue procesado recientemente"
```

### Escenario 5: Rate Limiting

```bash
# Ejecuta este comando 6 veces seguidas
for i in {1..6}; do
  echo "Request $i"
  curl -X POST http://localhost:3000/process \
    -H "Content-Type: application/json" \
    -d '{"videoUrl": "https://youtube.com/watch?v=test", "userId": "user-id"}'
  echo ""
done

# Las primeras 5 deberían funcionar
# La 6ta debe retornar 429: "Has excedido el límite..."
```

### Escenario 6: Video Demasiado Largo

```bash
# Busca un video de YouTube > 2 horas
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d '{
    "videoUrl": "https://www.youtube.com/watch?v=VIDEO_MUY_LARGO",
    "userId": "user-id"
  }'

# El worker debe fallar con: "Video demasiado largo (máximo 2.0 horas...)"
# El job status mostrará: status = "failed", error_message = "Video demasiado largo..."
```

---

## 🛠️ Testing con Postman/Insomnia

### Importar Colección (Postman)

Crea un archivo `postman_collection.json`:

```json
{
  "info": {
    "name": "Viral Content Engine API",
    "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
  },
  "item": [
    {
      "name": "Health Check",
      "request": {
        "method": "GET",
        "url": "http://localhost:3000/health"
      }
    },
    {
      "name": "Process Video",
      "request": {
        "method": "POST",
        "header": [{"key": "Content-Type", "value": "application/json"}],
        "url": "http://localhost:3000/process",
        "body": {
          "mode": "raw",
          "raw": "{\n  \"videoUrl\": \"https://www.youtube.com/watch?v=dQw4w9WgXcQ\",\n  \"userId\": \"{{USER_ID}}\"\n}"
        }
      }
    },
    {
      "name": "Get Job Status",
      "request": {
        "method": "GET",
        "url": "http://localhost:3000/status/{{JOB_ID}}"
      }
    },
    {
      "name": "List All Jobs",
      "request": {
        "method": "GET",
        "url": "http://localhost:3000/jobs"
      }
    },
    {
      "name": "Get User Credits",
      "request": {
        "method": "GET",
        "url": "http://localhost:3000/user/{{USER_ID}}/credits"
      }
    }
  ]
}
```

### Variables de Entorno (Postman)
```json
{
  "USER_ID": "tu-uuid-de-usuario",
  "JOB_ID": "se-llena-automaticamente"
}
```

---

## 📊 Códigos de Estado HTTP

| Código | Significado | Cuándo se usa |
|--------|-------------|---------------|
| 200 | OK | Request exitoso (GET) |
| 201 | Created | Job creado exitosamente |
| 400 | Bad Request | URL inválida o parámetros faltantes |
| 402 | Payment Required | Sin créditos suficientes |
| 404 | Not Found | Job o usuario no encontrado |
| 409 | Conflict | Video duplicado (ya procesado) |
| 429 | Too Many Requests | Rate limit excedido |
| 500 | Server Error | Error interno del servidor |
| 501 | Not Implemented | Feature no disponible (ej: Supabase no configurado) |

---

## 🔐 Seguridad

### Headers de Rate Limiting

Cuando un request es limitado, se envían estos headers:

```
RateLimit-Limit: 5
RateLimit-Remaining: 0
RateLimit-Reset: 1704812400
Retry-After: 900
```

### CORS

El servidor acepta requests solo desde:
- `http://localhost:3001` (desarrollo)
- La URL configurada en `FRONTEND_URL` (producción)

---

## 🐛 Debugging

### Ver logs del backend en tiempo real

```bash
# En el directorio backend
npm start

# Los logs mostrarán:
# - Requests recibidos
# - Errores de validación
# - Rate limits aplicados
# - Jobs creados
```

### Verificar Supabase desde el backend

```bash
# Consulta directa a Supabase desde Node
node -e "
const { supabase } = require('./src/lib/supabase');
supabase.from('jobs').select('*').limit(5).then(r => console.log(r.data));
"
```

---

## 💡 Tips

1. **Obtener tu User ID**: Ve a Supabase Dashboard → Authentication → Users → copia el UUID
2. **Monitorear jobs**: Usa `GET /jobs` para ver todos los trabajos en tiempo real
3. **Limpiar queue**: Si hay jobs atascados, elimina los archivos de `queue/*.json`
4. **Reset rate limit**: Reinicia el servidor backend (los límites se resetean)

---

## 📞 Soporte

Si encuentras problemas:
1. Verifica que el backend esté corriendo (`npm start`)
2. Verifica que el worker esté corriendo (`python main.py`)
3. Revisa los logs de ambos
4. Verifica que Supabase esté configurado correctamente

**Logs importantes:**
- Backend: Mensajes de error, validaciones, rate limits
- Worker: Progreso de procesamiento, errores de APIs externas
