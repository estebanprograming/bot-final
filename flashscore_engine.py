# -*- coding: utf-8 -*-
"""
Flashscore Live Engine (Versión Blindada 2.0 - Alta Concurrencia y Verdad de Estadio)
Extrae en tiempo real partidos, marcador oficial al descanso (HT) y estadísticas
oficiales de 1er tiempo (SOT, tiros totales, córners, posesión, xG, tarjetas)
directamente de los feeds de estadio de Flashscore.
"""

import re
import time
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


def obtener_partidos_dia(modo: str = "en_vivo") -> List[Dict[str, Any]]:
    """
    modo: 'en_vivo' (feed 1 - Ultrarrápido, solo partidos activos) o 'todos' (feed 0 - Jornada completa)
    Parsea los tokens delimitados por '~' de Flashscore.
    """
    feed_code = "1" if modo == "en_vivo" else "0"
    url = f"https://46.flashscore.ninja/2/x/feed/f_1_{feed_code}_3_es_1"
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

            # Marcador parcial de primer tiempo si viene en cabecera
            ba_m = re.search(r"BA÷(\d+)", p)
            bb_m = re.search(r"BB÷(\d+)", p)
            ht_h = int(ba_m.group(1)) if ba_m else None
            ht_a = int(bb_m.group(1)) if bb_m else None

            # Minuto o reloj
            ac_m = re.search(r"AC÷([^¬]+)", p)
            reloj = ac_m.group(1).strip() if ac_m else ""

            # Status codes:
            # 1 = Por empezar
            # 2 = 1er tiempo en juego
            # 12 = Entretiempo / Descanso exacto (MINUTO 45)
            # 3 = Finalizado
            # 6, 7, 13 = 2do tiempo en juego
            partidos.append({
                "id": mid,
                "liga": current_league,
                "local": home,
                "visitante": away,
                "status": status_code,
                "reloj": reloj,
                "es_descanso": (status_code == "12"),
                "es_en_vivo": status_code in ("2", "12", "13", "6", "7"),
                "es_finalizado": (status_code == "3"),
                "ht_l": ht_h,
                "ht_v": ht_a,
                "ft_l": ft_h,
                "ft_v": ft_a,
            })

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


def escanear_partidos_al_descanso() -> List[Dict[str, Any]]:
    """
    Sondea el feed en vivo y devuelve de inmediato los partidos que están
    EXACTAMENTE en el descanso (minuto 45 / status 12) con sus datos de estadio.
    """
    partidos_live = obtener_partidos_dia(modo="en_vivo")
    en_ht = [p for p in partidos_live if p.get("es_descanso") or p.get("status") == "12"]
    
    resultados = []
    for p in en_ht:
        mid = p["id"]
        ht_score, stats = obtener_datos_exactos_ht(mid)
        # Si no viene en el timeline, intentar con los tokens de cabecera
        if not ht_score and p.get("ht_l") is not None and p.get("ht_v") is not None:
            ht_score = (p["ht_l"], p["ht_v"])
            
        p["ht_score"] = ht_score
        p["stats_1t"] = stats
        resultados.append(p)
        
    return resultados
