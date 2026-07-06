#!/bin/bash
# Convierte proxies Webshare de formato host:port:user:pass
# a http://user:pass@host:port (uno por línea).
#
# Uso:
#   bash deploy/format-proxies.sh proxies-raw.txt > proxies.txt
#   # o pegar líneas directamente:
#   bash deploy/format-proxies.sh <<'EOF'
#   9.142.199.65:5232:myuser:mypass
#   9.142.199.66:5232:myuser:mypass
#   EOF

while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue

    IFS=':' read -r host port user pass <<< "$line"
    if [[ -z "$host" || -z "$port" || -z "$user" || -z "$pass" ]]; then
        echo "⚠️  Línea inválida (esperado host:port:user:pass): $line" >&2
        continue
    fi
    echo "http://${user}:${pass}@${host}:${port}"
done < "${1:-/dev/stdin}"
