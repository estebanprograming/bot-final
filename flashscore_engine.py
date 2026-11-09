# -*- coding: utf-8 -*-
"""
Flashscore Live Engine (Versión Blindada 2.0 - Alta Concurrencia y Verdad de Estadio)
Extrae en tiempo real partidos, marcador oficial al descanso (HT) y estadísticas
oficiales de 1er tiempo (SOT, tiros totales, córners, posesión, xG, tarjetas)
directamente de los feeds de estadio de Flashscore.
"""

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
import requests

FSIGN = "SW9D1eZo"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "x-fsign": FSIGN,
    "Referer": "https://www.flashscore.es/",
}

# Sesión persistente HTTP para reutilización de conexiones TCP ultrarrápida
_session = requests.Session()
_session.headers.update(HEADERS)

# Caché en memoria para evitar llamadas redundantes en el mismo sondeo
_cache_datos: Dict[str, Tuple[float, Any]] = {}
CACHE_TTL = 15  # segundos


def _fetch_url(url: str, timeout: int = 15) -> str:
    """Descarga el contenido de un feed de Flashscore con sesión optimizada y reintentos."""
    ahora = time.time()
    if url in _cache_datos:
        ts, data = _cache_datos[url]
        if ahora - ts < CACHE_TTL:
            return data

    for intento in range(2):
        try:
            resp = _session.get(url, timeout=timeout)
            if resp.status_code == 200:
                txt = resp.text
                _cache_datos[url] = (ahora, txt)
                return txt
        except Exception as e:
            if intento == 1:
                return ""
            time.sleep(0.5)
    return ""


def obtener_partidos_dia(modo: Any = "todos", day_offset: int = 0) -> List[Dict[str, Any]]:
    """
    modo: 'en_vivo' (retorna solo partidos activos de hoy) o 'todos' (jornada completa de hoy)
    day_offset: 0 para hoy, 1 mañana, -1 ayer.
    Parsea los tokens delimitados por '~' de Flashscore.
    """
    if isinstance(modo, int):
        day_offset = modo
        modo = "todos"

    url = f"https://46.flashscore.ninja/2/x/feed/f_1_{day_offset}_3_es_1"
    raw = _fetch_url(url, timeout=20 if modo == "todos" else 12)
    if not raw:
        return []

    parts = raw.split("~")
    partidos = []
    current_league = "General"
    current_country = ""

    for p in parts:
        if not p:
            continue
        # Cabecera de Liga / País
        if p.startswith("ZA÷"):
            za_m = re.search(r"ZA÷([^¬]+)", p)
            if za_m:
                current_league = za_m.group(1).strip()
            continue

        # Ficha de Partido
        if p.startswith("AA÷"):
            mid = p.split("¬")[0].replace("AA÷", "").strip()
            if not mid or len(mid) < 5:
                continue

            ab_m = re.search(r"AB÷(\d+)", p)
            status_code = ab_m.group(1) if ab_m else "0"

            cx_m = re.search(r"CX÷([^¬]+)", p)
            af_m = re.search(r"AF÷([^¬]+)", p)
            home = cx_m.group(1).strip() if cx_m else "Local"
            away = af_m.group(1).strip() if af_m else "Visitante"

            # Goles actuales / finales
            ag_m = re.search(r"AG÷(\d+)", p)
            ah_m = re.search(r"AH÷(\d+)", p)
            ft_h = int(ag_m.group(1)) if ag_m else None
            ft_a = int(ah_m.group(1)) if ah_m else None

            # Hora programada oficial desde timestamp Unix AD÷ (zona Colombia UTC-5)
            ad_m = re.search(r"AD÷(\d+)", p)
            hora_str = ""
            if ad_m:
                try:
                    ts = int(ad_m.group(1))
                    dt_col = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=-5)))
                    hora_str = dt_col.strftime("%H:%M")
                except Exception:
                    pass

            # Marcador parcial de primer tiempo (Flashscore entrega BC÷ y BD÷ en feeds 0/1)
            bc_m = re.search(r"BC÷(\d+)", p)
            bd_m = re.search(r"BD÷(\d+)", p)
            ba_m = re.search(r"BA÷(\d+)", p)
            bb_m = re.search(r"BB÷(\d+)", p)
            ht_h = int(bc_m.group(1)) if bc_m else (int(ba_m.group(1)) if ba_m else None)
            ht_a = int(bd_m.group(1)) if bd_m else (int(bb_m.group(1)) if bb_m else None)

            # Minuto, periodo o reloj (AC contiene el código exacto de etapa: 12 = Descanso)
            ac_m = re.search(r"AC÷([^¬]+)", p)
            stage_code = ac_m.group(1).strip() if ac_m else ""
            reloj = stage_code

            # Detección infalible de entretiempo (código 12 en AC o texto Descanso)
            es_descanso = (stage_code == "12" or "descanso" in stage_code.lower() or "ht" in stage_code.lower() or "half" in stage_code.lower())

            # Status codes unificados:
            # 1 = Por empezar
            # 2 = 1er tiempo en juego
            # 12 = Entretiempo / Descanso exacto (MINUTO 45)
            # 3 = Finalizado
            # 6, 7, 13 = 2do tiempo en juego
            effective_status = "12" if es_descanso else (stage_code if stage_code in ("1", "2", "3", "6", "7", "13") else status_code)

            partidos.append({
                "id": mid,
                "liga": current_league,
                "local": home,
                "visitante": away,
                "hora": hora_str,
                "status": effective_status,
                "reloj": reloj,
                "es_descanso": es_descanso,
                "es_en_vivo": (status_code == "2" or es_descanso or stage_code in ("2", "12", "13", "6", "7")),
                "es_finalizado": (status_code == "3" or stage_code == "3"),
                "ht_l": ht_h,
                "ht_v": ht_a,
                "ft_l": ft_h,
                "ft_v": ft_a,
            })

    if modo == "en_vivo":
        return [p for p in partidos if p.get("es_en_vivo")]

    return partidos


