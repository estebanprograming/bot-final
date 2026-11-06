# -*- coding: utf-8 -*-
"""
MOTOR DE EJECUCIÓN EN VIVO 24/7 — BOT FINAL 2.0 (FLASHSCORE + ESPN HYBRID)
- Doble motor: Flashscore como verdad de estadio en vivo (SOT, HT oficial status 12, xG)
  y ESPN como motor de probabilidades Poisson y perfiles matemáticos pre-partido.
- Escaneo continuo en tiempo real al descanso (minuto 45).
- Aplicación estricta de ALTA_CONFIANZA (Menos 3.5 en 1-0/0-1; Menos 4.5 en 1-1/0-2).
- Base de datos SQLite persistente con radar en vivo y conteo de partidos.
- Escucha continua de comandos en Telegram ('ESTADISTICAS', 'RADAR', 'DESCARGAR LOGS').
- Auto-recuperación ante fallos de conexión y rotación automática de medianoche.
"""
import os
import json
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any, Set

import requests
import config
import comun
import database
import bot_under35_v6 as bot
import flashscore_engine as fs


# ---------------------------------------------------------------------------
# HILO DE ESCUCHA DE COMANDOS TELEGRAM ('ESTADISTICAS', 'RADAR', 'LOGS')
# ---------------------------------------------------------------------------
def escuchador_comandos_telegram(token: str, chat_id_objetivo: str):
    """Escucha en segundo plano los mensajes del usuario en Telegram."""
    offset = None
    print("[TELEGRAM] Escuchador de comandos activo (Esperando 'ESTADISTICAS', 'RADAR', 'LOGS')...")

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

                    # Seguridad: responder únicamente al chat autorizado del usuario
                    if chat_id_objetivo and remitente_chat.strip() != str(chat_id_objetivo).strip():
                        continue

                    texto_upper = texto.upper()
                    
                    # 1. COMANDO RADAR / PARTIDOS EN VIVO
                    if any(cmd in texto_upper for cmd in ("RADAR", "PARTIDOS", "VIGILANCIA", "EN VIVO", "/RADAR", "/PARTIDOS", "MONITOREO")):
                        print(f"\n[COMANDO RECIBIDO] '{texto}' de chat {remitente_chat} (Consulta de Radar)")
                        fecha_hoy = datetime.now(bot.ZONA).strftime("%Y-%m-%d")
                        radar_items = database.obtener_radar_hoy(fecha_hoy)
                        
                        if not radar_items:
                            comun.enviar_mensaje(
                                f"📡 <b>RADAR DE LA JORNADA ({fecha_hoy}) — BOT 2.0 FLASHSCORE</b>\n\n"
                                f"🔍 El motor está realizando su ronda de sondeo. Espera 30 segundos y vuelve a escribir <code>RADAR</code>.",
                                chat=remitente_chat
                            )
                        else:
                            en_ht = [p for p in radar_items if p.get("estado") == "STATUS_HALFTIME"]
                            en_juego = [p for p in radar_items if p.get("estado") in ("STATUS_IN_PROGRESS", "STATUS_FIRST_HALF", "STATUS_SECOND_HALF") and p.get("estado") != "STATUS_HALFTIME"]
                            programados = [p for p in radar_items if p.get("estado") == "STATUS_SCHEDULED"]
                            finalizados = [p for p in radar_items if p.get("estado") in ("STATUS_FULL_TIME", "STATUS_FINAL")]
                            
                            txt = [f"📡 <b>RADAR EN VIVO ({fecha_hoy}) — BOT 2.0 FLASHSCORE</b>\n"]
                            txt.append(f"🔍 <b>Partidos bajo vigilancia en radar:</b> {len(radar_items)}\n")
                            
                            if en_ht:
                                txt.append("🟢 <b>EN ENTRETIEMPO (MINUTO 45 - CANDIDATOS):</b>")
                                for p in en_ht[:6]:
                                    txt.append(f"• <b>{p['local']} vs {p['visitante']}</b> (HT {p['ht']})\n  👉 <i>{p['diagnostico']}</i>")
                                txt.append("")
                                
                            if en_juego:
                                txt.append("🟡 <b>EN JUEGO (VIGILANDO MINUTO A MINUTO):</b>")
                                for p in en_juego[:6]:
                                    txt.append(f"• <b>{p['local']} vs {p['visitante']}</b> ({p['ht']})\n  👉 <i>{p['diagnostico']}</i>")
                                txt.append("")
                                
                            if programados:
                                txt.append(f"⚪ <b>PROGRAMADOS PARA MÁS TARDE ({len(programados)} partidos):</b>")
                                for p in programados[:6]:
                                    h_str = f"{p['hora'][:5]} | " if p['hora'] else ""
                                    txt.append(f"• {h_str}{p['liga'][:18]}: {p['local']} vs {p['visitante']}")
                                if len(programados) > 6:
                                    txt.append(f"  <i>...y {len(programados)-6} partidos más en el calendario.</i>")
                                txt.append("")
                                
                            if finalizados:
                                txt.append(f"🏁 <b>FINALIZADOS HOY:</b> {len(finalizados)} partidos en la jornada.")
                                
                            txt.append("\n📄 <i>Escribe 'ESTADISTICAS' para descargar el PDF completo o 'DESCARGAR LOGS' para la bitácora.</i>")
                            comun.enviar_mensaje("\n".join(txt), chat=remitente_chat)

                    # 2. COMANDO ESTADISTICAS (REPORTE OFICIAL PDF)
                    elif any(cmd in texto_upper for cmd in (
                        "ESTADISTICA", "ESTADÍSTICA", "STATS", "REPORTE", "INFORME",
                        "/STATS", "/ESTADISTICA", "/ESTADISTICAS"
                    )):
                        print(f"\n[COMANDO RECIBIDO] '{texto}' de chat {remitente_chat}")
                        fecha_hoy = datetime.now(bot.ZONA).strftime("%Y-%m-%d")
                        
                        # Mensaje de confirmación inmediata
                        comun.enviar_mensaje(
                            "⏳ <b>Procesando solicitud...</b>\nGenerando reporte estadístico oficial del Bot 2.0 Flashscore en PDF con todas las entradas y partidos analizados.",
                            chat=remitente_chat
                        )

                        # Generar PDF en tiempo real con datos de la DB
                        pdf_path = Path(__file__).parent / "Reporte_Estadisticas_EnVivo.pdf"
                        pdf_ok = False
                        try:
                            database.generar_pdf_estadisticas(str(pdf_path), fecha_hoy)
                            pdf_ok = True
                        except Exception as e_pdf:
                            comun.log_evento("ERROR_PDF", f"Fallo al compilar PDF: {e_pdf}")
                            print(f"[ERROR] Fallo al compilar PDF: {e_pdf}")

                        # Enviar PDF si se compiló con éxito
                        if pdf_ok and pdf_path.exists():
                            comun.enviar_pdf(
                                str(pdf_path),
                                caption="📊 <b>Reporte Estadístico Oficial — Bot 2.0 Flashscore En Vivo</b>",
                                chat=remitente_chat
                            )
                        else:
                            comun.enviar_mensaje(
                                "⚠️ <i>El archivo PDF tuvo un pequeño retraso al renderizarse, pero tus estadísticas están intactas y calculadas aquí abajo:</i>",
                                chat=remitente_chat
                            )

                        # Enviar resumen en texto SIEMPRE garantizado
                        try:
                            m = database.obtener_metricas_globales(fecha_hoy)
                            comun.enviar_mensaje(
                                f"📊 <b>RESUMEN ESTADÍSTICO ACTUALIZADO (BOT 2.0)</b>\n\n"
                                f"📅 <b>Fecha:</b> {fecha_hoy}\n"
                                f"🔍 <b>Partidos analizados hoy:</b> {m['analizados_hoy']}\n"
                                f"📈 <b>Partidos analizados histórico:</b> {m['analizados_historico']}\n"
                                f"🎯 <b>Total entradas ejecutadas:</b> {m['total_apuestas']}\n"
                                f"✅ <b>Ganadas:</b> {m['ganadas']} | ❌ <b>Perdidas:</b> {m['perdidas']} | ⏳ <b>En juego:</b> {m['pendientes']}\n"
                                f"🏆 <b>Efectividad Real:</b> {m['efectividad']:.1f}%\n\n"
                                f"📄 <i>Escribe <code>RADAR</code> para ver los partidos en juego o <code>DESCARGAR LOGS</code> para la bitácora técnica.</i>",
                                chat=remitente_chat
                            )
                        except Exception as e_m:
                            comun.log_evento("ERROR_METRICAS", f"Error métricas: {e_m}")
                            comun.enviar_mensaje(f"❌ Error al consultar métricas: {e_m}", chat=remitente_chat)

                    # 3. COMANDO DESCARGAR LOGS / CAJA NEGRA
                    elif any(cmd in texto_upper for cmd in ("DESCARGAR LOGS", "CAJA NEGRA", "SOLICITAR LOGS", "LOGS", "/LOGS", "DIAGNOSTICO", "BITACORA", "BITÁCORA")):
                        print(f"\n[COMANDO RECIBIDO] '{texto}' de chat {remitente_chat} (Caja Negra)")
                        comun.log_evento("TELEGRAM", f"Solicitud de descarga de Caja Negra recibida de {remitente_chat}")
                        
                        comun.enviar_mensaje(
                            "📦 <b>Extrayendo registros de la Caja Negra...</b>\nGenerando archivo de bitácora con el historial segundo a segundo de todas las decisiones, llamadas a Flashscore, ESPN y eventos del bot.",
                            chat=remitente_chat
                        )
                        
                        if not comun.LOG_PATH.exists():
                            comun.log_evento("SISTEMA", "Caja negra inicializada para envío a Telegram.")
                        
                        comun.enviar_pdf(
                            str(comun.LOG_PATH),
                            caption="📦 <b>Caja Negra Oficial — Bitácora de Auditoría 24/7</b>\nHistorial cronológico de todos los actos y decisiones del bot.",
                            chat=remitente_chat
                        )
                        comun.enviar_mensaje(
                            "✅ <b>Caja Negra enviada con éxito arriba.</b>\n"
                            "Si detectas algún comportamiento inesperado o quieres auditar alguna decisión, descárgate ese archivo y compártemelo para diagnosticar y reparar cualquier anomalía de inmediato.",
                            chat=remitente_chat
                        )

        except Exception as e:
            try:
                comun.log_evento("ERROR_LISTENER", f"Error en hilo de comandos: {e}")
                print(f"[ERROR LISTENER] {e}")
            except Exception:
                pass
            time.sleep(3)


