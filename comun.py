# -*- coding: utf-8 -*-
"""
MÓDULO COMÚN Y SERVICIOS DE SOPORTE — BOT 14/15 EN VIVO
- Extracción de eventos de primer tiempo (tiros, SOT, córners, rojas).
- Integración nativa con Telegram (Alertas en tiempo real, PDFs y estado).
- Conexión con ESPN API y utilidades estadísticas.
"""
from __future__ import annotations

import os
import re
import gzip
import json
import time
import math
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
import logging
from logging.handlers import RotatingFileHandler
import config

LOG_PATH = Path(__file__).parent / "caja_negra_operaciones.log"

# Configuración del logger rotativo de la Caja Negra (hasta 5 MB con 3 respaldos)
_logger = logging.getLogger("CajaNegraBot")
_logger.setLevel(logging.DEBUG)

if not _logger.handlers:
    rfh = RotatingFileHandler(str(LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    rfh.setFormatter(fmt)
    _logger.addHandler(rfh)


def log_evento(categoria: str, mensaje: str, nivel: str = "INFO"):
    """Registra un evento con timestamp exacto en la Caja Negra persistente."""
    txt = f"[{categoria}] {mensaje}"
    if nivel == "DEBUG":
        _logger.debug(txt)
    elif nivel == "WARNING":
        _logger.warning(txt)
    elif nivel == "ERROR":
        _logger.error(txt)
    else:
        _logger.info(txt)

UTC = timezone.utc
BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

TIPOS_TIRO = {"Shot On Target", "Shot Off Target", "Shot Blocked", "Goal",
              "Penalty - Scored", "Penalty - Missed", "Penalty - Saved"}
TIPOS_SOT = {"Shot On Target", "Goal", "Penalty - Scored", "Penalty - Saved"}


# ---------------------------------------------------------------------------
# Utilidades de texto y tiempo
# ---------------------------------------------------------------------------
def norm(s: str) -> str:
    s = str(s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def minuto_de(txt) -> int:
    txt = str(txt or "")
    if "45+" in txt:
        return 45
    if "90+" in txt:
        return 90
    m = re.findall(r"\d+", txt)
    return int(m[0]) if m else 0


def lado(nombre: str, hn: str, an: str) -> Optional[str]:
    if not nombre:
        return None
    if nombre == hn:
        return "L"
    if nombre == an:
        return "V"
    n_nombre = norm(nombre)
    n_hn = norm(hn)
    n_an = norm(an)
    if n_hn in n_nombre or n_nombre in n_hn:
        return "L"
    if n_an in n_nombre or n_nombre in n_an:
        return "V"
    return None


@dataclass
class Partido:
    event_id: str
    fecha: str
    liga: str
    local: str
    visitante: str
    local_id: str
    visitante_id: str
    goles_local_ht: Optional[int] = None
    goles_visitante_ht: Optional[int] = None
    goles_local_ft: Optional[int] = None
    goles_visitante_ft: Optional[int] = None
    estado: str = ""
    valido: bool = True


# ---------------------------------------------------------------------------
# Extracción de estadísticas en el 1T (Halftime)
# ---------------------------------------------------------------------------
def stats_1t(summary: dict, p: Partido, validar_final: bool = False) -> Optional[dict]:
    comps = (summary.get("header") or {}).get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    cs = comp.get("competitors", [])
    hb = next((c for c in cs if c.get("homeAway") == "home"), None)
    ab = next((c for c in cs if c.get("homeAway") == "away"), None)
    if not hb or not ab:
        return None
    hn = str((hb.get("team") or {}).get("displayName") or p.local)
    an = str((ab.get("team") or {}).get("displayName") or p.visitante)
    hid = str(hb.get("id") or (hb.get("team") or {}).get("id") or p.local_id or "")
    aid = str(ab.get("id") or (ab.get("team") or {}).get("id") or p.visitante_id or "")
    first = {s: {"shots": 0, "sot": 0, "corners": 0, "reds": 0} for s in ("L", "V")}
    full = {s: {"shots": 0, "sot": 0, "corners": 0} for s in ("L", "V")}
    eventos = 0
    for c in summary.get("commentary") or []:
        if not isinstance(c, dict):
            continue
        play = c.get("play") or {}
        if not isinstance(play, dict):
            continue
        tipo = str((play.get("type") or {}).get("text", ""))
        team_dict = play.get("team") or {}
        tid = str(team_dict.get("id", ""))
        tname = str(team_dict.get("displayName", ""))
        
        # Priorizar ID único del equipo para evitar errores en clásicos con nombres parecidos
        side = None
        if tid and hid and tid == hid:
            side = "L"
        elif tid and aid and tid == aid:
            side = "V"
        if not side:
            side = lado(tname, hn, an)
            
        if not side:
            continue
        per = (play.get("period") or {}).get("number")
        per = int(per) if per is not None else None
        m = minuto_de((play.get("clock") or {}).get("displayValue"))
        es1 = (per == 1) if per is not None else (m <= 45)
        if tipo == "Red Card":
            if es1:
                first[side]["reds"] += 1
            continue
        if tipo not in TIPOS_TIRO and tipo != "Corner Awarded":
            continue
        eventos += 1
        if tipo in TIPOS_TIRO:
            full[side]["shots"] += 1
            if es1:
                first[side]["shots"] += 1
        if tipo in TIPOS_SOT:
            full[side]["sot"] += 1
            if es1:
                first[side]["sot"] += 1
        if tipo == "Corner Awarded":
            full[side]["corners"] += 1
            if es1:
                first[side]["corners"] += 1

    if eventos == 0:
        return None

    # Verificación de consistencia del contexto (ctx_ok)
    diff = abs(first["L"]["shots"] - first["V"]["shots"])
    first["ctx_ok"] = True
    first["diff"] = diff
    return first


# ---------------------------------------------------------------------------
# Integración con Telegram
# ---------------------------------------------------------------------------
def telegram_config():
    token = os.environ.get("TELEGRAM_TOKEN") or config.TELEGRAM_TOKEN
    chat = os.environ.get("TELEGRAM_CHAT_ID") or config.TELEGRAM_CHAT_ID
    return token.strip(), chat.strip()


def enviar_mensaje(texto: str, token: str = "", chat: str = "", parse_mode: str = "HTML") -> bool:
    t_cfg, c_cfg = telegram_config()
    token = token or t_cfg
    chat = chat or c_cfg
    if not token or not chat:
        print("[WARN] Telegram no configurado: faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return False
    try:
        data = {"chat_id": chat, "text": texto}
        if parse_mode:
            data["parse_mode"] = parse_mode
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            timeout=25
        )
        if r.status_code == 200:
            return True
        else:
            print(f"[WARN] Error Telegram API ({r.status_code}): {r.text}")
            return False
    except Exception as e:
        print(f"[ERROR] Excepción al enviar mensaje a Telegram: {e}")
        return False


def enviar_pdf(ruta: str, caption: str = "", token: str = "", chat: str = "") -> bool:
    t_cfg, c_cfg = telegram_config()
    token = token or t_cfg
    chat = chat or c_cfg
    if not token or not chat:
        return False
    if not Path(ruta).exists():
        return False
    try:
        with open(ruta, "rb") as f:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat, "caption": caption[:1000]},
                files={"document": f},
                timeout=60
            )
        return r.status_code == 200
    except Exception as e:
        print(f"[ERROR] Excepción al enviar PDF a Telegram: {e}")
        return False
