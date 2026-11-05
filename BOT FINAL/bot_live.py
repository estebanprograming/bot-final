# -*- coding: utf-8 -*-
"""
INICIADOR PRINCIPAL — BOT 14/15 EN VIVO (24/7)
Punto de entrada para ejecutar en servidor gratuito (Render, Railway, VPS, etc.)
o localmente en segundo plano.
"""
import os
import sys

# Asegurar que el directorio actual esté en el PATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from live_base import run_live_247
import config

if __name__ == "__main__":
    try:
        run_live_247(titulo="BOT 14/15 DEFINITIVO EN VIVO")
    except KeyboardInterrupt:
        print("\n[INFO] Bot detenido manualmente por el usuario.")
    except Exception as e:
        print(f"\n[FATAL] Error crítico de inicio: {e}")
