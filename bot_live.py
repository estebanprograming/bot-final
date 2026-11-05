# -*- coding: utf-8 -*-
"""
INICIADOR PRINCIPAL — BOT 14/15 EN VIVO (24/7)
Compatible con Render Free Tier (Web Service $0/mes), Koyeb, Railway, VPS y Windows.
Incluye servidor web ligero de health-check para desbloquear el plan GRATUITO de Render.
"""
import os
import sys
import time
import threading
import http.server
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_base import run_live_247
import config

PORT = int(os.environ.get("PORT", "8080"))

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "bot_14_15_online", "mode": "24/7", "health": "ok"}')

    def log_message(self, format, *args):
        pass  # Silenciar logs HTTP


def iniciar_servidor_web():
    """Inicia un servidor HTTP ligero para habilitar el plan Free Web Service en Render / Koyeb."""
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
        print(f"[WEB SERVICE] Servidor HTTP de salud activo en puerto {PORT} (Plan Free habilitado)")
        server.serve_forever()
    except Exception as e:
        print(f"[WEB SERVICE] Aviso servidor web: {e}")


def anti_sleep_self_ping():
    """Si corre en Render Free Web Service, se auto-pingea cada 10 minutos para nunca dormirse."""
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    print(f"[ANTI-SLEEP] Auto-ping activado hacia {url} cada 10 minutos.")
    while True:
        time.sleep(600)  # 10 minutos
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SelfPingBot/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                pass
        except Exception:
            pass


if __name__ == "__main__":
    # Iniciar servidor web en segundo plano para que Render lo detecte como Web Service ($0/mes)
    t_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
    t_web.start()

    # Iniciar auto-ping si está en la nube
    t_ping = threading.Thread(target=anti_sleep_self_ping, daemon=True)
    t_ping.start()

    try:
        run_live_247(titulo="BOT 14/15 DEFINITIVO EN VIVO")
    except KeyboardInterrupt:
        print("\n[INFO] Bot detenido manualmente.")
    except Exception as e:
        print(f"\n[FATAL] Error crítico de inicio: {e}")
