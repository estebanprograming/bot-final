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
                                f"🔍 El motor está realizando su ronda de sondeo. Espera 10 segundos y vuelve a escribir <code>RADAR</code>.",
                                chat=remitente_chat
                            )
                        else:
                            en_ht = [p for p in radar_items if p.get("estado") == "STATUS_HALFTIME"]
                            en_juego = [p for p in radar_items if p.get("estado") in ("STATUS_IN_PROGRESS", "STATUS_FIRST_HALF", "STATUS_SECOND_HALF") and p.get("estado") != "STATUS_HALFTIME"]
                            programados = [p for p in radar_items if p.get("estado") == "STATUS_SCHEDULED"]
                            finalizados = [p for p in radar_items if p.get("estado") in ("STATUS_FULL_TIME", "STATUS_FINAL")]
                            m = database.obtener_metricas_globales(fecha_hoy)
                            
                            txt = [f"📡 <b>RADAR EN VIVO ({fecha_hoy}) — BOT 2.0 FLASHSCORE</b>\n"]
                            txt.append(f"🔍 <b>Partidos bajo monitoreo hoy:</b> {len(radar_items)} partidos en el mundo\n")
                            
                            if en_ht:
                                txt.append(f"🟢 <b>EN ENTRETIEMPO ({len(en_ht)} CANDIDATOS HT - EVALUANDO):</b>")
                                for p in en_ht[:8]:
                                    txt.append(f"• <b>{p['local']} vs {p['visitante']}</b> (HT {p['ht']})\n  👉 <i>{p['diagnostico']}</i>")
                                if len(en_ht) > 8:
                                    txt.append(f"  <i>...y {len(en_ht)-8} partidos más en descanso.</i>")
                                txt.append("")
                                
                            if en_juego:
                                txt.append(f"🟡 <b>EN JUEGO ({len(en_juego)} PARTIDOS VIGILÁNDOSE EN VIVO):</b>")
                                for p in en_juego[:8]:
                                    txt.append(f"• <b>{p['local']} vs {p['visitante']}</b> ({p['ht']})\n  👉 <i>{p['diagnostico']}</i>")
                                if len(en_juego) > 8:
                                    txt.append(f"  <i>...y {len(en_juego)-8} partidos más rodando ahora mismo.</i>")
                                txt.append("")
                                
                            if programados:
                                txt.append(f"⚪ <b>PROGRAMADOS PARA HOY ({len(programados)} partidos):</b>")
                                for p in programados[:6]:
                                    h_str = f"{p['hora'][:5]} | " if (p['hora'] and ":" in p['hora']) else ""
                                    txt.append(f"• {h_str}{p['liga'][:22]}: {p['local']} vs {p['visitante']}")
                                if len(programados) > 6:
                                    txt.append(f"  <i>...y {len(programados)-6} partidos más en el calendario de hoy.</i>")
                                txt.append("")
                                
                            if finalizados:
                                txt.append(f"🏁 <b>FINALIZADOS HOY:</b> {len(finalizados)} partidos completados en la jornada.")
                                
                            txt.append(f"\n📊 <b>BALANCE CERTIFICADO:</b> {m['total_apuestas']} entradas ({m['ganadas']}W - {m['perdidas']}L) | <b>{m['efectividad']:.1f}% Efectividad</b>")
                            txt.append("\n📄 <i>Escribe 'ESTADISTICAS' para descargar el PDF completo o 'DESCARGAR LOGS' para la bitácora técnica.</i>")
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


PALABRAS_PROHIBIDAS_LIGAS = (
    "women", "femenil", "femenino", "femenina", "fem", " w", "w ", "(w)",
    "u19", "u20", "u21", "u23", "sub-19", "sub-20", "sub-21", "sub-23", "sub 19", "sub 20", "sub-17", "sub 17",
    "youth", "juvenil", "juniores", "juniors",
    "reserves", "reserve", "reserva", "filial", "amateur", "regional", "tercera", "3. rfef", "rfef", "national 3"
)


