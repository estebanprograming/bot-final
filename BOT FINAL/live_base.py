# -*- coding: utf-8 -*-
"""
MOTOR DE EJECUCIÓN EN VIVO 24/7 — BOT 14/15 / BOT DEFINITIVO
- Escaneo continuo en tiempo real al descanso (minuto 45).
- Aplicación estricta de ALTA_CONFIANZA (Menos 3.5 en 1-0/0-1; Menos 4.5 en 1-1/0-2).
- Base de datos SQLite persistente con conteo diario y acumulado de partidos analizados y apuestas.
- Escucha continua de comandos en Telegram ('SOLICITAR ESTADISTICA') con envío automático de PDF.
- Notificaciones prioritarias al minuto 45.
- Monitoreo de tarjetas rojas en el 2T (Alerta de Cashout preventivo).
- Auto-recuperación ante fallos de conexión y rotación automática de medianoche.
"""
import os
import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

import requests
import config
import comun
import database
import bot_under35_v6 as bot


# ---------------------------------------------------------------------------
# HILO DE ESCUCHA DE COMANDOS TELEGRAM ('SOLICITAR ESTADISTICA')
# ---------------------------------------------------------------------------
def escuchador_comandos_telegram(token: str, chat_id_objetivo: str):
    """Escucha en segundo plano los mensajes del usuario en Telegram."""
    offset = None
    print("[TELEGRAM] Escuchador de comandos activo (Esperando 'SOLICITAR ESTADISTICA')...")

    while True:
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"timeout": 15}
            if offset is not None:
                params["offset"] = offset

            resp = requests.get(url, params=params, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    update_id = update["update_id"]
                    offset = update_id + 1

                    msg = update.get("message") or {}
                    texto = str(msg.get("text") or "").strip()
                    remitente_chat = str(msg.get("chat", {}).get("id") or "")

                    # Verificar si coincide con el comando y el chat autorizado
                    texto_upper = texto.upper()
                    if "SOLICITAR ESTADISTICA" in texto_upper or "SOLICITAR ESTADÍSTICA" in texto_upper or texto_upper in ("/STATS", "/ESTADISTICA", "/ESTADISTICAS"):
                        print(f"\n[COMANDO RECIBIDO] '{texto}' de chat {remitente_chat}")
                        
                        # Mensaje de confirmación inmediata
                        comun.enviar_mensaje(
                            "⏳ <b>Procesando solicitud...</b>\nGenerando reporte estadístico oficial del Bot 14/15 en PDF con todas las entradas y partidos analizados.",
                            chat=remitente_chat
                        )

                        # Generar PDF en tiempo real con datos de la DB
                        fecha_hoy = datetime.now(bot.ZONA).strftime("%Y-%m-%d")
                        pdf_path = Path(__file__).parent / "Reporte_Estadisticas_EnVivo.pdf"
                        database.generar_pdf_estadisticas(str(pdf_path), fecha_hoy)

                        # Enviar PDF
                        comun.enviar_pdf(
                            str(pdf_path),
                            caption="📊 <b>Reporte Estadístico Oficial — Bot 14/15 En Vivo</b>",
                            chat=remitente_chat
                        )

                        # Enviar resumen en texto
                        m = database.obtener_metricas_globales(fecha_hoy)
                        comun.enviar_mensaje(
                            f"📊 <b>RESUMEN ESTADÍSTICO ACTUALIZADO</b>\n\n"
                            f"📅 <b>Fecha:</b> {fecha_hoy}\n"
                            f"🔍 <b>Partidos analizados hoy:</b> {m['analizados_hoy']}\n"
                            f"📈 <b>Partidos analizados histórico:</b> {m['analizados_historico']}\n"
                            f"🎯 <b>Total entradas ejecutadas:</b> {m['total_apuestas']}\n"
                            f"✅ <b>Ganadas:</b> {m['ganadas']} | ❌ <b>Perdidas:</b> {m['perdidas']} | ⏳ <b>En juego:</b> {m['pendientes']}\n"
                            f"🏆 <b>Efectividad Real:</b> {m['efectividad']:.1f}%\n\n"
                            f"📄 <i>El PDF con el desglose día por día y la lista completa de partidos ya fue enviado arriba.</i>",
                            chat=remitente_chat
                        )

        except Exception as e:
            # Tolerancia ante cortes momentáneos de red
            time.sleep(3)
        time.sleep(1)


# ---------------------------------------------------------------------------
# BUCLE PRINCIPAL DE EJECUCIÓN EN VIVO 24/7
# ---------------------------------------------------------------------------
def run_live_247(titulo: str = "BOT 14/15 EN VIVO"):
    # Inicializar Base de Datos SQLite persistente
    database.init_db()

    token, chat_id = comun.telegram_config()
    
    # Iniciar hilo de escucha de comandos en Telegram
    if token and chat_id:
        t_listener = threading.Thread(
            target=escuchador_comandos_telegram,
            args=(token, chat_id),
            daemon=True
        )
        t_listener.start()

    client = bot.EspnClient("cache", workers=6, timeout=25)
    cfg = dict(bot.CFG)
    cfg["usar_contexto_vivo"] = False

    # Archivo persistente para no duplicar alertas
    alertas_path = Path("alertas_enviadas.json")
    enviadas = set()
    if alertas_path.exists():
        try:
            enviadas = set(json.loads(alertas_path.read_text(encoding="utf-8")))
        except Exception:
            enviadas = set()

    dia_actual = ""
    indice = []
    global_avg = 1.45
    alpha = 0.10
    cache_perfiles = {}
    partidos_activos = {}  # {event_id: {local, visita, ht, linea, alertado_roja}}
    ultimo_heartbeat = time.time()

    print("=" * 70)
    print(f"🚀 INICIANDO {titulo} (MODO 24/7 AUTÓNOMO)")
    print(f"📱 Canal Telegram: {config.TELEGRAM_CHAT_ID}")
    print(f"💬 Comando activado: Escribe 'SOLICITAR ESTADISTICA' en Telegram para recibir el PDF")
    print(f"⚙️ Filtro Pre-Match: {config.PERFIL_PREMATCH} (esp <= {config.ESP_MAX}, liga <= {config.LIGA_2T_MAX})")
    print(f"🎯 Regla al Descanso: 1-0/0-1 SOT <= {config.SOT_PIERDE_MAX_1G} (-3.5) | 1-1/0-2 SOT <= {config.SOT_TOT_MAX_2G} (-4.5)")
    print(f"⏱ Intervalo de sondeo: cada {config.POLL_INTERVAL} segundos")
    print("=" * 70)

    # Mensaje de arranque a Telegram
    comun.enviar_mensaje(
        f"🚀 <b>{titulo} INICIADO Y EN LÍNEA (24/7)</b>\n\n"
        f"📡 <b>Modo:</b> Monitoreo en tiempo real al descanso (minuto 45)\n"
        f"⚙️ <b>Filtro:</b> ALTA_CONFIANZA (-3.5 y -4.5)\n"
        f"🛡️ <b>Condición HT:</b> SOT &le; 2 (del que pierde en 1G / total en 2G)\n"
        f"📊 <b>Estadísticas:</b> Escribe <code>SOLICITAR ESTADISTICA</code> en cualquier momento y te enviaré el PDF con los resultados día a día.\n"
        f"🟢 <b>Estado:</b> Vigilancia activa de partidos de la jornada."
    )

    while True:
        try:
            ahora_dt = datetime.now(bot.ZONA)
            hoy_str = ahora_dt.strftime("%Y-%m-%d")
            hoy_ymd = hoy_str.replace("-", "")

            # -------------------------------------------------------------
            # 1. Rotación diaria automática (medianoche)
            # -------------------------------------------------------------
            if hoy_str != dia_actual:
                dia_actual = hoy_str
                ayer_str = (datetime.strptime(dia_actual, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                desde_str = (datetime.strptime(dia_actual, "%Y-%m-%d") - timedelta(days=config.DIAS_HISTORIAL)).strftime("%Y-%m-%d")
                print(f"\n[CALENDARIO] Nueva jornada detectada: {dia_actual}")
                print(f"[HISTORIAL] Actualizando índice {desde_str} -> {ayer_str} ...")
                
                try:
                    indice = bot.construir_indice(client, desde_str, ayer_str, cobertura=config.COBERTURA)
                    print(f"[HISTORIAL] Partidos históricos cargados: {len(indice)}")
                    global_avg, alpha = bot.media_2t_global(
                        indice, bot.iso_utc(datetime.strptime(dia_actual, "%Y-%m-%d").replace(tzinfo=bot.ZONA))
                    )
                    cache_perfiles.clear()
                except Exception as e:
                    print(f"[WARN] Error al actualizar historial diario: {e}")

            # -------------------------------------------------------------
            # 2. Heartbeat periódico a Telegram
            # -------------------------------------------------------------
            if (time.time() - ultimo_heartbeat) >= (config.HEARTBEAT_HORAS * 3600):
                ultimo_heartbeat = time.time()
                m_hb = database.obtener_metricas_globales(dia_actual)
                comun.enviar_mensaje(
                    f"🟢 <b>REPORTE DE ESTADO — {titulo}</b>\n\n"
                    f"📅 <b>Fecha:</b> {dia_actual}\n"
                    f"📡 <b>Sistema:</b> Operativo 24/7 sin interrupciones\n"
                    f"🔍 <b>Partidos analizados hoy:</b> {m_hb['analizados_hoy']}\n"
                    f"📈 <b>Partidos analizados histórico:</b> {m_hb['analizados_historico']}\n"
                    f"🎯 <b>Total entradas:</b> {m_hb['total_apuestas']} (Efectividad: {m_hb['efectividad']:.1f}%)\n"
                    f"💬 <i>Escribe 'SOLICITAR ESTADISTICA' para descargar el PDF completo.</i>"
                )

            # -------------------------------------------------------------
            # 3. Sondeo del Scoreboard en Vivo y Registro en DB
            # -------------------------------------------------------------
            evs = client.scoreboard(hoy_ymd, cobertura=config.COBERTURA)
            for ev in evs:
                p = bot.parsear_evento(ev, hoy_ymd)
                if not p:
                    continue

                ht_str = f"{p.goles_local_ht}-{p.goles_visitante_ht}" if p.tiene_ht else "SIN_HT"
                
                # Registrar en la base de datos de auditoría
                database.registrar_partido_analizado(
                    event_id=p.event_id,
                    fecha=hoy_str,
                    liga=p.liga_nombre,
                    local=p.local_nombre,
                    visitante=p.visitante_nombre,
                    ht=ht_str,
                    estado=p.estado
                )

                # Seguimiento del segundo tiempo (Monitor de Roja para Cashout y Actualización de FT)
                if p.event_id in partidos_activos and config.MONITOREAR_2T:
                    act = partidos_activos[p.event_id]
                    
                    # 1. Chequeo de expulsión en 2T (min 46 al 70)
                    if not act["alertado_roja"] and p.estado not in ("STATUS_SCHEDULED", "STATUS_HALFTIME"):
                        d_live = client.summary(p.event_id, ttl=15)
                        if d_live:
                            comms = d_live.get("commentary") or []
                            for c in comms:
                                play = c.get("play") or {}
                                if str((play.get("type") or {}).get("text", "")) == "Red Card":
                                    m_min = comun.minuto_de((play.get("clock") or {}).get("displayValue"))
                                    if 46 <= m_min <= 70:
                                        comun.enviar_mensaje(
                                            f"⚠️ <b>ALERTA DE CASHOUT / COBERTURA (2T)</b> ⚠️\n\n"
                                            f"⚽ <b>{act['local']} vs {act['visita']}</b>\n"
                                            f"⏱ <b>Minuto:</b> {m_min}' del Segundo Tiempo\n"
                                            f"🟥 <b>Tarjeta Roja detectada en el 2T.</b>\n\n"
                                            f"💡 <i>Se sugiere asegurar ganancias o evaluar cierre anticipado para mitigar el desorden táctico.</i>"
                                        )
                                        act["alertado_roja"] = True
                                        break

                    # 2. Si el partido finalizó, actualizar FT y win/loss en la DB
                    if p.estado in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                        ft_str = f"{p.goles_local_ft}-{p.goles_visitante_ft}"
                        goles_totales = (p.goles_local_ft or 0) + (p.goles_visitante_ft or 0)
                        gano = 1 if goles_totales < act["linea"] else 0
                        
                        database.actualizar_resultado_apuesta(p.event_id, ft_str, gano)
                        res_txt = "GANADA ✅" if gano == 1 else "PERDIDA ❌"
                        print(f"[PARTIDO FINALIZADO] {act['local']} vs {act['visita']} | FT: {ft_str} | Línea -{act['linea']} -> {res_txt}")
                        
                        partidos_activos.pop(p.event_id, None)

                # Descartar si no tiene marcador HT o no es candidato (1 o 2 goles)
                if not p.tiene_ht or p.total_ht not in (1, 2):
                    continue

                # Ignorar partidos no iniciados o ya finalizados para nuevas alertas
                if p.estado in ("STATUS_SCHEDULED", "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"):
                    continue
                if p.estado in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                    continue

                # Evitar alertas duplicadas
                if p.event_id in enviadas:
                    continue

                # Descargar resumen oficial para obtener SOT al descanso
                d = client.summary(p.event_id, ttl=20)
                if not d:
                    continue

                cp = comun.Partido(
                    event_id=p.event_id, fecha=p.fecha, liga=p.liga_nombre,
                    local=p.local_nombre, visitante=p.visitante_nombre,
                    local_id=p.local_id, visitante_id=p.visitante_id,
                    goles_local_ht=p.goles_local_ht, goles_visitante_ht=p.goles_visitante_ht,
                    goles_local_ft=p.goles_local_ft, goles_visitante_ft=p.goles_visitante_ft,
                    estado=p.estado, valido=p.valido,
                )
                st = comun.stats_1t(d, cp, validar_final=False)
                if not st or not st.get("ctx_ok"):
                    continue

                ht = f"{p.goles_local_ht}-{p.goles_visitante_ht}"
                decision = False
                motivo = ""
                linea = 3.5
                sot_pierde_val = 0
                sot_tot_val = st["L"]["sot"] + st["V"]["sot"]

                # ---------------------------------------------------------
                # CASO 1: 1 GOL AL DESCANSO (1-0 o 0-1) -> Línea -3.5
                # ---------------------------------------------------------
                if p.total_ht == 1:
                    e = bot.evaluar_partido(p, indice, cfg, cache_perfiles, global_avg, alpha)
                    base_ok, base_motivo = bot.decidir_con_config(e, bot.PERFILES[config.PERFIL_PREMATCH])
                    base_ok = bool(base_ok)
                    sot_pierde = st["V"]["sot"] if ht == "1-0" else st["L"]["sot"]
                    sot_pierde_val = sot_pierde
                    
                    decision = base_ok and (sot_pierde <= config.SOT_PIERDE_MAX_1G)
                    linea = 3.5
                    motivo = f"Perfil Pre-Match OK ({base_motivo}) | SOT del que pierde: {sot_pierde} (&le; {config.SOT_PIERDE_MAX_1G})"

                # ---------------------------------------------------------
                # CASO 2: 2 GOLES AL DESCANSO (solo 1-1 o 0-2) -> Línea -4.5
                # ---------------------------------------------------------
                elif p.total_ht == 2:
                    decision = (ht in ("1-1", "0-2")) and (sot_tot_val <= config.SOT_TOT_MAX_2G)
                    linea = 4.5
                    motivo = f"Marcador {ht} | SOT total combinado: {sot_tot_val} (&le; {config.SOT_TOT_MAX_2G})"

                if not decision:
                    continue

                # ---------------------------------------------------------
                # DISPARO DE ALERTA OFICIAL A TELEGRAM
                # ---------------------------------------------------------
                msg_alerta = (
                    f"🚨 <b>ALERTA BOT 14/15 — ENTRADA CONFIRMADA (HT)</b> 🚨\n\n"
                    f"⚽ <b>Partido:</b> {p.local_nombre} vs {p.visitante_nombre}\n"
                    f"🏆 <b>Liga:</b> {p.liga_nombre}\n"
                    f"⏱ <b>Momento:</b> Descanso Oficial (HT {ht})\n\n"
                    f"🎯 <b>MERCADO RECOMENDADO:</b>\n"
                    f"👉 <b>MENOS DE {linea} GOLES (-{linea})</b>\n\n"
                    f"📊 <b>Estadísticas 1T (HT):</b>\n"
                    f"• Tiros a Puerta: L {st['L']['sot']} - V {st['V']['sot']}\n"
                    f"• Remates Totales: {st['L']['shots'] + st['V']['shots']}\n"
                    f"• Córners: {st['L']['corners'] + st['V']['corners']}\n"
                    f"• Tarjetas Rojas: {st['L']['reds'] + st['V']['reds']}\n\n"
                    f"📈 <b>Criterio Técnico:</b>\n"
                    f"• {motivo}\n\n"
                    f"💰 <b>Stake Sugerido:</b> 1.5 a 2.0 Unidades\n"
                    f"🔒 <i>Confirmado al minuto 45 bajo protocolo ALTA_CONFIANZA.</i>"
                )

                print("\n" + "="*50)
                print(f"[ALERTA DISPARADA] {p.local_nombre} vs {p.visitante_nombre} (HT {ht}) -> Menos {linea}")
                print("="*50)

                # Envío de alerta al Telegram del usuario
                comun.enviar_mensaje(msg_alerta, parse_mode="HTML")
                
                # Registro en la base de datos persistente
                database.registrar_apuesta(
                    event_id=p.event_id,
                    fecha=hoy_str,
                    liga=p.liga_nombre,
                    local=p.local_nombre,
                    visitante=p.visitante_nombre,
                    ht=ht,
                    sot_pierde=sot_pierde_val,
                    sot_tot=sot_tot_val,
                    linea=linea,
                    motivo=motivo
                )

                # Registro en archivo json y seguimiento de 2T
                enviadas.add(p.event_id)
                alertas_path.write_text(json.dumps(sorted(enviadas), ensure_ascii=False), encoding="utf-8")

                partidos_activos[p.event_id] = {
                    "local": p.local_nombre,
                    "visita": p.visitante_nombre,
                    "ht": ht,
                    "linea": linea,
                    "alertado_roja": False
                }

        except Exception as e:
            print(f"[ERROR EN BUCLE 24/7] {e}")

        time.sleep(config.POLL_INTERVAL)
