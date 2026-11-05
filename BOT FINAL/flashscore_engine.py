# -*- coding: utf-8 -*-
"""
Flashscore Live Engine (Versión Blindada v2.0)
Extrae en tiempo real partidos, ligas correctas, marcador exacto al descanso (HT)
y estadísticas de primer tiempo (SOT, tiros totales, córners, posesión, xG)
sin límites de cuotas y a $0.
"""

import urllib.request
import re
import json
import time
from typing import Dict, List, Optional, Any, Tuple

FSIGN = "SW9D1eZo"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "x-fsign": FSIGN,
    "Referer": "https://www.flashscore.es/",
}

def _fetch_url(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")

def obtener_partidos_dia(modo: str = "todos") -> List[Dict[str, Any]]:
    """
    modo: 'todos' (feed 0) o 'en_vivo' (feed 1)
    Parsea correctamente el protocolo de tokens de Flashscore (delimitado por '~').
    Garantiza que cada partido quede bajo su liga y país real (ZA÷).
    """
    feed_code = "1" if modo == "en_vivo" else "0"
    url = f"https://46.flashscore.ninja/2/x/feed/f_1_{feed_code}_3_es_1"
    raw = _fetch_url(url)
    
    parts = raw.split("~")
    partidos = []
    current_league = "Desconocida"
    
    for p in parts:
        if not p:
            continue
        # Cabecera de Liga / Torneo
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
            
            # Goles finales si terminó
            ag_m = re.search(r"AG÷(\d+)", p)
            ah_m = re.search(r"AH÷(\d+)", p)
            ft_h = int(ag_m.group(1)) if ag_m else None
            ft_a = int(ah_m.group(1)) if ah_m else None
            
            # Status:
            # 1 = Por empezar
            # 2 = 1er tiempo en juego
            # 12 = Entretiempo / Descanso exacto (MINUTO 45)
            # 3 = Finalizado
            partidos.append({
                "id": mid,
                "liga": current_league,
                "local": home,
                "visitante": away,
                "status": status_code,
                "es_descanso": (status_code == "12"),
                "es_en_vivo": status_code in ("2", "12", "13", "6", "7"),
                "es_finalizado": (status_code == "3"),
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
        raw_sui = _fetch_url(url_sui)
        for part in raw_sui.split("~"):
            if "AC÷1st Half" in part:
                ig = re.search(r"IG÷(\d+)", part)
                ih = re.search(r"IH÷(\d+)", part)
                if ig and ih:
                    ht_score = (int(ig.group(1)), int(ih.group(1)))
                    break
    except Exception:
        pass

    # 2. Estadísticas de 1er tiempo
    url_st = f"https://46.flashscore.ninja/2/x/feed/df_st_1_{match_id}"
    stats = None
    try:
        raw_st = _fetch_url(url_st)
        if "SE÷1st Half" in raw_st:
            bloque_1t = raw_st.split("SE÷1st Half")[1].split("SE÷")[0]
            st_dict = {
                "sot_l": 0, "sot_v": 0,
                "shots_l": 0, "shots_v": 0,
                "corners_l": 0, "corners_v": 0,
                "possession_l": 50, "possession_v": 50,
                "xg_l": 0.0, "xg_v": 0.0,
                "yellow_l": 0, "yellow_v": 0,
                "red_l": 0, "red_v": 0,
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

                if "Shots on target" in nombre:
                    st_dict["sot_l"] = int(to_num(v_l))
                    st_dict["sot_v"] = int(to_num(v_v))
                elif "Total shots" in nombre:
                    st_dict["shots_l"] = int(to_num(v_l))
                    st_dict["shots_v"] = int(to_num(v_v))
                elif "Corner kicks" in nombre:
                    st_dict["corners_l"] = int(to_num(v_l))
                    st_dict["corners_v"] = int(to_num(v_v))
                elif "Ball possession" in nombre:
                    st_dict["possession_l"] = int(to_num(v_l, 50))
                    st_dict["possession_v"] = int(to_num(v_v, 50))
                elif "Expected goals" in nombre:
                    st_dict["xg_l"] = float(to_num(v_l, 0.0))
                    st_dict["xg_v"] = float(to_num(v_v, 0.0))
                elif "Yellow cards" in nombre:
                    st_dict["yellow_l"] = int(to_num(v_l))
                    st_dict["yellow_v"] = int(to_num(v_v))
                elif "Red cards" in nombre:
                    st_dict["red_l"] = int(to_num(v_l))
                    st_dict["red_v"] = int(to_num(v_v))
            stats = st_dict
    except Exception:
        pass

    return ht_score, stats

if __name__ == "__main__":
    print("=== TEST MOTOR BLINDADO V2 ===")
    partidos = obtener_partidos_dia("todos")
    print(f"Total partidos mapeados: {len(partidos)}")
    
    # Probar con partidos reales verificados
    verificados = [
        "tALwuvO9", # River Plate 2 vs Tigre 2
        "06r8jwoe", # Aurora vs Independiente
        "hhAf8IwB", # Chapecoense-SC vs Mirassol
        "nekZ4qXI", # Internacional vs Coritiba
        "ddYplzs9", # Cartagena vs Quindio
    ]
    
    for p in partidos:
        if p["id"] in verificados:
            ht, st = obtener_datos_exactos_ht(p["id"])
            ht_str = f"{ht[0]}-{ht[1]}" if ht else "Pendiente"
            ft_str = f"{p['ft_l']}-{p['ft_v']}" if p['ft_l'] is not None else "En juego"
            print(f"\n[LIGA: {p['liga']}]")
            print(f"Partido: {p['local']} vs {p['visitante']}")
            print(f"Marcador real: HT={ht_str} | FT={ft_str}")
            if st:
                print(f"Stats 1T: SOT={st['sot_l']}-{st['sot_v']} | Tiros={st['shots_l']}-{st['shots_v']} | Córners={st['corners_l']}-{st['corners_v']} | xG={st['xg_l']}-{st['xg_v']}")