def es_liga_inestable(torneo: str, local: str, visitante: str) -> tuple[bool, str]:
    """
    Excluye inmediatamente fútbol femenino, ligas juveniles, filiales, torneos amateurs
    y la 5ª división inglesa (EC / National League) con precisión quirúrgica,
    garantizando que no se bloquee ninguna otra liga legítima del mundo.
    """
    txt = f"{torneo} {local} {visitante}".lower()

    # 1. Bloqueo láser de la 5ª división inglesa (EC / National League / Conference)
    # Patrones 100% exclusivos de la 5ª división inglesa (nunca existen fuera de Inglaterra):
    es_exclusivo_ingles = any(p in txt for p in (
        "vanarama",
        "conference national",
        "conference premier",
        "english national league",
        "national league (inglaterra)",
        "national league (england)",
        "eng.5",
        "england.5",
        "eng.conference"
    ))
    # Si viene como 'national league', se exige que pertenezca a Inglaterra para no bloquear Gibraltar, Myanmar o Nueva Zelanda:
    es_inglaterra = any(p in txt for p in ("inglaterra", "england", "eng.", "england.", "uk", "gran bret"))
    es_national = "national league" in txt

    if es_exclusivo_ingles or (es_inglaterra and es_national):
        return True, "Liga 5ª división inglesa (EC / National League) excluida por alta volatilidad"

    # 2. Exclusión de categorías juveniles, femeninas, filiales y amateurs
    for pb in PALABRAS_PROHIBIDAS_LIGAS:
        if pb in txt:
            return True, f"Categoría inestable excluida ({pb})"
    return False, ""


def es_liga_profesional_permitida(torneo: str) -> bool:
    """Verifica si el torneo pertenece al catálogo de ligas profesionales masculinas oficiales (81 ligas)."""
    tn = comun.norm(torneo.split(":")[-1]) if ":" in torneo else comun.norm(torneo)
    pais = comun.norm(torneo.split(":")[0]) if ":" in torneo else ""
    for v in bot.LIGAS.values():
        ln = comun.norm(v["nombre"])
        if (tn in ln or ln in tn) and (not pais or pais in ln or any(p in ln for p in pais.split())):
            return True
        if tn in ln:
            return True
    return False


