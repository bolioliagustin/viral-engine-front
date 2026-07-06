"""
Sprint 1.1 — Dia 1: Probe cobalt.tools API

Tests multiple known public instances of cobalt to see which one still works
for YouTube video downloads. Cobalt changed their API in 2024 so we need to
verify what's actually responsive today.

Run: python test_cobalt.py
"""
import requests
import json
import sys

# Instancias conocidas (publicas + comunidad)
# Al Abr/2026 cobalt oficial requiere auth; estas son instancias comunidad
INSTANCES = [
    "https://api.cobalt.tools",           # oficial (probablemente requiere auth)
    "https://co.wuk.sh",                   # mirror conocido
    "https://dl01.yt-dl.click",            # comunidad
    "https://cobalt.synzr.ru",             # comunidad
    "https://capi.oak.li",                 # comunidad
]

TEST_VIDEOS = [
    ("Short - Rick Roll", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
    ("Podcast clip", "https://www.youtube.com/watch?v=jNQXAC9IVRw"),
    ("Long video", "https://www.youtube.com/watch?v=9bZkp7q19f0"),
]


def test_instance(base_url: str, video_url: str, timeout: int = 15) -> dict:
    """Prueba un POST a una instancia de cobalt."""
    endpoint = f"{base_url}/api/json" if "/api" not in base_url else base_url

    # Formato API v7 (nuevo)
    payload_v7 = {
        "url": video_url,
        "videoQuality": "720",
        "filenameStyle": "basic",
    }

    # Formato API v6 (viejo)
    payload_v6 = {
        "url": video_url,
        "vQuality": "720",
        "isAudioOnly": False,
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ViralEngine/1.0 (testing cobalt)",
    }

    results = {}
    for version, payload in [("v7", payload_v7), ("v6", payload_v6)]:
        try:
            # Probar tanto / como /api/json
            endpoints_to_try = [base_url, f"{base_url}/api/json"]
            for ep in endpoints_to_try:
                try:
                    r = requests.post(ep, json=payload, headers=headers, timeout=timeout)
                    results[f"{version}@{ep}"] = {
                        "status": r.status_code,
                        "body_preview": r.text[:400],
                    }
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("status") in ("stream", "success", "tunnel", "redirect"):
                            results[f"{version}@{ep}"]["WORKS"] = True
                            results[f"{version}@{ep}"]["download_url"] = data.get("url")
                            return results  # found working combo, stop
                except Exception as e:
                    results[f"{version}@{ep}"] = {"error": str(e)[:200]}
        except Exception as e:
            results[version] = {"error": str(e)[:200]}

    return results


def main():
    print("=" * 70)
    print("COBALT.TOOLS PROBE — Sprint 1.1 Dia 1")
    print("=" * 70)

    _, test_url = TEST_VIDEOS[0]  # solo probar con el primero para ir rapido
    print(f"\nVideo de prueba: {test_url}\n")

    winners = []
    for inst in INSTANCES:
        print(f"\n{'─' * 70}")
        print(f"Probando: {inst}")
        print(f"{'─' * 70}")
        res = test_instance(inst, test_url)
        for key, val in res.items():
            if isinstance(val, dict):
                status = val.get("status", "N/A")
                works = val.get("WORKS", False)
                marker = "✅ FUNCIONA" if works else ("⚠️" if status == 200 else "❌")
                print(f"  {marker} [{key}] status={status}")
                if works:
                    winners.append((inst, key, val.get("download_url", "")))
                if val.get("body_preview"):
                    print(f"     preview: {val['body_preview'][:200]}")
                if val.get("error"):
                    print(f"     error: {val['error']}")

    print("\n" + "=" * 70)
    print("RESULTADO FINAL")
    print("=" * 70)
    if winners:
        print(f"\n✅ Instancias que funcionan ({len(winners)}):")
        for inst, ver, url in winners:
            print(f"   • {inst} ({ver})")
            print(f"     download URL sample: {url[:100]}")
        print("\n→ Usar la primera que funcione como COBALT_API_URL")
    else:
        print("\n❌ NINGUNA instancia publica funciona.")
        print("   → Opciones: self-host cobalt en Render, o plan B (RapidAPI).")

    return 0 if winners else 1


if __name__ == "__main__":
    sys.exit(main())