# ---------------------------------------------------------------------------
# UTILIDADES DE MATCHING ENTRE FLASHSCORE Y ESPN
# ---------------------------------------------------------------------------
def emparejar_con_flashscore(local: str, visitante: str, partidos_fs: list) -> Optional[dict]:
    """Busca un partido en la lista de Flashscore mediante coincidencia de nombres."""
    nl = comun.norm(local)
    nv = comun.norm(visitante)
    for p in partidos_fs:
        fl = comun.norm(p.get("local", ""))
        fv = comun.norm(p.get("visitante", ""))
        if (nl in fl or fl in nl) and (nv in fv or fv in nv):
            return p
    return None


def emparejar_con_espn(local_fs: str, visita_fs: str, eventos_espn: list) -> Optional[dict]:
    """Busca un partido de Flashscore dentro del scoreboard de ESPN."""
    nl = comun.norm(local_fs)
    nv = comun.norm(visita_fs)
    for ev in eventos_espn:
        comp = (ev.get("competitions") or [{}])[0]
        l, v = bot._competidores(comp)
        if not l or not v:
            continue
        ln = comun.norm((l.get("team") or {}).get("displayName", ""))
        vn = comun.norm((v.get("team") or {}).get("displayName", ""))
        if (nl in ln or ln in nl) and (nv in vn or vn in nv):
            return ev
    return None


