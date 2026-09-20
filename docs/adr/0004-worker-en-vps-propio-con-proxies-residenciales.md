# El worker corre en un VPS propio con proxies residenciales y RapidAPI; el API queda en Render

YouTube bloquea las descargas desde IPs de datacenter (403 de googlevideo, "sign in to confirm you're not a bot", DRM/PO token) y Render free (512 MB) no soporta FFmpeg sobre videos largos. Tras pasar por Render y Hetzner, el worker quedó en Docker sobre un VPS de OVH (6 vCPU / 12 GB): los transcripts se obtienen por Supadata, las URLs de stream por RapidAPI resueltas y descargadas por el mismo proxy residencial (Webshare, "sticky"), y yt-dlp queda como alternativa. El API liviano sigue en Render y el frontend en Vercel porque no necesitan salir a YouTube.

## Consequences

- Cuatro proveedores de infraestructura y varios servicios de terceros en el camino crítico de cada job.
- La fiabilidad de la descarga depende de la salud de los proxies (en julio de 2026 bajaban a 30 KB/s) y fue la causa del freno del proyecto; motivó la decisión 0007.
- En una máquina con IP residencial (por ejemplo la Mac de desarrollo) yt-dlp funciona sin proxies ni RapidAPI.