def obtener_datos_exactos_ht(match_id: str) -> Tuple[Optional[Tuple[int, int]], Optional[Dict[str, Any]]]:
    """
    Obtiene la verdad de estadio para cualquier partido:
    1. Marcador exacto al descanso (HT) desde df_sui_1 (Timeline oficial).
    2. Estadísticas exclusivas de 1er tiempo desde df_st_1 (SE÷1st Half).
    """
    # 1. Timeline oficial para marcador HT
    url_sui = f"https://46.flashscore.ninja/2/x/feed/df_sui_1_{match_id}"
    ht_score = None
    try:
        raw_sui = _fetch_url(url_sui, timeout=10)
        for part in raw_sui.split("~"):
            if "AC÷1st Half" in part or "AC÷Descanso" in part or "AC÷1er tiempo" in part:
                ig = re.search(r"IG÷(\d+)", part)
                ih = re.search(r"IH÷(\d+)", part)
                if ig and ih:
                    ht_score = (int(ig.group(1)), int(ih.group(1)))
                    break
    except Exception:
        pass

    # 2. Estadísticas exclusivas del 1er tiempo desde df_st_1
    url_st = f"https://46.flashscore.ninja/2/x/feed/df_st_1_{match_id}"
    stats = None
    try:
        raw_st = _fetch_url(url_st, timeout=10)
        if "SE÷1st Half" in raw_st or "SE÷1er tiempo" in raw_st:
            delimitador = "SE÷1st Half" if "SE÷1st Half" in raw_st else "SE÷1er tiempo"
            bloque_1t = raw_st.split(delimitador)[1].split("SE÷")[0]
            st_dict = {
                "sot_l": 0, "sot_v": 0,
                "shots_l": 0, "shots_v": 0,
                "corners_l": 0, "corners_v": 0,
                "possession_l": 50, "possession_v": 50,
                "xg_l": 0.0, "xg_v": 0.0,
                "yellow_l": 0, "yellow_v": 0,
                "red_l": 0, "red_v": 0,
                "datos_verificados": True
            }
            for it in bloque_1t.split("~"):
                sg = re.search(r"SG÷([^¬]+)", it)
                sh = re.search(r"SH÷([^¬]+)", it)
                si = re.search(r"SI÷([^¬]+)", it)
                if not (sg and sh and si):
                    continue
                nombre = sg.group(1).strip()
                v_l = sh.group(1).strip()
                v_v = si.group(1).strip()

                def to_num(txt, default=0):
                    t = re.sub(r"[^\d.]", "", txt)
                    try:
                        return float(t) if "." in t else int(t)
                    except Exception:
                        return default

                if any(k in nombre for k in ("Shots on target", "Remates a puerta", "Tiros a puerta")):
                    st_dict["sot_l"] = int(to_num(v_l))
                    st_dict["sot_v"] = int(to_num(v_v))
                elif any(k in nombre for k in ("Total shots", "Remates", "Tiros")):
                    st_dict["shots_l"] = int(to_num(v_l))
                    st_dict["shots_v"] = int(to_num(v_v))
                elif any(k in nombre for k in ("Corner kicks", "Córneres", "Córners")):
                    st_dict["corners_l"] = int(to_num(v_l))
                    st_dict["corners_v"] = int(to_num(v_v))
                elif any(k in nombre for k in ("Ball possession", "Posesión")):
                    st_dict["possession_l"] = int(to_num(v_l, 50))
                    st_dict["possession_v"] = int(to_num(v_v, 50))
                elif any(k in nombre for k in ("Expected goals", "Goles esperados", "xG")):
                    st_dict["xg_l"] = float(to_num(v_l, 0.0))
                    st_dict["xg_v"] = float(to_num(v_v, 0.0))
                elif any(k in nombre for k in ("Yellow cards", "Tarjetas amarillas")):
                    st_dict["yellow_l"] = int(to_num(v_l))
                    st_dict["yellow_v"] = int(to_num(v_v))
                elif any(k in nombre for k in ("Red cards", "Tarjetas rojas")):
                    st_dict["red_l"] = int(to_num(v_l))
                    st_dict["red_v"] = int(to_num(v_v))

            stats = st_dict
    except Exception:
        pass

    return ht_score, stats