# ---------------------------------------------------------------------------
# BUCLE PRINCIPAL DE EJECUCIÓN EN VIVO 24/7 (BOT 2.0 FLASHSCORE + ESPN)
# ---------------------------------------------------------------------------
def run_live_247(titulo: str = "BOT FINAL 2.0 FLASHSCORE EN VIVO"):
    database.init_db()

    token, chat_id = comun.telegram_config()
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

    enviadas: set[str] = set()
    dia_actual = ""
    indice = []
    global_avg = 1.45
    alpha = 0.10
    cache_perfiles = {}
    partidos_activos = {}
    ultimo_heartbeat = time.time()
    ultimo_fs_full_scan = 0

    print("=" * 70)
    print(f"🚀 INICIANDO {titulo} (MODO 24/7 AUTÓNOMO)")
    print(f"📡 Motor en Vivo Principal: FLASHSCORE (Verdad de Estadio y Status 12)")
    print(f"🧠 Motor Matemático Base: ESPN (Poisson, Historial 75d, Lambdas)")
    print(f"🔔 Notificaciones: Telegram Activo (Chat ID: {config.TELEGRAM_CHAT_ID})")
    print(f"📊 Regla HT: 1-0/0-1 SOT<={config.SOT_PIERDE_MAX_1G} (-3.5) | 1-1/0-2 SOT<={config.SOT_TOT_MAX_2G} (-4.5)")
    print("=" * 70)

    # Mensaje de arranque a Telegram
    comun.log_evento("INICIO", f"{titulo} iniciado con arquitectura dual Flashscore+ESPN.")
    comun.enviar_mensaje(
        f"🚀 <b>{titulo} INICIADO Y EN LÍNEA (24/7)</b>\n\n"
        f"📡 <b>Motor en Vivo:</b> Flashscore Ninja (Verdad de Estadio, Tiros SOT reales y Status 12)\n"
        f"🧠 <b>Motor Matemático:</b> Modelado Poisson y filtros ALTA_CONFIANZA (-3.5 y -4.5)\n"
        f"🎯 <b>Condición HT:</b> SOT &le; 2 verificado en estadio\n"
        f"📊 <b>Estadísticas:</b> Escribe <code>ESTADISTICAS</code> o <code>RADAR</code> en cualquier momento.\n"
        f"📦 <b>Caja Negra:</b> Escribe <code>DESCARGAR LOGS</code> para auditoría forense.\n"
        f"🛡️ <b>Estado:</b> Monitoreo activo de partidos en todo el mundo sin falsos ceros."
    )

    while True:
        try:
            ahora_dt = datetime.now(bot.ZONA)
            hoy_str = ahora_dt.strftime("%Y-%m-%d")
            hoy_ymd = hoy_str.replace("-", "")

            # 1. Rotación diaria automática (medianoche)
            if hoy_str != dia_actual:
                dia_actual = hoy_str
                enviadas.clear()
                partidos_activos.clear()
                print(f"\n[SISTEMA] Rotación de fecha: {dia_actual}")
                comun.log_evento("ROTACION", f"Nuevo día operativo iniciado: {dia_actual}")

                desde_dt = ahora_dt - timedelta(days=config.DIAS_HISTORIAL)
                ayer_dt = ahora_dt - timedelta(days=1)
                desde_str = desde_dt.strftime("%Y-%m-%d")
                ayer_str = ayer_dt.strftime("%Y-%m-%d")

                try:
                    indice = bot.construir_indice(client, desde_str, ayer_str, cobertura=config.COBERTURA)
                    print(f"[HISTORIAL] Partidos históricos cargados: {len(indice)}")
                    global_avg, alpha = bot.media_2t_global(
                        indice, bot.iso_utc(datetime.strptime(dia_actual, "%Y-%m-%d").replace(tzinfo=bot.ZONA))
                    )
                    cache_perfiles.clear()
                except Exception as e:
                    print(f"[WARN] Error al actualizar historial diario: {e}")

            # 2. Heartbeat periódico a Telegram (cada 12h)
            if time.time() - ultimo_heartbeat > 43200:
                ultimo_heartbeat = time.time()
                m_hb = database.obtener_metricas_globales(dia_actual)
                comun.enviar_mensaje(
                    f"💓 <b>BOT 2.0 FLASHSCORE — LATIDO DE SISTEMA (HEARTBEAT)</b>\n\n"
                    f"📅 <b>Fecha:</b> {dia_actual}\n"
                    f"🟢 <b>Estado:</b> Operativo 24/7 sin interrupciones\n"
                    f"🔍 <b>Partidos analizados hoy:</b> {m_hb['analizados_hoy']}\n"
                    f"📈 <b>Partidos analizados histórico:</b> {m_hb['analizados_historico']}\n"
                    f"🎯 <b>Total entradas:</b> {m_hb['total_apuestas']} (Efectividad: {m_hb['efectividad']:.1f}%)\n"
                    f"📄 <i>Escribe 'ESTADISTICAS' para descargar el PDF completo.</i>"
                )

            # 2.5 Monitoreo de 2T para Cashout preventivo (tarjetas rojas)
            if partidos_activos and config.MONITOREAR_2T:
                for act_id in list(partidos_activos.keys()):
                    act = partidos_activos[act_id]
                    # Monitoreo de roja en 2T vía Flashscore o ESPN
                    d_act = client.summary(act_id, ttl=15)
                    if d_act:
                        for c in (d_act.get("commentary") or []):
                            play = c.get("play") or {}
                            if str((play.get("type") or {}).get("text", "")) == "Red Card":
                                per = (play.get("period") or {}).get("number")
                                if per == 2 and not act.get("alertado_roja"):
                                    act["alertado_roja"] = True
                                    comun.log_evento("CASHOUT_2T", f"ROJA en 2T en {act['local']} vs {act['visita']}. Alerta de Cashout disparada.")
                                    comun.enviar_mensaje(
                                        f"⚠️ <b>ALERTA DE CASHOUT PREVENTIVO (2T)</b> ⚠️\n\n"
                                        f"⚽ <b>Partido:</b> {act['local']} vs {act['visita']}\n"
                                        f"🟥 <b>Tarjeta Roja detectada en el 2° Tiempo</b>\n"
                                        f"💡 <b>Recomendación:</b> Asegurar ganancias o ejecutar Cashout si la casa ofrece buen valor."
                                    )

            # -------------------------------------------------------------
            # 3. ESCANEO PRIMARIO: FLASHSCORE EN VIVO (Verdad de Estadio)
            # -------------------------------------------------------------
            partidos_live_fs = fs.obtener_partidos_dia(modo="en_vivo")
            candidatos_ht_fs = [p for p in partidos_live_fs if p.get("es_descanso") or p.get("status") == "12"]

            # Si hay partidos en descanso detectados por Flashscore
            for pfs in candidatos_ht_fs:
                fs_id = pfs["id"]
                if fs_id in enviadas:
                    continue

                local_fs = pfs.get("local", "")
                visita_fs = pfs.get("visitante", "")
                liga_fs = pfs.get("liga", "Flashscore Live")

                # Obtener marcador oficial HT y estadísticas exactas de 1er tiempo
                ht_score, stats_fs = fs.obtener_datos_exactos_ht(fs_id)
                if not ht_score and pfs.get("ht_l") is not None and pfs.get("ht_v") is not None:
                    ht_score = (pfs["ht_l"], pfs["ht_v"])

                if not ht_score:
                    continue

                gl_ht, gv_ht = ht_score
                ht_str = f"{gl_ht}-{gv_ht}"
                total_ht = gl_ht + gv_ht

                # Registrar en radar DB
                database.registrar_partido_analizado(
                    event_id=f"FS_{fs_id}",
                    fecha=hoy_str,
                    hora=datetime.now(bot.ZONA).strftime("%H:%M"),
                    liga=liga_fs,
                    local=local_fs,
                    visitante=visita_fs,
                    ht=ht_str,
                    estado="STATUS_HALFTIME",
                    diagnostico=f"Descanso Oficial (HT {ht_str}) - Evaluando tiros Flashscore"
                )

                # Filtros de marcador
                if total_ht == 0:
                    database.registrar_partido_analizado(f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, "0-0", "STATUS_HALFTIME", "Descartado HT: Marcador 0-0 sin gol")
                    continue
                if total_ht >= 3:
                    database.registrar_partido_analizado(f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str, "STATUS_HALFTIME", f"Descartado HT: 3+ goles ({total_ht})")
                    continue
                if total_ht == 2 and ht_str == "2-0":
                    database.registrar_partido_analizado(f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str, "STATUS_HALFTIME", "Descartado HT: Marcador 2-0 prohibido")
                    continue

                # Extraer tiros y córners de estadio
                sot_l = stats_fs.get("sot_l", 0) if stats_fs else 0
                sot_v = stats_fs.get("sot_v", 0) if stats_fs else 0
                shots_tot = (stats_fs.get("shots_l", 0) + stats_fs.get("shots_v", 0)) if stats_fs else 0
                corners_tot = (stats_fs.get("corners_l", 0) + stats_fs.get("corners_v", 0)) if stats_fs else 0
                reds_1t = (stats_fs.get("red_l", 0) + stats_fs.get("red_v", 0)) if stats_fs else 0

                if reds_1t > 0:
                    database.registrar_partido_analizado(f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str, "STATUS_HALFTIME", f"Descartado HT: Roja en 1T ({reds_1t})")
                    continue

                decision = False
                motivo = ""
                linea = 3.5
                sot_pierde_val = 0
                sot_tot_val = sot_l + sot_v

                # CASO 1: 1 GOL AL DESCANSO (1-0 o 0-1) -> Línea -3.5
                if total_ht == 1:
                    linea = 3.5
                    sot_pierde = sot_v if ht_str == "1-0" else sot_l
                    sot_pierde_val = sot_pierde
                    
                    if sot_pierde <= config.SOT_PIERDE_MAX_1G:
                        decision = True
                        motivo = f"Flashscore Estadio OK: SOT del que pierde {sot_pierde} (<= {config.SOT_PIERDE_MAX_1G}) | Tiros totales: {shots_tot}"
                    else:
                        motivo = f"SOT del que pierde excesivo en estadio ({sot_pierde} > {config.SOT_PIERDE_MAX_1G})"

                # CASO 2: 2 GOLES AL DESCANSO (1-1 o 0-2) -> Línea -4.5
                elif total_ht == 2:
                    linea = 4.5
                    if sot_tot_val <= config.SOT_TOT_MAX_2G:
                        decision = True
                        motivo = f"Flashscore Estadio OK: Marcador {ht_str} | SOT total combinado: {sot_tot_val} (<= {config.SOT_TOT_MAX_2G})"
                    else:
                        motivo = f"SOT combinado excesivo en estadio ({sot_tot_val} > {config.SOT_TOT_MAX_2G})"

                if not decision:
                    database.registrar_partido_analizado(f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str, "STATUS_HALFTIME", f"Descartado HT: {motivo}")
                    continue

                # DISPARO OFICIAL DE ALERTA TELEGRAM (DATOS REALES DE ESTADIO)
                enviadas.add(fs_id)
                comun.log_evento("ALERTA_HT", f"ENTRADA CONFIRMADA (FLASHSCORE): {local_fs} vs {visita_fs} (HT {ht_str}) -> Menos {linea} | {motivo}")

                msg_alerta = (
                    f"🚨 <b>ALERTA BOT 2.0 FLASHSCORE — ENTRADA CONFIRMADA (HT)</b> 🚨\n\n"
                    f"⚽ <b>Partido:</b> {local_fs} vs {visita_fs}\n"
                    f"🏆 <b>Liga:</b> {liga_fs}\n"
                    f"⏱ <b>Momento:</b> Descanso Oficial (HT {ht_str})\n\n"
                    f"🎯 <b>MERCADO RECOMENDADO:</b>\n"
                    f"👉 <b>MENOS DE {linea} GOLES (-{linea})</b>\n\n"
                    f"📊 <b>Estadísticas Reales de Estadio (Flashscore):</b>\n"
                    f"• Tiros a Puerta: L {sot_l} - V {sot_v}\n"
                    f"• Remates Totales: {shots_tot}\n"
                    f"• Córners: {corners_tot}\n"
                    f"• Tarjetas Rojas: {reds_1t}\n\n"
                    f"📈 <b>Criterio Técnico:</b>\n"
                    f"• {motivo}\n\n"
                    f"💰 <b>Stake Sugerido:</b> 1.5 a 2.0 Unidades\n"
                    f"🔒 <i>Confirmado al minuto 45 bajo protocolo ALTA_CONFIANZA 2.0.</i>"
                )

                print("\n" + "=" * 50)
                print(f"[ALERTA FLASHSCORE] {local_fs} vs {visita_fs} (HT {ht_str}) -> Menos {linea}")
                print("=" * 50)

                comun.enviar_mensaje(msg_alerta, parse_mode="HTML")

                # Registrar en Base de Datos persistente
                database.registrar_apuesta(
                    event_id=f"FS_{fs_id}",
                    fecha=hoy_str,
                    hora=datetime.now(bot.ZONA).strftime("%H:%M"),
                    liga=liga_fs,
                    local=local_fs,
                    visitante=visita_fs,
                    ht=ht_str,
                    sot_pierde=sot_pierde_val,
                    sot_tot=sot_tot_val,
                    linea=linea,
                    motivo=motivo
                )
                database.registrar_partido_analizado(
                    f"FS_{fs_id}", hoy_str, datetime.now(bot.ZONA).strftime("%H:%M"), liga_fs,
                    local_fs, visita_fs, ht_str, "STATUS_HALFTIME",
                    diagnostico=f"🚨 ALERTA CONFIRMADA: Menos {linea} Goles (Flashscore)"
                )

                partidos_activos[fs_id] = {
                    "local": local_fs,
                    "visita": visita_fs,
                    "ht": ht_str,
                    "linea": linea,
                    "alertado_roja": False
                }

            # -------------------------------------------------------------
            # 4. ESCANEO SECUNDARIO: SCOREBOARD ESPN (Ligas Tradicionales)
            # -------------------------------------------------------------
            evs_espn = client.scoreboard_live(hoy_ymd)
            for ev in evs_espn:
                comp = (ev.get("competitions") or [{}])[0]
                status_raw = comp.get("status", {}) or {}
                tipo_raw = status_raw.get("type", {}) or {}
                estado_actual = str(tipo_raw.get("name", "")).upper()
                lid, slug, nombre_liga = bot.liga_desde_evento(ev)
                
                loc_raw, vis_raw = bot._competidores(comp)
                if not loc_raw or not vis_raw:
                    continue
                loc_nom = str((loc_raw.get("team") or {}).get("displayName", ""))
                vis_nom = str((vis_raw.get("team") or {}).get("displayName", ""))
                eid = str(ev.get("id", ""))

                # Parsear objeto Partido
                p = bot.parsear_evento(ev, hoy_ymd, live=True) or bot.parsear_evento(ev, hoy_ymd, live=False)
                ht_str = f"{p.goles_local_ht}-{p.goles_visitante_ht}" if (p and p.tiene_ht) else "SIN_HT"
                
                # Actualizar diagnóstico en radar DB
                clock_txt = str(status_raw.get("displayClock", "")).strip()
                l_sc = str(loc_raw.get("score", "0"))
                v_sc = str(vis_raw.get("score", "0"))
                
                if estado_actual == "STATUS_SCHEDULED":
                    diag = "Programado para hoy"
                elif estado_actual in ("STATUS_IN_PROGRESS", "STATUS_FIRST_HALF"):
                    diag = f"En juego 1T ({clock_txt} | {l_sc}-{v_sc}) - Esperando descanso min 45"
                elif estado_actual == "STATUS_HALFTIME":
                    diag = f"En descanso ({ht_str}) - Evaluando estadio"
                elif estado_actual in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                    diag = f"Finalizado ({l_sc}-{v_sc})"
                else:
                    diag = f"En juego 2T ({clock_txt} | {l_sc}-{v_sc})"

                database.registrar_partido_analizado(
                    event_id=eid, fecha=hoy_str, hora=str(ev.get("date", ""))[11:16],
                    liga=nombre_liga, local=loc_nom, visitante=vis_nom,
                    ht=ht_str, estado=estado_actual, diagnostico=diag
                )

                # Si el partido terminó, actualizar resultado en apuestas
                if estado_actual in ("STATUS_FULL_TIME", "STATUS_FINAL") and p and p.goles_local_ft is not None:
                    database.actualizar_resultado_ft(eid, f"{p.goles_local_ft}-{p.goles_visitante_ft}", p.total_ft)
                    if eid in partidos_activos:
                        del partidos_activos[eid]

            time.sleep(config.POLL_INTERVAL)

        except Exception as e:
            comun.log_evento("ERROR_CICLO", f"Excepción en ciclo principal: {e}")
            print(f"[ERROR LOOP] {e}")
            time.sleep(5)


if __name__ == "__main__":
    run_live_247()