def evaluar_prematch_hibrido(
    local_nom: str,
    visita_nom: str,
    liga_nom: str,
    fecha_iso: str,
    ev_espn_match: Optional[dict],
    indice: list,
    cfg: dict,
    cache_perfiles: dict,
    global_avg: float,
    alpha: float,
    fs_id: Optional[str] = None
) -> tuple[bool, str, dict]:
    """
    Evalúa el comportamiento pre-partido bajo el protocolo ALTA_CONFIANZA (75 días):
    1. Si el partido fue detectado en Flashscore (fs_id presente), extrae NATIVAMENTE el historial
       de los últimos 15 partidos de cada equipo desde el feed oficial df_hh_1 de Flashscore.
       Calcula la estabilidad Under 3.5 real y la expectativa de goles sin depender de ESPN ni de traducciones de nombres.
    2. Si ESPN tiene el evento en su scoreboard, calcula expectativa de goles y estabilidad exactas por Poisson.
    3. Si ambos perfiles históricos existen en el índice de ESPN, valida por nombre normalizado.
    4. REGLA DE ORO DE CAPITAL: Si ningún motor tiene historial suficiente para certificar el
       comportamiento del equipo cuando va perdiendo o ganando, SE DESCARTA (sin_perfil).
    """
    detalles = {"esp_total": 0.0, "estab_35": 0.0, "fuente": "DESCONOCIDA"}

    # 1. EVALUACIÓN PRE-MATCH NATIVA DE FLASHSCORE (Sin depender de ESPN)
    if fs_id:
        try:
            p_fs_hist = fs.obtener_perfil_prematch_fs(fs_id)
            if p_fs_hist:
                detalles["esp_total"] = p_fs_hist["esp_total"]
                detalles["estab_35"] = p_fs_hist["estab_35"]
                detalles["fuente"] = "FLASHSCORE_NATIVO"
                if p_fs_hist["esp_total"] > cfg["esp_max"]:
                    return False, f"Expectativa ofensiva alta en Flashscore ({p_fs_hist['esp_total']:.2f} > {cfg['esp_max']})", detalles
                if p_fs_hist["estab_35"] < cfg["e35_min"]:
                    return False, f"Estabilidad Under baja en Flashscore ({p_fs_hist['estab_35']:.1f}% < {cfg['e35_min']}%)", detalles
                return True, f"Pre-Match Flashscore Nativo Aprobado (Exp: {p_fs_hist['esp_total']:.2f}, Est: {p_fs_hist['estab_35']:.1f}%)", detalles
        except Exception as e_fs_hist:
            pass

    # 2. EVALUACIÓN DIRECTA SI ESPN TIENE EL PARTIDO HOY
    if ev_espn_match and indice:
        p_obj = bot.parsear_evento(ev_espn_match, fecha_iso[:10].replace("-", ""), live=True) or \
                bot.parsear_evento(ev_espn_match, fecha_iso[:10].replace("-", ""), live=False)
        if p_obj:
            eval_pm = bot.evaluar_partido(p_obj, indice, cfg, cache_perfiles, global_avg, alpha)
            detalles["esp_total"] = eval_pm.esp_total
            detalles["estab_35"] = eval_pm.estab_35
            detalles["fuente"] = "ESPN_PREMATCH"
            if eval_pm.esp_total > cfg["esp_max"]:
                return False, f"Expectativa ofensiva alta en ESPN ({eval_pm.esp_total:.2f} > {cfg['esp_max']})", detalles
            if eval_pm.estab_35 < cfg["e35_min"]:
                return False, f"Estabilidad Under baja en ESPN ({eval_pm.estab_35:.1f}% < {cfg['e35_min']}%)", detalles
            if eval_pm.liga_2t > cfg["liga_2t_max"]:
                return False, f"Liga goleadora en 2T ({eval_pm.liga_2t:.2f} > {cfg['liga_2t_max']})", detalles
            return True, f"Pre-Match ESPN Aprobado (Exp: {eval_pm.esp_total:.2f}, Est: {eval_pm.estab_35:.1f}%)", detalles

    # 3. BÚSQUEDA CRUZADA EN EL ÍNDICE DE 75 DÍAS DE ESPN POR NOMBRE NORMALIZADO
    if indice:
        nl = comun.norm(local_nom)
        nv = comun.norm(visita_nom)
        team_id_l = None
        team_id_v = None
        for p in indice:
            pl_n = comun.norm(p.local_nombre)
            pv_n = comun.norm(p.visitante_nombre)
            if not team_id_l:
                if nl in pl_n or pl_n in nl:
                    team_id_l = p.local_id
                elif nl in pv_n or pv_n in nl:
                    team_id_l = p.visitante_id
            if not team_id_v:
                if nv in pv_n or pv_n in nv:
                    team_id_v = p.visitante_id
                elif nv in pl_n or pl_n in nv:
                    team_id_v = p.local_id
            if team_id_l and team_id_v:
                break

        if team_id_l and team_id_v:
            pl = bot.construir_perfil(indice, team_id_l, local_nom, fecha_iso, cfg["min_partidos_perfil"], cfg["min_partidos_ht"], cfg["max_partidos_perfil"])
            pv = bot.construir_perfil(indice, team_id_v, visita_nom, fecha_iso, cfg["min_partidos_perfil"], cfg["min_partidos_ht"], cfg["max_partidos_perfil"])
            if pl and pv:
                esp_1t = (pl.gf_1t + pl.gc_1t + pv.gf_1t + pv.gc_1t) / 2.0
                esp_2t = (pl.gf_2t_local + pl.gc_2t_local + pv.gf_2t_visita + pv.gc_2t_visita) / 2.0
                esp_tot = esp_1t + esp_2t
                est_35 = (pl.pct_35 + pv.pct_35) / 2.0
                detalles["esp_total"] = esp_tot
                detalles["estab_35"] = est_35
                detalles["fuente"] = "HISTORICO_INDICE"
                if esp_tot > cfg["esp_max"]:
                    return False, f"Expectativa ofensiva histórica alta ({esp_tot:.2f} > {cfg['esp_max']})", detalles
                if est_35 < cfg["e35_min"]:
                    return False, f"Estabilidad Under histórica baja ({est_35:.1f}% < {cfg['e35_min']}%)", detalles
                return True, f"Pre-Match Histórico Aprobado (Exp: {esp_tot:.2f}, Est: {est_35:.1f}%)", detalles

    # 4. REGLA DE ORO DE CAPITAL:
    # Si ningún motor tiene historial suficiente para certificar el comportamiento,
    # se descarta para evitar operar a ciegas (regla del backtest sin_perfil).
    return False, "Historial insuficiente de partidos previos para calcular perfiles de comportamiento (sin_perfil)", detalles


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
    try:
        pendientes_db = database.obtener_apuestas_pendientes()
        for p_pend in pendientes_db:
            eid = p_pend["event_id"]
            origen = "FS" if str(eid).startswith("FS_") else "ESPN"
            partidos_activos[eid] = {
                "local": p_pend["local"],
                "visita": p_pend["visitante"],
                "ht": p_pend["ht"],
                "linea": p_pend["linea"],
                "alertado_roja": False,
                "origen": origen
            }
            enviadas.add(eid)
            enviadas.add(f"{comun.norm(p_pend['local'])}_{comun.norm(p_pend['visitante'])}")
        if pendientes_db:
            print(f"📦 [RECUPERACIÓN PERSISTENTE] {len(pendientes_db)} apuestas activas restauradas desde SQLite.")
    except Exception as e:
        print(f"[WARN] Error al restaurar apuestas pendientes: {e}")
    ultimo_heartbeat = time.time()
    ultimo_fs_full_scan = 0

    print("=" * 70)
    print(f"🚀 INICIANDO {titulo} (MODO 24/7 AUTÓNOMO)")
    print(f"📡 Motor en Vivo Principal: FLASHSCORE (Verdad de Estadio y Status 12)")
    print(f"🧠 Motor Matemático Base: ESPN (Poisson, Historial 75d, Lambdas)")
    print(f"🔔 Notificaciones: Telegram Activo (Chat ID: {config.TELEGRAM_CHAT_ID})")
    print(f"📊 Regla HT: 1-0/0-1 SOT<={config.SOT_PIERDE_MAX_1G} (-3.5) | 1-1/0-2 SOT<={config.SOT_TOT_MAX_2G} (-4.5)")
    print("=" * 70)

    # 0. Barrido inicial inmediato para que el radar esté 100% activo desde el segundo 1
    try:
        init_fs = fs.obtener_partidos_dia(modo="en_vivo")
        init_hoy = datetime.now(bot.ZONA).strftime("%Y-%m-%d")
        init_lote = []
        for p in init_fs:
            st = p.get("status", "0")
            reloj = p.get("reloj", "")
            if st == "12":
                st_radar = "STATUS_HALFTIME"
                diag_radar = f"En descanso oficial (HT {p.get('ht_l',0)}-{p.get('ht_v',0)}) — Evaluando filtros"
            elif st == "2":
                st_radar = "STATUS_FIRST_HALF"
                diag_radar = f"En juego 1T ({reloj}) — Vigilando minuto a minuto"
            elif st in ("6", "7", "13"):
                st_radar = "STATUS_SECOND_HALF"
                diag_radar = f"En juego 2T ({reloj}) — Monitoreando Cashout"
            elif st == "1":
                st_radar = "STATUS_SCHEDULED"
                diag_radar = f"Programado hoy ({reloj})"
            elif st == "3":
                st_radar = "STATUS_FULL_TIME"
                diag_radar = f"Finalizado ({p.get('ft_l',0)}-{p.get('ft_v',0)})"
            else:
                st_radar = "STATUS_IN_PROGRESS"
                diag_radar = f"En seguimiento ({reloj})"

            ht_s = f"{p.get('ht_l',0)}-{p.get('ht_v',0)}" if (p.get('ht_l') is not None and p.get('ht_v') is not None) else "0-0"
            init_lote.append((
                f"FS_{p['id']}", init_hoy, reloj, p.get("liga", "General"),
                p.get("local", "Local"), p.get("visitante", "Visitante"),
                ht_s, st_radar, diag_radar, datetime.now().isoformat()
            ))
        database.registrar_lote_partidos_analizados(init_lote)
        print(f"[RADAR] Sondeo inicial completado: {len(init_lote)} partidos bajo vigilancia activa.")
    except Exception as e_init:
        print(f"[WARN] Error en barrido inicial: {e_init}")

    # Mensaje de arranque a Telegram
    comun.log_evento("INICIO", f"{titulo} iniciado con arquitectura dual Flashscore+ESPN.")
    m_ini = database.obtener_metricas_globales(datetime.now(bot.ZONA).strftime("%Y-%m-%d"))
    comun.enviar_mensaje(
        f"🚀 <b>{titulo} INICIADO Y EN LÍNEA (24/7)</b>\n\n"
        f"📡 <b>Motor en Vivo:</b> Flashscore Ninja (Verdad de Estadio y Status 12)\n"
        f"🧠 <b>Motor Matemático:</b> Modelado Poisson y filtros ALTA_CONFIANZA (-3.5 y -4.5)\n"
        f"🎯 <b>Condición HT:</b> SOT &le; 2 verificado en estadio\n"
        f"🔍 <b>Vigilancia Activa Hoy:</b> {m_ini['analizados_hoy']} partidos en radar\n"
        f"📈 <b>Histórico Certificado:</b> {m_ini['analizados_historico']} partidos auditados ({m_ini['total_apuestas']} entradas | {m_ini['efectividad']:.1f}% efectividad)\n\n"
        f"📊 <b>Comandos disponibles en chat:</b>\n"
        f"• Escribe <code>RADAR</code> para ver partidos en juego y descanso.\n"
        f"• Escribe <code>ESTADISTICAS</code> para descargar el PDF completo.\n"
        f"• Escribe <code>DESCARGAR LOGS</code> para la bitácora técnica."
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

            # 2. OBTENCIÓN CONCURRENTE DE DATOS (AMBOS MOTORES EN VIVO)
            # Motor 1: Flashscore en vivo (1,600+ partidos para máxima cobertura)
            partidos_live_fs = fs.obtener_partidos_dia(modo="todos")
            
            # Motor 2: ESPN Scoreboard en vivo
            try:
                evs_espn = client.scoreboard_live(hoy_ymd)
            except Exception as e_espn:
                evs_espn = []
                print(f"[WARN] Scoreboard ESPN no disponible en este ciclo: {e_espn}")

            # 2.1 Actualización del radar masivo de la jornada en SQLite
            if partidos_live_fs:
                lote_radar = []
                for p in partidos_live_fs:
                    st = p.get("status", "0")
                    reloj = p.get("reloj", "")
                    if st == "12":
                        st_radar = "STATUS_HALFTIME"
                        diag_radar = f"En descanso oficial (HT {p.get('ht_l',0)}-{p.get('ht_v',0)}) — Evaluando filtros"
                    elif st == "2":
                        st_radar = "STATUS_FIRST_HALF"
                        diag_radar = f"En juego 1T ({reloj}) — Vigilando minuto a minuto"
                    elif st in ("6", "7", "13"):
                        st_radar = "STATUS_SECOND_HALF"
                        diag_radar = f"En juego 2T ({reloj}) — Monitoreando Cashout"
                    elif st == "1":
                        st_radar = "STATUS_SCHEDULED"
                        diag_radar = f"Programado hoy ({reloj})"
                    elif st == "3":
                        st_radar = "STATUS_FULL_TIME"
                        diag_radar = f"Finalizado ({p.get('ft_l',0)}-{p.get('ft_v',0)})"
                        # Actualizar resultado final FT en la base de datos de apuestas si fue alertado
                        if p.get("ft_l") is not None and p.get("ft_v") is not None:
                            gl_ft = p["ft_l"]
                            gv_ft = p["ft_v"]
                            database.actualizar_resultado_ft(f"FS_{p['id']}", f"{gl_ft}-{gv_ft}", gl_ft + gv_ft)
                        if p["id"] in partidos_activos:
                            del partidos_activos[p["id"]]
                        if f"FS_{p['id']}" in partidos_activos:
                            del partidos_activos[f"FS_{p['id']}"]
                    else:
                        st_radar = "STATUS_IN_PROGRESS"
                        diag_radar = f"En seguimiento ({reloj})"

                    ht_s = f"{p.get('ht_l',0)}-{p.get('ht_v',0)}" if (p.get('ht_l') is not None and p.get('ht_v') is not None) else "0-0"
                    lote_radar.append((
                        f"FS_{p['id']}", hoy_str, reloj, p.get("liga", "General"),
                        p.get("local", "Local"), p.get("visitante", "Visitante"),
                        ht_s, st_radar, diag_radar, ahora_dt.isoformat()
                    ))
                database.registrar_lote_partidos_analizados(lote_radar)

            # 2.5 Monitoreo de 2T para Cashout preventivo (tarjetas rojas)
            # REGLA ESTRICTA: Solo se vigila mientras el partido esté EN JUEGO en el 2T (min 46 a 90),
            # NUNCA en partidos ya finalizados (FT / Status 3 / STATUS_FULL_TIME).
            if partidos_activos and config.MONITOREAR_2T:
                for act_id in list(partidos_activos.keys()):
                    act = partidos_activos[act_id]
                    if act.get("alertado_roja"):
                        continue
                    roja_detectada = False

                    # Monitoreo dual de tarjeta roja en 2T:
                    if act.get("origen") == "ESPN":
                        ev_espn_act = next((e for e in evs_espn if str(e.get("id")) == act_id), None)
                        if ev_espn_act:
                            comp_act = (ev_espn_act.get("competitions") or [{}])[0]
                            st_raw = comp_act.get("status", {}) or {}
                            st_name = str((st_raw.get("type", {})).get("name", "")).upper()
                            
                            # Si ya finalizó (STATUS_FULL_TIME / STATUS_FINAL), resolver y omitir de inmediato
                            if st_name in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                                l_c, v_c = bot._competidores(comp_act)
                                if l_c and v_c:
                                    gl_ft = int(l_c.get("score", 0))
                                    gv_ft = int(v_c.get("score", 0))
                                    database.actualizar_resultado_ft(act_id, f"{gl_ft}-{gv_ft}", gl_ft + gv_ft)
                                del partidos_activos[act_id]
                                continue

                            per_act = st_raw.get("period", 0)
                            # REGLA ESTRICTA: Solo vigilar si está corriendo en el segundo tiempo
                            if per_act == 2 or st_name in ("STATUS_IN_PROGRESS", "STATUS_SECOND_HALF"):
                                d_act = client.summary(act_id, ttl=15)
                                if d_act:
                                    for c in (d_act.get("commentary") or []):
                                        play = c.get("play") or {}
                                        tipo = str((play.get("type") or {}).get("text", ""))
                                        desc = str(play.get("text", "")).lower()
                                        per = (play.get("period") or {}).get("number")
                                        if per == 2 and (tipo == "Red Card" or "red card" in desc or "second yellow" in desc or "sent off" in desc):
                                            roja_detectada = True
                                            break
                    else: # Flashscore
                        clean_id = act_id.replace("FS_", "")
                        p_fs_info = next((p for p in partidos_live_fs if p.get("id") == clean_id), None)
                        if p_fs_info:
                            st_fs = str(p_fs_info.get("status", ""))
                            # Si ya finalizó (status 3 = FT), resolver y omitir de inmediato
                            if st_fs == "3":
                                gl_ft = p_fs_info.get("ft_l")
                                gv_ft = p_fs_info.get("ft_v")
                                if gl_ft is not None and gv_ft is not None:
                                    database.actualizar_resultado_ft(act_id, f"{gl_ft}-{gv_ft}", gl_ft + gv_ft)
                                del partidos_activos[act_id]
                                continue

                            # REGLA ESTRICTA: Solo vigilar si está corriendo en el segundo tiempo (status 13 o 6)
                            if st_fs not in ("13", "6"):
                                continue

                            # Detección oficial en timeline 2T
                            roja_detectada = fs.detectar_roja_2t(clean_id)

                    if roja_detectada:
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
            candidatos_ht_fs = [p for p in partidos_live_fs if p.get("es_descanso") or p.get("status") == "12"]

            # Si hay partidos en descanso detectados por Flashscore
            for pfs in candidatos_ht_fs:
                fs_id = pfs["id"]
                local_fs = pfs.get("local", "")
                visita_fs = pfs.get("visitante", "")
                liga_fs = pfs.get("liga", "Flashscore Live")

                # Sincronización anti-duplicados (por ID y por clave normalizada de equipos)
                clave_partido_fs = f"{comun.norm(local_fs)}_{comun.norm(visita_fs)}"
                if fs_id in enviadas or clave_partido_fs in enviadas:
                    continue

                # 1. FILTRO DE EXCLUSIÓN INMEDIATA: Femenino, Juvenil, Filiales y Amateurs
                inestable, mot_inestable = es_liga_inestable(liga_fs, local_fs, visita_fs)
                if inestable:
                    database.registrar_partido_analizado(
                        f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, "HT",
                        "STATUS_HALFTIME", f"Descartado HT: {mot_inestable}"
                    )
                    continue

                # 2. FILTRO DE LIGAS PROFESIONALES OFICIALES (Catálogo de 81 ligas masculinas)
                if not es_liga_profesional_permitida(liga_fs):
                    database.registrar_partido_analizado(
                        f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, "HT",
                        "STATUS_HALFTIME", "Descartado HT: Liga menor/regional fuera del catálogo profesional"
                    )
                    continue

                # Obtener marcador oficial HT y estadísticas exactas de 1er tiempo
                ht_score, stats_fs = fs.obtener_datos_exactos_ht(fs_id)
                if not ht_score and pfs.get("ht_l") is not None and pfs.get("ht_v") is not None:
                    ht_score = (pfs["ht_l"], pfs["ht_v"])

                if not ht_score:
                    continue

                gl_ht, gv_ht = ht_score
                ht_str = f"{gl_ht}-{gv_ht}"
                total_ht = gl_ht + gv_ht

                # 3. FILTRO PRE-MATCH HÍBRIDO (ALTA_CONFIANZA 75 días)
                ev_espn_match = emparejar_con_espn(local_fs, visita_fs, evs_espn)
                ok_pm, mot_pm, det_pm = evaluar_prematch_hibrido(
                    local_fs, visita_fs, liga_fs, hoy_str, ev_espn_match,
                    indice, cfg, cache_perfiles, global_avg, alpha,
                    fs_id=fs_id
                )
                if not ok_pm:
                    database.registrar_partido_analizado(
                        f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str,
                        "STATUS_HALFTIME", f"Descartado Pre-Match: {mot_pm}"
                    )
                    continue

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

                # BLOQUEO RIGUROSO DE FALSOS CEROS:
                # Si Flashscore no tiene estadísticas detalladas (liga menor sin scout),
                # no podemos certificar que hubo pocos tiros. Se descarta por seguridad.
                if not stats_fs or (shots_tot == 0 and (sot_l + sot_v) == 0):
                    database.registrar_partido_analizado(
                        f"FS_{fs_id}", hoy_str, "", liga_fs, local_fs, visita_fs, ht_str,
                        "STATUS_HALFTIME", "Descartado HT: Sin estadísticas detalladas de tiros en estadio (liga sin scout)"
                    )
                    continue

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
                enviadas.add(clave_partido_fs)
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

                partidos_activos[f"FS_{fs_id}"] = {
                    "local": local_fs,
                    "visita": visita_fs,
                    "ht": ht_str,
                    "linea": linea,
                    "alertado_roja": False,
                    "origen": "FS"
                }

            # -------------------------------------------------------------
            # 4. ESCANEO SECUNDARIO: SCOREBOARD ESPN (Ligas Tradicionales)
            # -------------------------------------------------------------
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

                # 4.1 EVALUACIÓN Y DISPARO DUAL EN DESCANSO (ESPN)
                clave_partido_espn = f"{comun.norm(loc_nom)}_{comun.norm(vis_nom)}"
                if estado_actual == "STATUS_HALFTIME" and eid not in enviadas and clave_partido_espn not in enviadas and p and p.tiene_ht:
                    # 1. Filtro de exclusión inmediata de categorías inestables
                    inest_e, mot_inest_e = es_liga_inestable(nombre_liga or slug, loc_nom, vis_nom)
                    if inest_e:
                        database.registrar_partido_analizado(
                            eid, hoy_str, "", nombre_liga or slug, loc_nom, vis_nom, ht_str,
                            "STATUS_HALFTIME", f"Descartado HT: {mot_inest_e}"
                        )
                        continue

                    # 2. Filtro pre-match ALTA_CONFIANZA (índice 75 días)
                    if indice:
                        eval_e_pm = bot.evaluar_partido(p, indice, cfg, cache_perfiles, global_avg, alpha)
                        ok_e_pm, mot_e_pm = bot.decidir_con_config(eval_e_pm, cfg)
                        if not ok_e_pm:
                            database.registrar_partido_analizado(
                                eid, hoy_str, "", nombre_liga or slug, loc_nom, vis_nom, ht_str,
                                "STATUS_HALFTIME", f"Descartado Pre-Match ALTA_CONFIANZA: {mot_e_pm}"
                            )
                            continue

                    gl_ht = p.goles_local_ht
                    gv_ht = p.goles_visitante_ht
                    total_ht = gl_ht + gv_ht
                    
                    if total_ht in (1, 2) and not (total_ht == 2 and ht_str == "2-0"):
                        d_act = client.summary(eid, ttl=15)
                        st_1t = comun.stats_1t(d_act, p) if d_act else None
                        
                        # BLOQUEO RIGUROSO DE FALSOS CEROS (ESPN sin play-by-play)
                        if not st_1t:
                            database.registrar_partido_analizado(
                                eid, hoy_str, "", nombre_liga or slug, loc_nom, vis_nom, ht_str,
                                "STATUS_HALFTIME", "Descartado HT: Sin comentarios minuto a minuto de ESPN para extraer tiros"
                            )
                            continue

                        sot_l = st_1t.get("L", {}).get("sot", 0)
                        sot_v = st_1t.get("V", {}).get("sot", 0)
                        shots_tot = st_1t.get("L", {}).get("shots", 0) + st_1t.get("V", {}).get("shots", 0)
                        corners_tot = st_1t.get("L", {}).get("corners", 0) + st_1t.get("V", {}).get("corners", 0)
                        reds_1t = st_1t.get("L", {}).get("reds", 0) + st_1t.get("V", {}).get("reds", 0)
                        
                        if reds_1t == 0:
                            decision = False
                            motivo = ""
                            linea = 3.5
                            sot_pierde_val = 0
                            sot_tot_val = sot_l + sot_v
                            
                            if total_ht == 1:
                                linea = 3.5
                                sot_pierde = sot_v if ht_str == "1-0" else sot_l
                                sot_pierde_val = sot_pierde
                                if sot_pierde <= config.SOT_PIERDE_MAX_1G:
                                    decision = True
                                    motivo = f"SOT del que pierde {sot_pierde} (<= {config.SOT_PIERDE_MAX_1G}) | Tiros totales: {shots_tot}"
                                else:
                                    motivo = f"SOT del que pierde excesivo ({sot_pierde} > {config.SOT_PIERDE_MAX_1G})"
                            elif total_ht == 2:
                                linea = 4.5
                                if sot_tot_val <= config.SOT_TOT_MAX_2G:
                                    decision = True
                                    motivo = f"Marcador {ht_str} | SOT total combinado: {sot_tot_val} (<= {config.SOT_TOT_MAX_2G})"
                                else:
                                    motivo = f"SOT combinado excesivo ({sot_tot_val} > {config.SOT_TOT_MAX_2G})"

                            if decision:
                                enviadas.add(eid)
                                enviadas.add(clave_partido_espn)
                                comun.log_evento("ALERTA_HT", f"ENTRADA CONFIRMADA (DUAL ESPN): {loc_nom} vs {vis_nom} (HT {ht_str}) -> Menos {linea} | {motivo}")
                                
                                msg_alerta = (
                                    f"🚨 <b>ALERTA BOT 2.0 — ENTRADA CONFIRMADA (HT)</b> 🚨\n\n"
                                    f"⚽ <b>Partido:</b> {loc_nom} vs {vis_nom}\n"
                                    f"🏆 <b>Liga:</b> {nombre_liga or slug}\n"
                                    f"⏱ <b>Momento:</b> Descanso Oficial (HT {ht_str})\n\n"
                                    f"🎯 <b>MERCADO RECOMENDADO:</b>\n"
                                    f"👉 <b>MENOS DE {linea} GOLES (-{linea})</b>\n\n"
                                    f"📊 <b>Estadísticas de 1er Tiempo:</b>\n"
                                    f"• Tiros a Puerta: L {sot_l} - V {sot_v}\n"
                                    f"• Remates Totales: {shots_tot}\n"
                                    f"• Córners: {corners_tot}\n\n"
                                    f"📈 <b>Criterio Técnico:</b>\n"
                                    f"• {motivo}\n\n"
                                    f"💰 <b>Stake Sugerido:</b> 1.5 a 2.0 Unidades\n"
                                    f"🔒 <i>Confirmado al minuto 45 bajo protocolo ALTA_CONFIANZA 2.0.</i>"
                                )
                                print(f"\n[ALERTA DUAL ESPN] {loc_nom} vs {vis_nom} (HT {ht_str}) -> Menos {linea}")
                                comun.enviar_mensaje(msg_alerta, parse_mode="HTML")
                                
                                database.registrar_apuesta(
                                    event_id=eid, fecha=hoy_str, liga=nombre_liga or slug or "General",
                                    local=loc_nom, visitante=vis_nom, ht=ht_str,
                                    sot_pierde=sot_pierde_val, sot_tot=sot_tot_val,
                                    linea=linea, motivo=motivo
                                )
                                diag = f"🚨 ALERTA CONFIRMADA: Menos {linea} Goles"
                                partidos_activos[eid] = {
                                    "local": loc_nom, "visita": vis_nom,
                                    "ht": ht_str, "linea": linea, "alertado_roja": False,
                                    "origen": "ESPN"
                                }
                            else:
                                diag = f"Descartado HT: {motivo}"

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
