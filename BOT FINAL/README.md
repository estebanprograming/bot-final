# BOT 14/15 EN VIVO — SISTEMA DEFINITIVO (24/7)

Sistema algorítmico automatizado para apuestas deportivas al descanso (Minuto 45).
Diseñado para operar en vivo de forma autónoma las 24 horas del día, los 7 días de la semana, enviando alertas prioritarias a Telegram.

---

## 🎯 Parámetros Oficiales del Motor

* **Línea Menos 3.5 Goles (-3.5):**
  * Marcador al descanso: `1-0` o `0-1`.
  * Filtro Pre-Match: `ALTA_CONFIANZA` (`esp_total <= 2.60`, `estab_35 >= 50%`, `liga_2t <= 1.70`, `exigir_sin_rojas = True`, `prob >= 0.78`).
  * Condición en vivo: Tiros a puerta (SOT) del equipo que va perdiendo $\le 2$.

* **Línea Menos 4.5 Goles (-4.5):**
  * Marcador al descanso: Solo `1-1` o `0-2`.
  * Condición en vivo: Tiros a puerta totales combinados $\le 2$.

* **Vigilancia Activa de Segundo Tiempo:**
  * Alerta de Cashout preventivo si se detecta tarjeta roja en el 2T (minuto 46 al 65).

---

## 📱 Configuración de Telegram Integrada

* **Token:** `8792051359:AAEdiOxPj3dbMgOR6HwK0V_Ez0dQ8U_4bZU`
* **Chat ID:** `9202582025` (@AnalistaGoles2026_bot)
* **Archivo de configuración:** `config.py`

---

## 🚀 Cómo Ponerlo a Funcionar

### Opción A: En Servidor Gratuito en la Nube (24/7 sin Apagarse)
Lee el archivo `GUIA_COMO_SUBIR_EL_BOT_A_INTERNET_GRATIS_24_7.txt` o `GUIA_COMO_SUBIR_EL_BOT_A_INTERNET_GRATIS_24_7.pdf`.
Solo subes esta carpeta a tu GitHub y la conectas a **Render.com** o **Railway.app** en 3 minutos. Ya dejamos incluidos `Procfile`, `render.yaml` y `Dockerfile`.

### Opción B: En tu Computadora Windows (1 Clic)
Haz doble clic en `iniciar_bot_windows.bat`. El bot arrancará y se quedará vigilando los partidos en segundo plano. Para detenerlo, haz doble clic en `detener_bot_windows.bat`.