def es_liga_prohibida_fs(liga: str) -> bool:
    """
    Verifica si la liga pertenece a la 5ª división inglesa (EC / National League / Vanarama / Conference)
    con precisión quirúrgica para evitar bloquear otras ligas internacionales.
    """
    l_low = (liga or "").lower()
    es_exclusivo_ingles = any(p in l_low for p in (
        "vanarama",
        "conference national",
        "conference premier",
        "english national league",
        "national league (inglaterra)",
        "national league (england)"
    ))
    es_inglaterra = any(p in l_low for p in ("inglaterra", "england", "eng.", "uk", "gran bret"))
    es_national = "national league" in l_low

    return es_exclusivo_ingles or (es_inglaterra and es_national)


def escanear_partidos_al_descanso() -> List[Dict[str, Any]]:
    """
    Sondea el feed en vivo y devuelve de inmediato los partidos que están
    EXACTAMENTE en el descanso (minuto 45 / status 12) con sus datos de estadio.
    """
    partidos_live = obtener_partidos_dia(modo="en_vivo")
    en_ht = [p for p in partidos_live if p.get("es_descanso") or p.get("status") == "12"]
    
    resultados = []
    for p in en_ht:
        # Exclusión preventiva de la 5ª división inglesa (EC / National League)
        if es_liga_prohibida_fs(p.get("liga", "")):
            continue

        mid = p["id"]
        ht_score, stats = obtener_datos_exactos_ht(mid)
        # Si no viene en el timeline, intentar con los tokens de cabecera
        if not ht_score and p.get("ht_l") is not None and p.get("ht_v") is not None:
            ht_score = (p["ht_l"], p["ht_v"])
            
        p["ht_score"] = ht_score
        p["stats_1t"] = stats
        resultados.append(p)
        
    return resultados


