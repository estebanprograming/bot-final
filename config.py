# -*- coding: utf-8 -*-
"""
CONFIGURACIÓN CENTRAL — BOT 14/15 EN VIVO (BOT DEFINITIVO)
"""
import os

# Credenciales oficiales de Telegram proporcionadas
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8792051359:AAEdiOxPj3dbMgOR6HwK0V_Ez0dQ8U_4bZU").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "9202582025").strip()

# Parámetros del Motor de Decisión (Filtro ALTA_CONFIANZA + HT SOT)
PERFIL_PREMATCH = "ALTA_CONFIANZA"
ESP_MAX = 2.60          # Máxima expectativa combinada de goles en 2T
E35_MIN = 50.0          # Mínimo porcentaje de estabilidad Under 3.5
LIGA_2T_MAX = 1.70      # Máximo promedio de goles de la liga en 2T
PROB_MIN = 0.78         # Mínima probabilidad Poisson/Bayesiana
LAMBDA_MAX = 2.00       # Máxima intensidad ofensiva esperada en 2T
EXIGIR_SIN_ROJAS = True # Descarte inmediato si hubo roja en 1T

# Parámetros al Descanso (Minuto 45 - HT)
SOT_PIERDE_MAX_1G = 2   # Para marcadores 1-0 y 0-1 (Línea -3.5): SOT del que pierde <= 2
SOT_TOT_MAX_2G = 2      # Para marcadores 1-1 y 0-2 (Línea -4.5): SOT total acumulado <= 2

# Parámetros de Operación 24/7
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # Sondeo cada 60 segundos
COBERTURA = "AGREGADO"  # Cobertura global de ligas ESPN
DIAS_HISTORIAL = 75     # Días hacia atrás para calcular perfiles pre-partido
HEARTBEAT_HORAS = 12.0  # Mensaje de vida a Telegram cada 12 horas
MONITOREAR_2T = True    # Vigilar tarjetas rojas en el segundo tiempo para Cashout preventivo