def detectar_roja_2t(match_id: str) -> bool:
    """
    Verifica si en el 2º tiempo ocurrió alguna tarjeta roja en Flashscore:
    1. Revisa el timeline de incidencias df_sui_1 (donde IA÷2 indica 2º tiempo y IK÷Red Card).
    2. Revisa el bloque df_st_1 (SE÷2nd Half para tarjetas rojas).
    """
    clean_id = match_id.replace("FS_", "")
    # 1. Timeline de incidencias
    url_sui = f"https://46.flashscore.ninja/2/x/feed/df_sui_1_{clean_id}"
    try:
        raw_sui = _fetch_url(url_sui, timeout=6)
        if raw_sui:
            for part in raw_sui.split("~"):
                if "IA÷2" in part and any(k in part for k in ("Red Card", "Tarj. roja", "Tarjeta roja", "Roja")):
                    return True
    except Exception:
        pass

    # 2. Estadísticas de 2do tiempo
    url_st = f"https://46.flashscore.ninja/2/x/feed/df_st_1_{clean_id}"
    try:
        raw_st = _fetch_url(url_st, timeout=6)
        if raw_st and ("SE÷2nd Half" in raw_st or "SE÷2do tiempo" in raw_st or "SE÷2nd" in raw_st):
            delim = "SE÷2nd Half" if "SE÷2nd Half" in raw_st else ("SE÷2do tiempo" if "SE÷2do tiempo" in raw_st else "SE÷2nd")
            bloque_2t = raw_st.split(delim)[1].split("SE÷")[0]
            for it in bloque_2t.split("~"):
                if "SG÷" in it and any(k in it for k in ("Red cards", "Tarjetas rojas")):
                    sh = re.search(r"SH÷(\d+)", it)
                    si = re.search(r"SI÷(\d+)", it)
                    r_l = int(sh.group(1)) if sh else 0
                    r_v = int(si.group(1)) if si else 0
                    if r_l > 0 or r_v > 0:
                        return True
    except Exception:
        pass

    return False


def obtener_perfil_prematch_fs(match_id: str, max_partidos: int = 15) -> Optional[Dict[str, Any]]:
    """
    Extrae de forma nativa e independiente el historial previo de ambos equipos desde Flashscore (feed df_hh_1).
    Calcula:
      - Estabilidad histórica Under 3.5 de ambos equipos (pct_35_local, pct_35_visita, estab_35).
      - Promedio de goles recientes y expectativa ofensiva previa (esp_total).
      - Muestra de partidos analizados (n_local, n_visita).
    Elimina por completo la necesidad de traducir o sincronizar nombres hacia ESPN.
    """
    clean_id = match_id.replace("FS_", "")
    url = f"https://46.flashscore.ninja/2/x/feed/df_hh_1_{clean_id}"
    raw = _fetch_url(url, timeout=6)
    if not raw:
        return None

    bloques = raw.split("~KB÷")
    regs_l = []
    regs_v = []

    for b in bloques:
        header = b.split("¬")[0]
        if "Last matches:" in header:
            items = b.split("~KC÷")
            matches_data = []
            for item in items[1:]:
                g_l = None
                g_v = None
                for line in item.split("¬"):
                    if line.startswith("KU÷"):
                        try:
                            g_l = int(line.split("÷")[1])
                        except Exception:
                            pass
                    elif line.startswith("KT÷"):
                        try:
                            g_v = int(line.split("÷")[1])
                        except Exception:
                            pass
                if g_l is not None and g_v is not None:
                    matches_data.append({"gl": g_l, "gv": g_v, "tot": g_l + g_v})

            if not regs_l and matches_data:
                regs_l = matches_data[:max_partidos]
            elif not regs_v and matches_data:
                regs_v = matches_data[:max_partidos]
                break

    if not regs_l or not regs_v:
        return None

    n_l = len(regs_l)
    n_v = len(regs_v)
    if n_l < 5 or n_v < 5:
        return None

    u35_l = sum(1 for m in regs_l if m["tot"] < 3.5)
    u35_v = sum(1 for m in regs_v if m["tot"] < 3.5)

    pct_35_l = (u35_l / n_l) * 100.0
    pct_35_v = (u35_v / n_v) * 100.0
    estab_35 = (pct_35_l + pct_35_v) / 2.0

    avg_l = sum(m["tot"] for m in regs_l) / n_l
    avg_v = sum(m["tot"] for m in regs_v) / n_v
    esp_total = (avg_l + avg_v) / 2.0

    return {
        "n_local": n_l,
        "n_visita": n_v,
        "pct_35_local": round(pct_35_l, 1),
        "pct_35_visita": round(pct_35_v, 1),
        "estab_35": round(estab_35, 1),
        "avg_goles_local": round(avg_l, 2),
        "avg_goles_visita": round(avg_v, 2),
        "esp_total": round(esp_total, 2)
    }
