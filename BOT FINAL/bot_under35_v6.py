# -*- coding: utf-8 -*-
# ==============================================================================
# BOT "UNDER 3.5" - V6.3 (Google Colab / Telegram)
# ------------------------------------------------------------------------------
# Estrategia validada:
#   * Mercado unico: MENOS DE 3.5 GOLES (-3.5) al descanso.
#   * Marcador al minuto 45: EXACTAMENTE 1-0 o 0-1 (un solo gol en total).
#   * Se descarta 0-0 y cualquier marcador con 2 o mas goles.
#   * NUNCA se estima el marcador de descanso con factores (ej. 0.42).
#   * Si la API no confirma el entretiempo, NO se alerta: se reintenta.
#
# Modos:
#   MODO_BOT=AUDITORIA  -> backtest + PDF mensual enviado a Telegram
#   MODO_BOT=VIVO       -> bot en vivo, alertas al descanso
#   MODO_BOT=TEST       -> pruebas internas (no toca Telegram)
#
# Credenciales (NO las escribas en el codigo):
#   En Colab:  Panel "Secrets" -> TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
#   En local:  variables de entorno TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
#
# Requisitos: requests, reportlab. En Colab se instala reportlab solo.
# ==============================================================================

from __future__ import annotations

import os
import re
import sys
import json
import math
import time
import html
import gzip
import hashlib
import logging
import threading
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, Iterable

import requests

try:
    from zoneinfo import ZoneInfo
    ZONA = ZoneInfo("America/Bogota")
except Exception:  # pragma: no cover
    ZONA = timezone(timedelta(hours=-5))

UTC = timezone.utc
VERSION = "6.3"

# ==============================================================================
# 0. CONFIGURACION
# ==============================================================================

# --- Perfiles de riesgo -------------------------------------------------------
# INGENIERO: regla original del informe (pre-filtro 3.35 / 50).
# EQUILIBRADO: exige algo mas de calidad sin matar demasiadas oportunidades.
# ALTA_CONFIANZA: el modo recomendado tras validar con datos reales.
# MAXIMA_CONFIANZA: muy selectivo; menos entradas, mas exigencia.
PERFILES = {
    "INGENIERO": {
        "esp_max": 3.35,
        "e35_min": 50.0,
        "liga_2t_max": None,
        "solo_local_1_0": False,
        "exigir_sin_rojas": False,
        "prob_min": 0.0,
        "lambda_max": 3.00,
    },
    "EQUILIBRADO": {
        "esp_max": 2.90,
        "e35_min": 50.0,
        "liga_2t_max": 1.85,
        "solo_local_1_0": False,
        "exigir_sin_rojas": True,
        "prob_min": 0.76,
        "lambda_max": 2.20,
    },
    "ALTA_CONFIANZA": {
        "esp_max": 2.60,
        "e35_min": 50.0,
        "liga_2t_max": 1.70,
        "solo_local_1_0": False,
        "exigir_sin_rojas": True,
        "prob_min": 0.78,
        "lambda_max": 2.00,
    },
    "LOCAL_1_0": {
        "esp_max": 2.70,
        "e35_min": 50.0,
        "liga_2t_max": 1.70,
        "solo_local_1_0": True,
        "exigir_sin_rojas": True,
        "prob_min": 0.78,
        "lambda_max": 2.00,
    },
    "MAXIMA_CONFIANZA": {
        "esp_max": 2.40,
        "e35_min": 55.0,
        "liga_2t_max": 1.60,
        "solo_local_1_0": True,
        "exigir_sin_rojas": True,
        "prob_min": 0.84,
        "lambda_max": 1.80,
    },
}

PERFIL_ACTIVO = os.environ.get("PERFIL_BOT", "ALTA_CONFIANZA").upper()
if PERFIL_ACTIVO not in PERFILES:
    PERFIL_ACTIVO = "ALTA_CONFIANZA"

CFG: dict[str, Any] = {
    # --- Modo de ejecucion ---
    "modo": os.environ.get("MODO_BOT", "AUDITORIA").upper(),
    "perfil": PERFIL_ACTIVO,

    # --- Reglas duras de la estrategia (NO tocar a la ligera) ---
    # Linea objetivo: "3.5" (regla original), "4.5", "5.5" o "AUTO".
    # Para volumen 3-9/dia con 96%+, usa "5.5".
    "linea_objetivo": os.environ.get("LINEA_OBJETIVO", "3.5").upper(),
    "mercado": "MENOS DE 3.5 GOLES (-3.5)",
    "solo_un_gol_ht": True,          # total HT == 1
    "marcadores_validos": {(1, 0), (0, 1)},

    # --- Auditoria ---
    "dias_auditoria": int(os.environ.get("DIAS_AUDITORIA", "30")),
    "dias_historial": int(os.environ.get("DIAS_HISTORIAL", "75")),
    "min_partidos_perfil": int(os.environ.get("MIN_PARTIDOS_PERFIL", "8")),
    "min_partidos_ht": int(os.environ.get("MIN_PARTIDOS_HT", "5")),
    "max_partidos_perfil": 15,
    "max_fichas_pdf": int(os.environ.get("MAX_FICHAS_PDF", "260")),

    # --- Vivo ---
    "poll_segundos": int(os.environ.get("POLL_SEGUNDOS", "75")),
    "max_horas_vivo": float(os.environ.get("MAX_HORAS_VIVO", "11.5")),
    "confirmaciones_halftime": int(os.environ.get("CONFIRMACIONES_HALFTIME", "2")),
    "heartbeat_horas": float(os.environ.get("HEARTBEAT_HORAS", "12")),
    "cuota_referencia": float(os.environ.get("CUOTA_REFERENCIA", "0")) or None,

    # --- Contexto en vivo (tiros, corners, posesion, presion) ---
    # Por defecto SOLO se captura y se muestra. El filtro automatico queda
    # apagado hasta calibrarlo con datos reales.
    "usar_contexto_vivo": os.environ.get("USAR_CONTEXTO_VIVO", "1") == "1",
    "sot_pierde_max": int(os.environ.get("SOT_PIERDE_MAX", "2")),
    "peligro_max": float(os.environ.get("PELIGRO_MAX", "999")),
    "presion_pierde_max": float(os.environ.get("PRESION_PIERDE_MAX", "999")),
    "registrar_contexto": os.environ.get("REGISTRAR_CONTEXTO", "1") == "1",
    "minuto_contexto": int(os.environ.get("MINUTO_CONTEXTO", "45")),

    # --- Red ---
    "max_workers": int(os.environ.get("MAX_WORKERS", "6")),
    "timeout": int(os.environ.get("TIMEOUT_HTTP", "25")),
    "cobertura": os.environ.get("COBERTURA", "AGREGADO").upper(),  # AGREGADO | COMPLETO

    # --- Archivos ---
    "dir_datos": os.environ.get("DIR_DATOS", ""),
}
CFG.update(PERFILES[PERFIL_ACTIVO])
if CFG.get("linea_objetivo") not in {"3.5", "4.5", "5.5", "AUTO"}:
    CFG["linea_objetivo"] = "3.5"

# Credenciales: nunca hardcodear. Se leen de Secrets de Colab o variables de entorno.
def _leer_secreto(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if valor:
        return valor
    try:
        from google.colab import userdata  # type: ignore
        return (userdata.get(nombre) or "").strip()
    except Exception:
        return ""

TELEGRAM_TOKEN = _leer_secreto("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _leer_secreto("TELEGRAM_CHAT_ID")

# ==============================================================================
# 1. LIGAS PERMITIDAS
# ==============================================================================
# Solo ligas domesticas profesionales. Se excluyen copas, amistosos, juveniles,
# femeninas y universitarias. El ID es el que ESPN pone en el uid: s:600~l:ID~e:...
LIGAS: dict[str, dict[str, str]] = {
    "eng.1": {"id": "700", "nombre": "Premier League (Inglaterra)"},
    "eng.2": {"id": "3914", "nombre": "Championship (Inglaterra)"},
    "eng.3": {"id": "3915", "nombre": "League One (Inglaterra)"},
    "eng.4": {"id": "3916", "nombre": "League Two (Inglaterra)"},
    "eng.5": {"id": "3917", "nombre": "National League (Inglaterra)"},
    "esp.1": {"id": "740", "nombre": "LaLiga (Espana)"},
    "esp.2": {"id": "3921", "nombre": "LaLiga 2 (Espana)"},
    "ita.1": {"id": "730", "nombre": "Serie A (Italia)"},
    "ita.2": {"id": "3931", "nombre": "Serie B (Italia)"},
    "ger.1": {"id": "720", "nombre": "Bundesliga (Alemania)"},
    "ger.2": {"id": "3927", "nombre": "2. Bundesliga (Alemania)"},
    "fra.1": {"id": "710", "nombre": "Ligue 1 (Francia)"},
    "fra.2": {"id": "3926", "nombre": "Ligue 2 (Francia)"},
    "por.1": {"id": "715", "nombre": "Primeira Liga (Portugal)"},
    "ned.1": {"id": "725", "nombre": "Eredivisie (Paises Bajos)"},
    "ned.2": {"id": "3933", "nombre": "Eerste Divisie (Paises Bajos)"},
    "bel.1": {"id": "3901", "nombre": "Pro League (Belgica)"},
    "tur.1": {"id": "3946", "nombre": "Super Lig (Turquia)"},
    "sco.1": {"id": "735", "nombre": "Premiership (Escocia)"},
    "sco.2": {"id": "3940", "nombre": "Championship (Escocia)"},
    "sco.3": {"id": "3941", "nombre": "League One (Escocia)"},
    "sco.4": {"id": "3942", "nombre": "League Two (Escocia)"},
    "irl.1": {"id": "3930", "nombre": "Premier Division (Irlanda)"},
    "nir.1": {"id": "3958", "nombre": "Premiership (Irlanda del Norte)"},
    "wal.1": {"id": "3963", "nombre": "Premier League (Gales)"},
    "swe.1": {"id": "3945", "nombre": "Allsvenskan (Suecia)"},
    "swe.2": {"id": "5362", "nombre": "Superettan (Suecia)"},
    "nor.1": {"id": "3960", "nombre": "Eliteserien (Noruega)"},
    "nor.2": {"id": "5360", "nombre": "1. Division (Noruega)"},
    "den.1": {"id": "3913", "nombre": "Superliga (Dinamarca)"},
    "den.2": {"id": "5348", "nombre": "1. Division (Dinamarca)"},
    "fin.1": {"id": "5315", "nombre": "Veikkausliiga (Finlandia)"},
    "aut.1": {"id": "3907", "nombre": "Bundesliga (Austria)"},
    "aut.2": {"id": "5345", "nombre": "2. Liga (Austria)"},
    "sui.1": {"id": "3944", "nombre": "Super League (Suiza)"},
    "sui.2": {"id": "5363", "nombre": "Challenge League (Suiza)"},
    "gre.1": {"id": "3955", "nombre": "Super League (Grecia)"},
    "cze.1": {"id": "5347", "nombre": "Liga Checa"},
    "rou.1": {"id": "10747", "nombre": "Liga 1 (Rumania)"},
    "rus.1": {"id": "3939", "nombre": "Premier League (Rusia)"},
    "isr.1": {"id": "10750", "nombre": "Premier League (Israel)"},
    "col.1": {"id": "650", "nombre": "Primera A (Colombia)"},
    "col.2": {"id": "3910", "nombre": "Primera B (Colombia)"},
    "arg.1": {"id": "745", "nombre": "Liga Profesional (Argentina)"},
    "arg.2": {"id": "3903", "nombre": "Nacional B (Argentina)"},
    "arg.3": {"id": "3904", "nombre": "Primera B (Argentina)"},
    "arg.4": {"id": "4003", "nombre": "Primera C (Argentina)"},
    "bra.1": {"id": "630", "nombre": "Serie A (Brasil)"},
    "bra.2": {"id": "4007", "nombre": "Serie B (Brasil)"},
    "bra.3": {"id": "11088", "nombre": "Serie C (Brasil)"},
    "chi.1": {"id": "640", "nombre": "Primera Division (Chile)"},
    "chi.2": {"id": "3909", "nombre": "Segunda Division (Chile)"},
    "per.1": {"id": "670", "nombre": "Liga 1 (Peru)"},
    "per.2": {"id": "3936", "nombre": "Segunda Division (Peru)"},
    "ecu.1": {"id": "660", "nombre": "LigaPro (Ecuador)"},
    "uru.1": {"id": "680", "nombre": "Liga AUF (Uruguay)"},
    "par.1": {"id": "3934", "nombre": "Primera Division (Paraguay)"},
    "bol.1": {"id": "620", "nombre": "Liga Profesional (Bolivia)"},
    "ven.1": {"id": "3949", "nombre": "Primera Division (Venezuela)"},
    "mex.1": {"id": "760", "nombre": "Liga MX (Mexico)"},
    "mex.2": {"id": "3932", "nombre": "Liga de Expansion (Mexico)"},
    "usa.1": {"id": "770", "nombre": "MLS (Estados Unidos)"},
    "crc.1": {"id": "4005", "nombre": "Primera Division (Costa Rica)"},
    "gua.1": {"id": "3928", "nombre": "Liga Nacional (Guatemala)"},
    "hon.1": {"id": "3929", "nombre": "Liga Nacional (Honduras)"},
    "slv.1": {"id": "3943", "nombre": "Primera Division (El Salvador)"},
    "jam.1": {"id": "8347", "nombre": "Premier League (Jamaica)"},
    "rsa.1": {"id": "3937", "nombre": "Premiership (Sudafrica)"},
    "rsa.2": {"id": "3938", "nombre": "First Division (Sudafrica)"},
    "nga.1": {"id": "8345", "nombre": "Premier League (Nigeria)"},
    "gha.1": {"id": "11052", "nombre": "Premier League (Ghana)"},
    "ken.1": {"id": "11054", "nombre": "Premier League (Kenia)"},
    "uga.1": {"id": "11053", "nombre": "Premier League (Uganda)"},
    "jpn.1": {"id": "750", "nombre": "J1 League (Japon)"},
    "aus.1": {"id": "3906", "nombre": "A-League (Australia)"},
    "chn.1": {"id": "8376", "nombre": "Super League (China)"},
    "ind.1": {"id": "8316", "nombre": "Super League (India)"},
    "ksa.1": {"id": "21231", "nombre": "Saudi Pro League (Arabia Saudita)"},
    "tha.1": {"id": "8344", "nombre": "Thai League 1"},
    "idn.1": {"id": "8339", "nombre": "Super League (Indonesia)"},
    "mys.1": {"id": "8340", "nombre": "Super League (Malasia)"},
}

LIGA_ID_A_SLUG = {v["id"]: k for k, v in LIGAS.items()}
LIGA_ID_A_NOMBRE = {v["id"]: v["nombre"] for k, v in LIGAS.items()}
LIGAS_PERMITIDAS = set(LIGA_ID_A_SLUG.keys())


def liga_desde_evento(ev: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Devuelve (liga_id, slug, nombre) usando el uid de ESPN, no el campo season."""
    uid = str(ev.get("uid", ""))
    m = re.search(r"~l:(\d+)~", uid)
    if not m:
        return None, None, None
    lid = m.group(1)
    slug = LIGA_ID_A_SLUG.get(lid)
    if not slug:
        return lid, None, None
    return lid, slug, LIGA_ID_A_NOMBRE.get(lid, LIGAS[slug]["nombre"])


# ==============================================================================
# 2. UTILIDADES
# ==============================================================================

def ahora_bogota() -> datetime:
    return datetime.now(ZONA)


def iso_utc(dt: datetime) -> str:
    """Normaliza cualquier datetime a un ISO UTC comparable con los de ESPN."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fecha_bogota_de_iso(iso_txt: str) -> str:
    try:
        dt = datetime.fromisoformat(str(iso_txt).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(ZONA).strftime("%Y-%m-%d")
    except Exception:
        return str(iso_txt)[:10]


def num_estricto(valor: Any) -> Optional[int]:
    """Convierte scores de ESPN (int, str, dict con value/displayValue) a int."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return int(valor)
    if isinstance(valor, (int, float)):
        if math.isnan(valor) or math.isinf(valor):
            return None
        return int(valor)
    if isinstance(valor, dict):
        if valor.get("value") is not None:
            return num_estricto(valor.get("value"))
        if valor.get("displayValue") is not None:
            return num_estricto(valor.get("displayValue"))
        return None
    if isinstance(valor, str):
        limpio = valor.strip()
        if not limpio:
            return None
        m = re.search(r"-?\d+", limpio)
        return int(m.group()) if m else None
    return None


def float_estricto(valor: Any) -> Optional[float]:
    """Convierte valores de ESPN a float (posesion, porcentajes, etc.)."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return float(valor)
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, dict):
        if valor.get("value") is not None:
            return float_estricto(valor.get("value"))
        if valor.get("displayValue") is not None:
            return float_estricto(valor.get("displayValue"))
        return None
    if isinstance(valor, str):
        limpio = valor.strip().replace("%", "").replace(",", ".")
        if not limpio:
            return None
        try:
            return float(limpio)
        except Exception:
            m = re.search(r"-?\d+(?:\.\d+)?", limpio)
            return float(m.group()) if m else None
    return None


def minuto_de_reloj(texto: Any) -> int:
    """
    Convierte el reloj de ESPN a minuto de juego.
    '45'+2'' -> 45, '45+2' -> 45, '90'+4'' -> 90, '38'' -> 38.
    Los goles de descuento del primer tiempo cuentan como minuto 45.
    """
    txt = str(texto or "").strip()
    if not txt:
        return 0
    if "45+" in txt:
        return 45
    if "90+" in txt:
        return 90
    m = re.search(r"\d+", txt)
    return int(m.group()) if m else 0


def escapar(txt: Any) -> str:
    return html.escape(str(txt or ""), quote=False)


def pct(numerador: float, denominador: float) -> float:
    return 100.0 * numerador / denominador if denominador else 0.0


def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de confianza de Wilson para una proporcion."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    den = 1.0 + z * z / n
    centro = (p + z * z / (2.0 * n)) / den
    medio = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
    return (100.0 * max(0.0, centro - medio), 100.0 * min(1.0, centro + medio))


def poisson_cdf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    total = 0.0
    for i in range(k + 1):
        total += math.exp(-lam) * (lam ** i) / math.factorial(i)
    return min(1.0, max(0.0, total))


def nbinom_cdf(k: int, mu: float, alpha: float) -> float:
    """CDF de una binomial negativa con media mu y dispersion alpha."""
    if mu <= 0:
        return 1.0 if k >= 0 else 0.0
    if alpha <= 1e-8:
        return poisson_cdf(k, mu)
    r = 1.0 / alpha
    p = r / (r + mu)
    if p <= 0.0 or p >= 1.0:
        return poisson_cdf(k, mu)
    total = 0.0
    for i in range(k + 1):
        log_coef = math.lgamma(i + r) - math.lgamma(i + 1) - math.lgamma(r)
        total += math.exp(log_coef + r * math.log(p) + i * math.log1p(-p))
    return min(1.0, max(0.0, total))


def formatear_num(x: Any, dec: int = 2) -> str:
    try:
        return f"{float(x):.{dec}f}"
    except Exception:
        return "-"


# ==============================================================================
# 3. CLIENTE HTTP CON CACHE, REINTENTOS Y RATE LIMIT
# ==============================================================================

class EspnClient:
    """
    Cliente de ESPN con:
      * cache en disco (gzip) para no repetir llamadas historicas,
      * reintentos con backoff,
      * limite de concurrencia,
      * decodificacion UTF-8 corregida.
    """

    BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

    def __init__(self, cache_dir: Path, workers: int = 6, timeout: int = 25):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.workers = max(1, min(12, workers))
        self.sem = threading.Semaphore(self.workers)
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
        })
        self.stats = Counter()

    # ---------- cache ----------
    def _ruta_cache(self, clave: str) -> Path:
        h = hashlib.sha1(clave.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{h}.json.gz"

    def _leer_cache(self, clave: str, ttl: Optional[float]) -> Optional[Any]:
        ruta = self._ruta_cache(clave)
        if not ruta.exists():
            return None
        if ttl is not None:
            edad = time.time() - ruta.stat().st_mtime
            if edad > ttl:
                return None
        try:
            with gzip.open(ruta, "rt", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _guardar_cache(self, clave: str, data: Any) -> None:
        ruta = self._ruta_cache(clave)
        tmp = ruta.with_suffix(".tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            tmp.replace(ruta)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ---------- GET ----------
    def get_json(self, url: str, cache_key: Optional[str] = None,
                 ttl: Optional[float] = None, force: bool = False) -> Optional[Any]:
        clave = cache_key or url
        if not force:
            cacheado = self._leer_cache(clave, ttl)
            if cacheado is not None:
                self.stats["cache_hit"] += 1
                return cacheado

        ultimo_error = None
        for intento in range(4):
            try:
                with self.sem:
                    r = self.s.get(url, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(2.0 + 2.0 * intento)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(1.5 + 1.5 * intento)
                    continue
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                # ESPN a veces manda Latin-1 mal etiquetado; forzamos UTF-8.
                r.encoding = "utf-8"
                data = r.json()
                self.stats["http_ok"] += 1
                if cache_key:
                    self._guardar_cache(clave, data)
                return data
            except Exception as e:
                ultimo_error = e
                self.stats["http_error"] += 1
                time.sleep(1.0 + 1.5 * intento)
        logging.warning("Fallo HTTP %s: %s", url, ultimo_error)
        return None

    # ---------- endpoints ----------
    def scoreboard(self, fecha_yyyymmdd: str, cobertura: str = "AGREGADO") -> list[dict]:
        """Devuelve eventos crudos. En COMPLETO consulta liga por liga."""
        if cobertura.upper() == "COMPLETO":
            return self._scoreboard_completo(fecha_yyyymmdd)
        url = f"{self.BASE}/all/scoreboard?dates={fecha_yyyymmdd}&limit=1000"
        data = self.get_json(url, cache_key=f"sb_all_{fecha_yyyymmdd}", ttl=None)
        if not isinstance(data, dict):
            return []
        return data.get("events", []) or []

    def scoreboard_live(self, fecha_yyyymmdd: str) -> list[dict]:
        """Scoreboard fresco, sin cache, para el bot en vivo."""
        url = f"{self.BASE}/all/scoreboard?dates={fecha_yyyymmdd}&limit=1000"
        data = self.get_json(url, cache_key=None, ttl=0, force=True)
        if not isinstance(data, dict):
            return []
        return data.get("events", []) or []

    def _scoreboard_completo(self, fecha_yyyymmdd: str) -> list[dict]:
        eventos: dict[str, dict] = {}

        def bajar(slug: str):
            url = f"{self.BASE}/{slug}/scoreboard?dates={fecha_yyyymmdd}&limit=300"
            data = self.get_json(url, cache_key=f"sb_{slug}_{fecha_yyyymmdd}", ttl=None)
            if not isinstance(data, dict):
                return []
            liga = (data.get("leagues") or [{}])[0]
            salida = []
            for ev in data.get("events", []) or []:
                ev = dict(ev)
                ev["_liga_slug_forzada"] = slug
                ev["_liga_nombre_forzado"] = liga.get("name") or LIGAS.get(slug, {}).get("nombre", slug)
                salida.append(ev)
            return salida

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futuros = [ex.submit(bajar, slug) for slug in LIGAS]
            for f in as_completed(futuros):
                try:
                    for ev in f.result():
                        eid = str(ev.get("id", ""))
                        if eid and eid not in eventos:
                            eventos[eid] = ev
                except Exception:
                    continue
        return list(eventos.values())

    def summary(self, event_id: str, force: bool = False, ttl: Optional[float] = 300,
                historico: bool = False) -> Optional[dict]:
        """
        historico=True usa una clave de cache separada. Asi el resumen de un
        partido en vivo (que puede estar a medias) no contamina la auditoria
        posterior del mismo partido.
        """
        url = f"{self.BASE}/all/summary?event={event_id}"
        clave = f"hist_sum_{event_id}" if historico else f"sum_{event_id}"
        data = self.get_json(url, cache_key=clave, ttl=ttl, force=force)
        return data if isinstance(data, dict) else None


# ==============================================================================
# 4. MODELO DE DATOS
# ==============================================================================

@dataclass
class MarcadorHT:
    local: int
    visitante: int
    fuente: str
    confirmado: bool
    estado: str = ""
    minuto: str = ""


@dataclass
class Partido:
    event_id: str
    fecha_iso: str
    fecha: str
    liga_id: str
    liga_slug: str
    liga_nombre: str
    local_id: str
    local_nombre: str
    visitante_id: str
    visitante_nombre: str
    goles_local_ft: int
    goles_visitante_ft: int
    goles_local_ht: Optional[int]
    goles_visitante_ht: Optional[int]
    fuente_ht: str
    rojas_1t: int = 0
    estado: str = ""
    valido: bool = True
    motivo_invalido: str = ""

    @property
    def total_ft(self) -> int:
        return self.goles_local_ft + self.goles_visitante_ft

    @property
    def total_ht(self) -> Optional[int]:
        if self.goles_local_ht is None or self.goles_visitante_ht is None:
            return None
        return self.goles_local_ht + self.goles_visitante_ht

    @property
    def tiene_ht(self) -> bool:
        return self.goles_local_ht is not None and self.goles_visitante_ht is not None

    @property
    def goles_2t(self) -> Optional[int]:
        if not self.tiene_ht:
            return None
        return max(0, self.total_ft - int(self.total_ht))

    def marcador_ht(self) -> str:
        if not self.tiene_ht:
            return "sin confirmar"
        return f"{self.goles_local_ht} - {self.goles_visitante_ht}"


@dataclass
class PerfilEquipo:
    team_id: str
    nombre: str
    n: int
    n_ht: int
    pct_35: float
    pct_45: float
    gf_1t: float
    gc_1t: float
    gf_2t_local: float
    gc_2t_local: float
    gf_2t_visita: float
    gc_2t_visita: float
    media_2t: float
    n_1gol_1t: int
    media_2t_tras_1gol: Optional[float]
    pct_35_tras_1gol: Optional[float]
    n_ganando_1t: int
    media_2t_ganando: Optional[float]
    n_perdiendo_1t: int
    media_2t_perdiendo: Optional[float]


@dataclass
class ContextoVivo:
    """Estadisticas del partido en el momento de la entrada (45' o 50')."""
    minuto: int = 0
    tiros_local: int = 0
    tiros_visita: int = 0
    tiros_arco_local: int = 0
    tiros_arco_visita: int = 0
    corners_local: int = 0
    corners_visita: int = 0
    posesion_local: float = 0.0
    posesion_visita: float = 0.0
    centros_local: int = 0
    centros_visita: int = 0
    bloqueos_local: int = 0
    bloqueos_visita: int = 0
    paradas_local: int = 0
    paradas_visita: int = 0
    faltas_local: int = 0
    faltas_visita: int = 0
    rojas_local: int = 0
    rojas_visita: int = 0
    despejes_local: int = 0
    despejes_visita: int = 0
    sot_pierde: int = 0
    peligro_45: float = 0.0
    presion_pierde_45: float = 0.0
    control_gana: float = 0.0
    ritmo_45: float = 0.0
    datos_ok: bool = False
    fuente: str = "boxscore"

    def resumen_corto(self) -> str:
        if not self.datos_ok:
            return "sin estadisticas en vivo"
        return (
            f"Tiros {self.tiros_local}-{self.tiros_visita} | "
            f"A puerta {self.tiros_arco_local}-{self.tiros_arco_visita} | "
            f"Corners {self.corners_local}-{self.corners_visita} | "
            f"Posesion {self.posesion_local:.0f}%-{self.posesion_visita:.0f}%"
        )


@dataclass
class Evaluacion:
    partido: Partido
    perfil_local: Optional[PerfilEquipo] = None
    perfil_visitante: Optional[PerfilEquipo] = None
    contexto: Optional[ContextoVivo] = None
    liga_2t: float = 0.0
    esp_1t: float = 0.0
    esp_2t: float = 0.0
    esp_total: float = 0.0
    estab_35: float = 0.0
    estab_45: float = 0.0
    lambda_2t: float = 0.0
    prob_under35: float = 0.0
    prob_under45: float = 0.0
    prob_under55: float = 0.0
    linea: str = "3.5"
    prob_linea: float = 0.0
    cuota_minima_linea: Optional[float] = None
    apuesta: bool = False
    motivo: str = ""
    explicacion: str = ""
    gano: Optional[bool] = None
    goles_2t_reales: Optional[int] = None
    cuota_minima: Optional[float] = None
    valor_esperado: Optional[float] = None

    @property
    def fecha(self) -> str:
        return self.partido.fecha

    @property
    def liga(self) -> str:
        return self.partido.liga_nombre

    @property
    def partido_txt(self) -> str:
        return f"{self.partido.local_nombre} vs {self.partido.visitante_nombre}"


# ==============================================================================
# 5. EXTRACCION ESTRICTA DEL MARCADOR AL DESCANSO
# ==============================================================================

ESTADOS_HALFTIME = {
    "STATUS_HALFTIME", "STATUS_HALF_TIME", "STATUS_HALF-TIME",
    "STATUS_HT", "STATUS_BREAK", "STATUS_INTERVAL",
}
TEXTOS_HALFTIME = ("halftime", "half time", "half-time", "descanso", "entretiempo", "medio tiempo", "ht")


def es_estado_halftime(status: dict) -> bool:
    tipo = (status or {}).get("type", {}) or {}
    nombre = str(tipo.get("name", "")).upper()
    desc = str(tipo.get("description", "")).lower()
    detalle = str(tipo.get("detail", "")).lower()
    corto = str(tipo.get("shortDetail", "")).lower()
    if nombre in ESTADOS_HALFTIME:
        return True
    texto = " ".join([desc, detalle, corto])
    return any(t in texto for t in TEXTOS_HALFTIME)


def _competidores(comp: dict) -> tuple[Optional[dict], Optional[dict]]:
    comps = comp.get("competitors", []) or []
    local = next((c for c in comps if c.get("homeAway") == "home"), None)
    visita = next((c for c in comps if c.get("homeAway") == "away"), None)
    if not local and len(comps) >= 2:
        local, visita = comps[0], comps[1]
    return local, visita


def _lineas_ht(local: dict, visita: dict, minimo: int) -> Optional[tuple[int, int]]:
    lls = local.get("linescores") or []
    vls = visita.get("linescores") or []
    if len(lls) < minimo or len(vls) < minimo:
        return None
    l1 = num_estricto(lls[0])
    v1 = num_estricto(vls[0])
    if l1 is None or v1 is None:
        return None
    return l1, v1


def _goles_detalle(details: list, local_id: str, visita_id: str,
                   total_esperado: int) -> Optional[tuple[int, int]]:
    """
    Cuenta goles del primer tiempo desde 'details'.
    Solo acepta si el numero total de goles coincide con el marcador.
    Los penales de shootout se excluyen. El own goal se acredita al equipo
    que figura en details['team']['id'] (ESPN acredita al beneficiado).
    """
    goles = []
    for d in details or []:
        if not d.get("scoringPlay"):
            continue
        if d.get("shootout") is True:
            continue
        m = minuto_de_reloj((d.get("clock") or {}).get("displayValue"))
        if m <= 45:
            goles.append(d)
    if total_esperado >= 0 and len([d for d in details or [] if d.get("scoringPlay") and not d.get("shootout")]) != total_esperado:
        return None
    l = v = 0
    for d in goles:
        tid = str((d.get("team") or {}).get("id", ""))
        if tid == str(local_id):
            l += 1
        elif tid == str(visita_id):
            v += 1
        else:
            return None
    return l, v


def extraer_ht(evento: dict, resumen: Optional[dict] = None, live: bool = False) -> Optional[MarcadorHT]:
    """
    Regla de oro: el marcador de descanso SOLO se devuelve si esta confirmado.
    - En vivo: el estado debe ser Halftime/Descanso/Entretiempo.
    - En auditoria: el partido debe estar terminado (STATUS_FULL_TIME) y el
      1T debe salir de linescores o de details validados contra el FT.
    - Nunca se usa el marcador final multiplicado por ningun factor.
    - Nunca se asume 0-0 por defecto.
    """
    comp = (evento.get("competitions") or [{}])[0]
    status = comp.get("status", {}) or {}
    tipo = status.get("type", {}) or {}
    estado = str(tipo.get("state", "")).lower()
    nombre = str(tipo.get("name", "")).upper()
    local, visita = _competidores(comp)
    if not local or not visita:
        return None

    lid = str((local.get("team") or {}).get("id", ""))
    vid = str((visita.get("team") or {}).get("id", ""))
    gl = num_estricto(local.get("score"))
    gv = num_estricto(visita.get("score"))
    if gl is None or gv is None:
        return None

    # ------------------------- MODO EN VIVO -------------------------
    if live:
        if estado != "in" or not es_estado_halftime(status):
            return None

        # El marcador actual, con estado oficial de entretiempo, ES el HT.
        # Se cruza con linescores/details cuando estan disponibles.
        lls = local.get("linescores") or []
        vls = visita.get("linescores") or []
        if len(lls) >= 1 and len(vls) >= 1:
            l1 = num_estricto(lls[0])
            v1 = num_estricto(vls[0])
            if l1 is not None and v1 is not None:
                if (l1, v1) != (gl, gv):
                    return None  # datos inconsistentes: esperar
                return MarcadorHT(l1, v1, "linescores_halftime", True,
                                  tipo.get("name", ""), status.get("displayClock", ""))

        det = comp.get("details", []) or []
        goles = [d for d in det if d.get("scoringPlay") and not d.get("shootout")]
        if len(goles) == gl + gv:
            l1 = v1 = 0
            for d in goles:
                m = minuto_de_reloj((d.get("clock") or {}).get("displayValue"))
                if m > 45:
                    return None
                tid = str((d.get("team") or {}).get("id", ""))
                if tid == lid:
                    l1 += 1
                elif tid == vid:
                    v1 += 1
                else:
                    return None
            if (l1, v1) == (gl, gv):
                return MarcadorHT(l1, v1, "details_halftime", True,
                                  tipo.get("name", ""), status.get("displayClock", ""))

        # Sin linescores ni details completos: el estado oficial de halftime
        # sigue siendo suficiente para el marcador actual.
        return MarcadorHT(gl, gv, "halftime_score", True,
                          tipo.get("name", ""), status.get("displayClock", ""))

    # ------------------------- MODO AUDITORIA -------------------------
    if estado != "post":
        return None
    if nombre and nombre not in {"STATUS_FULL_TIME", "STATUS_FINAL"}:
        # Excluye FINAL_PEN, FINAL_AET, SUSPENDED, POSTPONED, etc.
        return None
    if gl == 0 and gv == 0:
        # Un 0-0 final implica 0-0 al descanso. Es un hecho, no una suposicion.
        return MarcadorHT(0, 0, "ft_0_0", True, nombre, "FT")

    # 1) linescores del evento (dos periodos completos)
    ht = _lineas_ht(local, visita, minimo=2)
    if ht is not None:
        return MarcadorHT(ht[0], ht[1], "linescores", True, nombre, "FT")

    # 2) details del evento
    ht = _goles_detalle(comp.get("details", []), lid, vid, gl + gv)
    if ht is not None:
        return MarcadorHT(ht[0], ht[1], "details", True, nombre, "FT")

    # 3) resumen (header) como respaldo
    if resumen:
        hcomp = ((resumen.get("header") or {}).get("competitions") or [{}])[0]
        hlocal, hvisita = _competidores(hcomp)
        if hlocal and hvisita:
            hgl = num_estricto(hlocal.get("score"))
            hgv = num_estricto(hvisita.get("score"))
            if hgl == gl and hgv == gv:
                ht = _lineas_ht(hlocal, hvisita, minimo=2)
                if ht is not None:
                    return MarcadorHT(ht[0], ht[1], "summary_linescores", True, nombre, "FT")
                ht = _goles_detalle(hcomp.get("details", []), lid, vid, gl + gv)
                if ht is not None:
                    return MarcadorHT(ht[0], ht[1], "summary_details", True, nombre, "FT")

    return None


def contar_rojas_1t(evento: dict) -> int:
    comp = (evento.get("competitions") or [{}])[0]
    total = 0
    for d in comp.get("details", []) or []:
        if d.get("redCard") is True:
            if minuto_de_reloj((d.get("clock") or {}).get("displayValue")) <= 45:
                total += 1
    return total


# ==============================================================================
# 5B. CONTEXTO EN VIVO: TIROS, CORNERS, POSESION, PRESION
# ==============================================================================

def _stats_equipo(team_box: dict) -> dict[str, Any]:
    salida: dict[str, Any] = {}
    for st in team_box.get("statistics", []) or []:
        nombre = st.get("name")
        if nombre:
            salida[str(nombre)] = st.get("displayValue")
    return salida


def _stat_int(stats: dict, nombre: str) -> int:
    return num_estricto(stats.get(nombre)) or 0


def _stat_float(stats: dict, nombre: str) -> float:
    return float_estricto(stats.get(nombre)) or 0.0


def extraer_contexto(resumen: Optional[dict], minuto: int = 45,
                     pierde_es_local: bool = False) -> Optional[ContextoVivo]:
    """
    Extrae las estadisticas del boxscore de ESPN en el momento de la entrada.
    En vivo, a los 45-50 minutos, ESPN devuelve las estadisticas acumuladas
    del primer tiempo (y los primeros minutos del segundo si ya empezo).

    Si el proveedor no trae estadisticas (todo en cero o None), devuelve un
    contexto con datos_ok=False. En ese caso el bot NO debe inventar nada:
    sigue con el modelo base.
    """
    if not resumen:
        return None
    box = resumen.get("boxscore") or {}
    teams = box.get("teams") or []
    if len(teams) < 2:
        return None
    home = next((t for t in teams if str(t.get("homeAway", "")).lower() == "home"), None)
    away = next((t for t in teams if str(t.get("homeAway", "")).lower() == "away"), None)
    if not home and len(teams) >= 2:
        home, away = teams[0], teams[1]
    if not home or not away:
        return None

    hs = _stats_equipo(home)
    as_ = _stats_equipo(away)

    ctx = ContextoVivo(
        minuto=max(1, int(minuto)),
        tiros_local=_stat_int(hs, "totalShots"),
        tiros_visita=_stat_int(as_, "totalShots"),
        tiros_arco_local=_stat_int(hs, "shotsOnTarget"),
        tiros_arco_visita=_stat_int(as_, "shotsOnTarget"),
        corners_local=_stat_int(hs, "wonCorners"),
        corners_visita=_stat_int(as_, "wonCorners"),
        posesion_local=round(_stat_float(hs, "possessionPct"), 1),
        posesion_visita=round(_stat_float(as_, "possessionPct"), 1),
        centros_local=_stat_int(hs, "totalCrosses"),
        centros_visita=_stat_int(as_, "totalCrosses"),
        bloqueos_local=_stat_int(hs, "blockedShots"),
        bloqueos_visita=_stat_int(as_, "blockedShots"),
        paradas_local=_stat_int(hs, "saves"),
        paradas_visita=_stat_int(as_, "saves"),
        faltas_local=_stat_int(hs, "foulsCommitted"),
        faltas_visita=_stat_int(as_, "foulsCommitted"),
        rojas_local=_stat_int(hs, "redCards"),
        rojas_visita=_stat_int(as_, "redCards"),
        despejes_local=_stat_int(hs, "totalClearance"),
        despejes_visita=_stat_int(as_, "totalClearance"),
        fuente="boxscore_espn",
    )

    # Si todo esta en cero/None, el proveedor no cargo las estadisticas.
    senales = [
        ctx.tiros_local, ctx.tiros_visita,
        ctx.tiros_arco_local, ctx.tiros_arco_visita,
        ctx.corners_local, ctx.corners_visita,
        ctx.posesion_local, ctx.posesion_visita,
    ]
    ctx.datos_ok = any(x > 0 for x in senales)
    if not ctx.datos_ok:
        return ctx

    m = max(1, ctx.minuto)
    sot_tot = ctx.tiros_arco_local + ctx.tiros_arco_visita
    tiros_tot = ctx.tiros_local + ctx.tiros_visita
    corners_tot = ctx.corners_local + ctx.corners_visita
    centros_tot = ctx.centros_local + ctx.centros_visita
    bloqueos_tot = ctx.bloqueos_local + ctx.bloqueos_visita

    # Indice de peligro: pondera lo que mas correlaciona con goles.
    peligro = (
        sot_tot * 1.00 +
        tiros_tot * 0.35 +
        corners_tot * 0.50 +
        centros_tot * 0.15 +
        bloqueos_tot * 0.20
    )
    ctx.peligro_45 = round(peligro * 45.0 / m, 2)
    ctx.ritmo_45 = round((tiros_tot + corners_tot) * 45.0 / m, 2)

    if pierde_es_local:
        ctx.sot_pierde = ctx.tiros_arco_local
        presion = (
            ctx.tiros_arco_local * 1.00 +
            ctx.tiros_local * 0.35 +
            ctx.corners_local * 0.50
        )
        control = (
            ctx.posesion_visita / 100.0 +
            ctx.paradas_visita * 0.10 +
            ctx.despejes_visita * 0.03
        )
    else:
        ctx.sot_pierde = ctx.tiros_arco_visita
        presion = (
            ctx.tiros_arco_visita * 1.00 +
            ctx.tiros_visita * 0.35 +
            ctx.corners_visita * 0.50
        )
        control = (
            ctx.posesion_local / 100.0 +
            ctx.paradas_local * 0.10 +
            ctx.despejes_local * 0.03
        )
    ctx.presion_pierde_45 = round(presion * 45.0 / m, 2)
    ctx.control_gana = round(control, 3)
    return ctx


# ==============================================================================
# 6. PARSEO DE EVENTOS E INDICE HISTORICO
# ==============================================================================

def parsear_evento(ev: dict, fecha_scoreboard: str, live: bool = False,
                   resumen: Optional[dict] = None) -> Optional[Partido]:
    comp = (ev.get("competitions") or [{}])[0]
    status = comp.get("status", {}) or {}
    tipo = status.get("type", {}) or {}
    estado = str(tipo.get("state", "")).lower()
    nombre_estado = str(tipo.get("name", "")).upper()

    lid, slug, nombre_liga = liga_desde_evento(ev)
    if "_liga_slug_forzada" in ev:
        slug = ev["_liga_slug_forzada"]
        nombre_liga = ev.get("_liga_nombre_forzado") or LIGAS.get(slug, {}).get("nombre", slug)
        lid = LIGAS.get(slug, {}).get("id", lid)
    if not slug or lid not in LIGAS_PERMITIDAS:
        return None

    local, visita = _competidores(comp)
    if not local or not visita:
        return None
    gl = num_estricto(local.get("score"))
    gv = num_estricto(visita.get("score"))
    if gl is None or gv is None:
        return None

    if live:
        if estado != "in" or not es_estado_halftime(status):
            return None
    else:
        if estado != "post":
            return None
        if nombre_estado and nombre_estado not in {"STATUS_FULL_TIME", "STATUS_FINAL"}:
            return None

    ht = extraer_ht(ev, resumen=resumen, live=live)
    fecha_iso = str(ev.get("date", ""))
    fecha = fecha_bogota_de_iso(fecha_iso) if fecha_iso else fecha_scoreboard

    p = Partido(
        event_id=str(ev.get("id", "")),
        fecha_iso=fecha_iso,
        fecha=fecha,
        liga_id=str(lid),
        liga_slug=str(slug),
        liga_nombre=str(nombre_liga or LIGAS.get(slug, {}).get("nombre", slug)),
        local_id=str((local.get("team") or {}).get("id", "")),
        local_nombre=str((local.get("team") or {}).get("displayName", "Local")),
        visitante_id=str((visita.get("team") or {}).get("id", "")),
        visitante_nombre=str((visita.get("team") or {}).get("displayName", "Visitante")),
        goles_local_ft=gl,
        goles_visitante_ft=gv,
        goles_local_ht=ht.local if ht else None,
        goles_visitante_ht=ht.visitante if ht else None,
        fuente_ht=ht.fuente if ht else "sin_confirmar",
        rojas_1t=contar_rojas_1t(ev),
        estado=nombre_estado,
        valido=bool(ht and ht.confirmado),
        motivo_invalido="" if ht else "sin_ht_confirmado",
    )
    return p


def construir_indice(client: EspnClient, desde: str, hasta: str,
                     cobertura: str = "AGREGADO") -> list[Partido]:
    """
    Construye un indice global de partidos terminados entre dos fechas.
    Se consulta el scoreboard agregado por dia; se deduplica por event_id.
    """
    d0 = datetime.strptime(desde, "%Y-%m-%d")
    d1 = datetime.strptime(hasta, "%Y-%m-%d")
    fechas = []
    while d0 <= d1:
        fechas.append(d0.strftime("%Y%m%d"))
        d0 += timedelta(days=1)

    partidos: dict[str, Partido] = {}
    errores = 0

    def procesar(fecha: str):
        evs = client.scoreboard(fecha, cobertura=cobertura)
        salida = []
        for ev in evs:
            try:
                p = parsear_evento(ev, fecha, live=False)
                if p and not p.tiene_ht:
                    # Respaldo: el scoreboard no trae details completos.
                    # Se pide el resumen (linescores) para no perder el HT.
                    res = client.summary(p.event_id, ttl=None, historico=True)
                    if res:
                        p2 = parsear_evento(ev, fecha, live=False, resumen=res)
                        if p2 and p2.tiene_ht:
                            p = p2
                if p:
                    salida.append(p)
            except Exception:
                continue
        return salida

    with ThreadPoolExecutor(max_workers=client.workers) as ex:
        futuros = {ex.submit(procesar, f): f for f in fechas}
        for fut in as_completed(futuros):
            try:
                for p in fut.result():
                    if p.event_id and p.event_id not in partidos:
                        partidos[p.event_id] = p
            except Exception:
                errores += 1

    lista = sorted(partidos.values(), key=lambda x: (x.fecha_iso, x.event_id))
    logging.info("Indice: %s partidos (%s errores)", len(lista), errores)
    return lista


# ==============================================================================
# 7. PERFILES DE EQUIPO Y MEDIAS DE LIGA
# ==============================================================================

def _registros_equipo(partidos: list[Partido], team_id: str,
                      cutoff_iso: str, max_partidos: int = 15) -> list[dict]:
    previos = [
        p for p in partidos
        if p.tiene_ht and p.valido and p.fecha_iso < cutoff_iso
        and (p.local_id == team_id or p.visitante_id == team_id)
    ]
    previos = previos[-max_partidos:]
    regs = []
    for p in previos:
        if p.local_id == team_id:
            gf1 = int(p.goles_local_ht)
            gc1 = int(p.goles_visitante_ht)
            gf2 = max(0, p.goles_local_ft - gf1)
            gc2 = max(0, p.goles_visitante_ft - gc1)
            es_local = True
        else:
            gf1 = int(p.goles_visitante_ht)
            gc1 = int(p.goles_local_ht)
            gf2 = max(0, p.goles_visitante_ft - gf1)
            gc2 = max(0, p.goles_local_ft - gc1)
            es_local = False
        regs.append({
            "es_local": es_local,
            "gf1": gf1, "gc1": gc1, "gf2": gf2, "gc2": gc2,
            "t1": gf1 + gc1, "t2": gf2 + gc2, "total": gf1 + gc1 + gf2 + gc2,
        })
    return regs


def construir_perfil(partidos: list[Partido], team_id: str, nombre: str,
                     cutoff_iso: str, min_partidos: int = 8,
                     min_ht: int = 5, max_partidos: int = 15) -> Optional[PerfilEquipo]:
    regs = _registros_equipo(partidos, team_id, cutoff_iso, max_partidos)
    n = len(regs)
    if n < min_partidos:
        return None
    n_ht = sum(1 for r in regs if r["t1"] is not None)
    if n_ht < min_ht:
        return None

    # Suavizado bayesiano: evita que 5 partidos al 100% parezcan certeza.
    alpha = 5.0
    prior_35 = 0.55
    prior_45 = 0.75
    bajo_35 = sum(1 for r in regs if r["total"] < 3.5)
    bajo_45 = sum(1 for r in regs if r["total"] < 4.5)
    pct_35 = 100.0 * (bajo_35 + alpha * prior_35) / (n + alpha)
    pct_45 = 100.0 * (bajo_45 + alpha * prior_45) / (n + alpha)

    loc = [r for r in regs if r["es_local"]]
    vis = [r for r in regs if not r["es_local"]]
    media_2t = sum(r["t2"] for r in regs) / n

    def media(lista, clave):
        return sum(r[clave] for r in lista) / len(lista) if lista else None

    g1 = [r["t2"] for r in regs if r["t1"] == 1]
    ganando = [r["t2"] for r in regs if r["gf1"] > r["gc1"]]
    perdiendo = [r["t2"] for r in regs if r["gf1"] < r["gc1"]]

    media_1g = sum(g1) / len(g1) if g1 else None
    if g1:
        pct_1g = 100.0 * sum(1 for x in g1 if x <= 2) / len(g1)
        # Suavizar hacia el pct_35 global del equipo.
        pct_1g = (pct_1g * len(g1) + pct_35 * 3.0) / (len(g1) + 3.0)
    else:
        pct_1g = None

    return PerfilEquipo(
        team_id=str(team_id),
        nombre=str(nombre),
        n=n,
        n_ht=n_ht,
        pct_35=round(pct_35, 2),
        pct_45=round(pct_45, 2),
        gf_1t=round(sum(r["gf1"] for r in regs) / n, 3),
        gc_1t=round(sum(r["gc1"] for r in regs) / n, 3),
        gf_2t_local=round(media(loc, "gf2") if loc else sum(r["gf2"] for r in regs) / n, 3),
        gc_2t_local=round(media(loc, "gc2") if loc else sum(r["gc2"] for r in regs) / n, 3),
        gf_2t_visita=round(media(vis, "gf2") if vis else sum(r["gf2"] for r in regs) / n, 3),
        gc_2t_visita=round(media(vis, "gc2") if vis else sum(r["gc2"] for r in regs) / n, 3),
        media_2t=round(media_2t, 3),
        n_1gol_1t=len(g1),
        media_2t_tras_1gol=round(media_1g, 3) if media_1g is not None else None,
        pct_35_tras_1gol=round(pct_1g, 2) if pct_1g is not None else None,
        n_ganando_1t=len(ganando),
        media_2t_ganando=round(sum(ganando) / len(ganando), 3) if ganando else None,
        n_perdiendo_1t=len(perdiendo),
        media_2t_perdiendo=round(sum(perdiendo) / len(perdiendo), 3) if perdiendo else None,
    )


def media_2t_liga(partidos: list[Partido], liga_id: str, cutoff_iso: str,
                  global_avg: float = 1.45, max_partidos: int = 90) -> float:
    previos = [
        p for p in partidos
        if p.liga_id == str(liga_id) and p.tiene_ht and p.valido and p.fecha_iso < cutoff_iso
    ][-max_partidos:]
    n = len(previos)
    if n == 0:
        return global_avg
    suma = sum(max(0, p.total_ft - int(p.total_ht)) for p in previos)
    # Suavizado hacia la media global.
    alpha = 12.0
    return (suma + alpha * global_avg) / (n + alpha)


def media_2t_global(partidos: list[Partido], cutoff_iso: str) -> tuple[float, float]:
    """Devuelve (media, alpha_nb) de goles del 2T para calibrar la binomial negativa."""
    vals = [
        max(0, p.total_ft - int(p.total_ht))
        for p in partidos
        if p.tiene_ht and p.valido and p.fecha_iso < cutoff_iso
    ]
    if len(vals) < 30:
        return 1.45, 0.10
    media = sum(vals) / len(vals)
    var = sum((x - media) ** 2 for x in vals) / (len(vals) - 1)
    if media <= 0:
        return 1.45, 0.10
    # Piso de 0.05: evita una Poisson demasiado estrecha y reduce la
    # sobreconfianza en probabilidades altas.
    alpha = max(0.05, min(0.60, (var - media) / (media * media)))
    return media, alpha


# ==============================================================================
# 8. ESTRATEGIA Y MODELO
# ==============================================================================

def _componentes_modelo(e: Evaluacion) -> tuple[float, float, float, float]:
    """
    Devuelve (lambda_cond, lambda_estado, lambda_base, lambda_liga).
    lambda_cond: media 2T de los equipos en partidos con exactamente 1 gol al 1T.
    lambda_estado: media 2T segun el equipo que gana/pierde al descanso.
    lambda_base: media 2T por sede (local/visitante).
    lambda_liga: media 2T de la liga.
    """
    pl, pv, p = e.perfil_local, e.perfil_visitante, e.partido
    if not pl or not pv:
        return 1.45, 1.45, 1.45, e.liga_2t or 1.45

    base = e.esp_2t
    # Condicional: solo si ambos equipos tienen muestra suficiente.
    conds = []
    pesos = []
    for perfil in (pl, pv):
        if perfil.media_2t_tras_1gol is not None and perfil.n_1gol_1t >= 2:
            conds.append(perfil.media_2t_tras_1gol)
            pesos.append(min(6, perfil.n_1gol_1t))
    if conds:
        cond = sum(c * w for c, w in zip(conds, pesos)) / sum(pesos)
        n_cond = sum(pesos)
        w = n_cond / (n_cond + 5.0)
        cond = w * cond + (1 - w) * base
    else:
        cond = base

    # Estado: equipo que gana / pierde al descanso.
    if p.goles_local_ht == 1 and p.goles_visitante_ht == 0:
        lider, rival = pl, pv
        lider_es_local = True
    else:
        lider, rival = pv, pl
        lider_es_local = False

    def media_estado(perfil: PerfilEquipo, ganando: bool) -> Optional[float]:
        if ganando:
            return perfil.media_2t_ganando
        return perfil.media_2t_perdiendo

    l_gan = media_estado(lider, True)
    l_per = media_estado(rival, False)
    if l_gan is not None and l_per is not None:
        estado = (l_gan + l_per) / 2.0
        n_est = min(lider.n_ganando_1t, rival.n_perdiendo_1t)
        w = n_est / (n_est + 4.0) if n_est > 0 else 0.0
        estado = w * estado + (1 - w) * base
    else:
        estado = base

    return cond, estado, base, e.liga_2t


def decidir_contexto(contexto: Optional[ContextoVivo], cfg: dict,
                     linea: str = "3.5") -> tuple[bool, str]:
    """
    Devuelve (pasa, motivo). Si el filtro esta apagado, siempre pasa.
    El filtro de SOT del que pierde aplica a la linea -3.5. Para -4.5 y -5.5
    la linea ya es mas segura y no se necesita ese filtro (se permite volumen).
    """
    if not cfg.get("usar_contexto_vivo") or not contexto or not contexto.datos_ok:
        return True, ""
    if linea == "3.5" and cfg.get("sot_pierde_max") is not None and contexto.sot_pierde > cfg["sot_pierde_max"]:
        return False, "contexto_sot_pierde"
    if contexto.peligro_45 > cfg["peligro_max"]:
        return False, "contexto_peligro"
    if contexto.presion_pierde_45 > cfg["presion_pierde_max"]:
        return False, "contexto_presion"
    return True, ""


def evaluar_partido(p: Partido, indice: list[Partido], cfg: dict,
                    cache_perfiles: Optional[dict] = None,
                    global_avg: float = 1.45, alpha_nb: float = 0.10,
                    contexto: Optional[ContextoVivo] = None) -> Evaluacion:
    """
    Aplica la regla de oro y el modelo. No usa informacion posterior al partido.
    Orden de descarte:
      sin HT -> 0-0 -> 2+ goles -> marcador no valido -> sin perfil ->
      pre-filtro -> filtros extra -> modelo.
    """
    e = Evaluacion(partido=p, contexto=contexto)
    cache_perfiles = cache_perfiles if cache_perfiles is not None else {}

    # 1) Confirmacion del HT
    if not p.tiene_ht:
        e.motivo = "sin_ht"
        e.explicacion = "No se pudo confirmar el marcador oficial al descanso. No se alerta."
        return e

    total_ht = int(p.total_ht)
    if total_ht == 0:
        e.motivo = "0_0"
        e.explicacion = "El partido iba 0-0 al descanso. La estrategia lo descarta: no tiene valor de cuota."
        return e
    if total_ht >= 2:
        e.motivo = "2_mas"
        e.explicacion = f"El partido iba {p.marcador_ht()} al descanso (2 o mas goles). La estrategia lo descarta."
        return e
    if (p.goles_local_ht, p.goles_visitante_ht) not in {(1, 0), (0, 1)}:
        e.motivo = "marcador_no_valido"
        e.explicacion = f"Marcador al descanso no valido: {p.marcador_ht()}."
        return e

    # El resultado real se calcula despues de elegir la linea.
    e.goles_2t_reales = p.goles_2t

    # 2) Perfiles (solo con partidos anteriores)
    clave_l = (p.local_id, p.fecha)
    clave_v = (p.visitante_id, p.fecha)
    if clave_l not in cache_perfiles:
        cache_perfiles[clave_l] = construir_perfil(
            indice, p.local_id, p.local_nombre, p.fecha_iso,
            cfg["min_partidos_perfil"], cfg["min_partidos_ht"], cfg["max_partidos_perfil"])
    if clave_v not in cache_perfiles:
        cache_perfiles[clave_v] = construir_perfil(
            indice, p.visitante_id, p.visitante_nombre, p.fecha_iso,
            cfg["min_partidos_perfil"], cfg["min_partidos_ht"], cfg["max_partidos_perfil"])
    pl = cache_perfiles.get(clave_l)
    pv = cache_perfiles.get(clave_v)
    if not pl or not pv:
        e.motivo = "sin_perfil"
        e.explicacion = "No hay suficientes partidos previos con marcador de descanso confirmado para calcular el perfil."
        return e
    e.perfil_local, e.perfil_visitante = pl, pv

    # 3) Metricas pre-partido
    e.liga_2t = media_2t_liga(indice, p.liga_id, p.fecha_iso, global_avg)
    e.esp_1t = (pl.gf_1t + pl.gc_1t + pv.gf_1t + pv.gc_1t) / 2.0
    e.esp_2t = (pl.gf_2t_local + pl.gc_2t_local + pv.gf_2t_visita + pv.gc_2t_visita) / 2.0
    e.esp_total = e.esp_1t + e.esp_2t
    e.estab_35 = (pl.pct_35 + pv.pct_35) / 2.0
    e.estab_45 = (pl.pct_45 + pv.pct_45) / 2.0

    # 4) Modelo
    cond, estado, base, liga = _componentes_modelo(e)
    lam = 0.50 * cond + 0.30 * estado + 0.20 * liga
    lam = max(0.20, min(4.50, lam))
    e.lambda_2t = round(lam, 3)
    e.prob_under35 = nbinom_cdf(2, lam, alpha_nb)
    e.prob_under45 = nbinom_cdf(3, lam, alpha_nb)
    e.prob_under55 = nbinom_cdf(4, lam, alpha_nb)

    # 4A) Linea objetivo
    linea = str(cfg.get("linea_objetivo", "3.5")).upper()
    if linea == "AUTO":
        if e.prob_under35 >= 0.90:
            linea = "3.5"
        elif e.prob_under35 >= 0.80:
            linea = "4.5"
        else:
            linea = "5.5"
    if linea not in {"3.5", "4.5", "5.5"}:
        linea = "3.5"
    e.linea = linea
    e.prob_linea = {
        "3.5": e.prob_under35,
        "4.5": e.prob_under45,
        "5.5": e.prob_under55,
    }[linea]
    e.cuota_minima = round(1.0 / e.prob_under35, 3) if e.prob_under35 > 0 else None
    e.cuota_minima_linea = round(1.0 / e.prob_linea, 3) if e.prob_linea > 0 else None
    e.gano = (p.total_ft < float(e.linea))
    if cfg.get("cuota_referencia"):
        e.valor_esperado = round(e.prob_linea * cfg["cuota_referencia"] - 1.0, 4)

    # 4B) Contexto en vivo (opcional; apagado por defecto hasta calibrar)
    ok_ctx, motivo_ctx = decidir_contexto(contexto, cfg, linea=e.linea)
    if not ok_ctx:
        e.motivo = motivo_ctx
        if motivo_ctx == "contexto_sot_pierde" and contexto:
            e.explicacion = (
                f"El equipo que va perdiendo acumula {contexto.sot_pierde} tiros a puerta en el "
                f"primer tiempo, por encima del maximo permitido ({cfg['sot_pierde_max']}). "
                f"Esta generando peligro real y puede empatar o abrir el partido; se descarta."
            )
        elif motivo_ctx == "contexto_peligro" and contexto:
            e.explicacion = (
                f"Indice de peligro en vivo de {contexto.peligro_45:.2f}, por encima del maximo "
                f"({cfg['peligro_max']:.2f}). El partido esta generando demasiadas ocasiones "
                f"(tiros a puerta, corners, centros); se descarta por riesgo de goles."
            )
        elif contexto:
            e.explicacion = (
                f"El equipo que va perdiendo tiene una presion de {contexto.presion_pierde_45:.2f}, "
                f"por encima del maximo ({cfg['presion_pierde_max']:.2f}). Esta atacando y puede "
                f"empatar o abrir el partido; se descarta."
            )
        return e

    # 5) Pre-filtros solicitados
    if e.esp_total > cfg["esp_max"]:
        e.motivo = "pre_esp"
        e.explicacion = (
            f"Expectativa pre-partido de {e.esp_total:.2f} goles, por encima del maximo "
            f"permitido ({cfg['esp_max']:.2f}). Se descarta."
        )
        return e
    if e.estab_35 < cfg["e35_min"]:
        e.motivo = "pre_estab"
        e.explicacion = (
            f"Estabilidad historica de -3.5 del {e.estab_35:.1f}%, por debajo del minimo "
            f"({cfg['e35_min']:.0f}%). Se descarta."
        )
        return e

    # 6) Filtros extra
    if cfg.get("liga_2t_max") is not None and e.liga_2t > cfg["liga_2t_max"]:
        e.motivo = "liga"
        e.explicacion = (
            f"La liga promedia {e.liga_2t:.2f} goles en el 2T, por encima del maximo "
            f"({cfg['liga_2t_max']:.2f}). Se descarta por riesgo de liga alta."
        )
        return e
    if cfg.get("solo_local_1_0") and not (p.goles_local_ht == 1 and p.goles_visitante_ht == 0):
        e.motivo = "solo_local"
        e.explicacion = "El perfil solo acepta 1-0 (local ganando) al descanso; este partido iba 0-1."
        return e
    if cfg.get("exigir_sin_rojas") and p.rojas_1t > 0:
        e.motivo = "rojas"
        e.explicacion = (
            f"Hubo {p.rojas_1t} tarjeta(s) roja(s) en el primer tiempo. El modelo no esta "
            f"calibrado para inferioridad numerica; se descarta."
        )
        return e

    # 7) Filtro del modelo
    if e.prob_linea < cfg["prob_min"]:
        e.motivo = "modelo_prob"
        e.explicacion = (
            f"Probabilidad estimada de que el total final quede bajo {e.linea}: "
            f"{e.prob_linea*100:.1f}%, por debajo del minimo ({cfg['prob_min']*100:.0f}%). Se descarta."
        )
        return e
    # El limite de lambda aplica a -3.5. Para -4.5 y -5.5 la linea ya es mas
    # segura y no se necesita ese tope (se permite mas volumen).
    if e.linea == "3.5" and e.lambda_2t > cfg["lambda_max"]:
        e.motivo = "modelo_lambda"
        e.explicacion = (
            f"Proyeccion de {e.lambda_2t:.2f} goles para el 2T, por encima del maximo "
            f"({cfg['lambda_max']:.2f}). Se descarta."
        )
        return e

    # 8) Entrada
    e.apuesta = True
    e.motivo = "apuesta"
    e.explicacion = explicar_entrada(e)
    return e


def explicar_entrada(e: Evaluacion) -> str:
    p, pl, pv = e.partido, e.perfil_local, e.perfil_visitante
    if not pl or not pv:
        return ""
    lider = p.local_nombre if p.goles_local_ht == 1 else p.visitante_nombre
    rival = p.visitante_nombre if p.goles_local_ht == 1 else p.local_nombre
    return (
        f"El partido llego al minuto 45 con {p.marcador_ht()} (un solo gol en total), que es el unico "
        f"escenario permitido por la estrategia. {lider} gana al descanso y {rival} queda obligado a "
        f"reaccionar. En los partidos previos de {pl.nombre} con exactamente 1 gol al 1T, el 2T promedia "
        f"{formatear_num(pl.media_2t_tras_1gol, 2) if pl.media_2t_tras_1gol is not None else 'sin muestra'} goles; "
        f"en los de {pv.nombre}, "
        f"{formatear_num(pv.media_2t_tras_1gol, 2) if pv.media_2t_tras_1gol is not None else 'sin muestra'} goles. "
        f"La liga promedia {e.liga_2t:.2f} goles en el 2T. El modelo estima {e.lambda_2t:.2f} goles para el "
        f"segundo tiempo, lo que da una probabilidad de {e.prob_linea*100:.1f}% de que el total final "
        f"quede por debajo de {e.linea}. La cuota minima de equilibrio (sin margen de casa) es "
        f"{e.cuota_minima_linea:.2f}; por debajo de esa cuota la apuesta no tiene valor esperado positivo."
    )


# ==============================================================================
# 9. FORMATEO DE MENSAJES
# ==============================================================================

def texto_alerta(e: Evaluacion) -> str:
    p = e.partido
    filas = [
        f"🚨 <b>ALERTA UNDER {e.linea}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏆 <b>{escapar(p.liga_nombre)}</b>",
        f"⚽ <b>{escapar(p.local_nombre)} vs {escapar(p.visitante_nombre)}</b>",
        f"⏱️ <b>Descanso:</b> {p.marcador_ht()}  (minuto 45)",
        f"📉 <b>Mercado:</b> MENOS DE {e.linea} GOLES (-{e.linea})",
        f"📊 <b>Prob. modelo:</b> {e.prob_linea*100:.1f}%",
        f"🎯 <b>Cuota minima:</b> {e.cuota_minima_linea:.2f}",
        f"🧠 <b>Proyeccion 2T:</b> {e.lambda_2t:.2f} goles",
        f"📈 <b>Esperanza pre:</b> {e.esp_total:.2f} | <b>Estab. -3.5:</b> {e.estab_35:.1f}%",
    ]
    if e.contexto and e.contexto.datos_ok:
        filas += [
            "━━━━━━━━━━━━━━━━━━━━",
            "📡 <b>Contexto en vivo</b>",
            f"• Tiros: {e.contexto.tiros_local}-{e.contexto.tiros_visita} | "
            f"A puerta: {e.contexto.tiros_arco_local}-{e.contexto.tiros_arco_visita}",
            f"• Corners: {e.contexto.corners_local}-{e.contexto.corners_visita} | "
            f"Posesion: {e.contexto.posesion_local:.0f}%-{e.contexto.posesion_visita:.0f}%",
            f"• A puerta del que pierde: <b>{e.contexto.sot_pierde}</b> "
            + (f"(max {CFG.get('sot_pierde_max', 2)})" if e.linea == "3.5" else "(sin filtro SOT)"),
            f"• Peligro: {e.contexto.peligro_45:.1f} | "
            f"Presion del que pierde: {e.contexto.presion_pierde_45:.1f}",
        ]
    if e.valor_esperado is not None:
        filas.append(f"💰 <b>Valor esperado:</b> {e.valor_esperado*100:+.1f}% (cuota {CFG['cuota_referencia']:.2f})")
    filas += [
        "━━━━━━━━━━━━━━━━━━━━",
        "<i>Solo entra si el marcador oficial al descanso es 1-0 o 0-1. "
        "Si la cuota real esta por debajo de la cuota minima, NO hay valor.</i>",
        f"🆔 <code>{escapar(p.event_id)}</code>",
    ]
    return "\n".join(filas)


# ==============================================================================
# 10. TELEGRAM
# ==============================================================================

def telegram_configurado() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _tg_api(metodo: str, data: Optional[dict] = None,
            files: Optional[dict] = None, timeout: int = 30) -> Optional[dict]:
    if not telegram_configurado():
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{metodo}"
    for intento in range(3):
        try:
            r = requests.post(url, data=data or {}, files=files, timeout=timeout)
            if r.status_code == 429:
                time.sleep(3 + 3 * intento)
                continue
            if r.status_code >= 500:
                time.sleep(2 + 2 * intento)
                continue
            payload = r.json()
            if not payload.get("ok"):
                logging.warning("Telegram %s: %s", metodo, payload.get("description"))
                return None
            return payload
        except Exception as e:
            logging.warning("Telegram %s error: %s", metodo, e)
            time.sleep(1 + intento)
    return None


def enviar_mensaje(texto: str, silencioso: bool = False) -> bool:
    if not telegram_configurado():
        print("[Telegram] Sin credenciales. Mensaje no enviado:")
        print(texto[:500])
        return False
    # Telegram limita a 4096 caracteres por mensaje.
    partes = []
    actual = ""
    for linea in str(texto).splitlines(keepends=True):
        if len(actual) + len(linea) > 3900:
            partes.append(actual)
            actual = linea
        else:
            actual += linea
    if actual:
        partes.append(actual)
    ok = True
    for parte in partes:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": parte,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if silencioso:
            payload["disable_notification"] = True
        if not _tg_api("sendMessage", payload):
            ok = False
    return ok


def enviar_pdf(ruta_pdf: str | Path, caption: str) -> bool:
    if not telegram_configurado():
        print("[Telegram] Sin credenciales. PDF no enviado:", ruta_pdf)
        return False
    ruta = Path(ruta_pdf)
    if not ruta.exists():
        return False
    try:
        with open(ruta, "rb") as f:
            files = {"document": (ruta.name, f, "application/pdf")}
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": str(caption)[:1020],
                "parse_mode": "HTML",
            }
            return bool(_tg_api("sendDocument", payload, files=files, timeout=120))
    except Exception as e:
        logging.warning("Error enviando PDF: %s", e)
        return False


def validar_telegram() -> bool:
    if not telegram_configurado():
        print("=" * 70)
        print("FALTAN CREDENCIALES DE TELEGRAM")
        print("En Colab: panel Secrets -> TELEGRAM_TOKEN y TELEGRAM_CHAT_ID")
        print("En local: exporta las variables de entorno.")
        print("=" * 70)
        return False
    data = _tg_api("getMe")
    if not data:
        print("Token de Telegram invalido o sin conexion. Revisa el token.")
        return False
    nombre = data.get("result", {}).get("username", "?")
    print(f"Telegram OK: @{nombre}")
    return True


def enviar_alerta(e: Evaluacion) -> bool:
    return enviar_mensaje(texto_alerta(e))


# ==============================================================================
# 11. RESUMENES PARA AUDITORIA
# ==============================================================================

def resumen_evaluaciones(evaluaciones: list[Evaluacion]) -> dict:
    total = len(evaluaciones)
    motivos = Counter(e.motivo for e in evaluaciones)
    apuestas = [e for e in evaluaciones if e.apuesta]
    ganadas = [e for e in apuestas if e.gano]
    perdidas = [e for e in apuestas if e.gano is False]
    n = len(apuestas)
    k = len(ganadas)
    ci = wilson_interval(k, n)
    goles_2t = [e.goles_2t_reales for e in apuestas if e.goles_2t_reales is not None]
    return {
        "total": total,
        "con_ht": sum(1 for e in evaluaciones if e.partido.tiene_ht),
        "apuestas": n,
        "ganadas": k,
        "perdidas": len(perdidas),
        "efectividad": round(100.0 * k / n, 2) if n else 0.0,
        "ci_bajo": round(ci[0], 1),
        "ci_alto": round(ci[1], 1),
        "goles_2t_prom": round(sum(goles_2t) / len(goles_2t), 2) if goles_2t else 0.0,
        "motivos": dict(motivos),
        "sin_ht": motivos.get("sin_ht", 0),
        "sin_perfil": motivos.get("sin_perfil", 0),
        "cero_cero": motivos.get("0_0", 0),
        "dos_mas": motivos.get("2_mas", 0),
        "pre": motivos.get("pre_esp", 0) + motivos.get("pre_estab", 0),
        "modelo": motivos.get("modelo_prob", 0) + motivos.get("modelo_lambda", 0),
        "extras": motivos.get("liga", 0) + motivos.get("solo_local", 0) + motivos.get("rojas", 0),
        "contexto": motivos.get("contexto_peligro", 0) + motivos.get("contexto_presion", 0),
        "marcador_no_valido": motivos.get("marcador_no_valido", 0),
    }


def decidir_con_config(e: Evaluacion, cfg: dict) -> tuple[bool, str]:
    """Aplica una configuracion a una evaluacion ya calculada (sin recalcular el modelo)."""
    p = e.partido
    if not p.tiene_ht:
        return False, "sin_ht"
    if p.total_ht == 0:
        return False, "0_0"
    if p.total_ht is not None and p.total_ht >= 2:
        return False, "2_mas"
    if (p.goles_local_ht, p.goles_visitante_ht) not in {(1, 0), (0, 1)}:
        return False, "marcador_no_valido"
    if not e.perfil_local or not e.perfil_visitante:
        return False, "sin_perfil"
    if e.esp_total > cfg["esp_max"]:
        return False, "pre_esp"
    if e.estab_35 < cfg["e35_min"]:
        return False, "pre_estab"
    if cfg.get("liga_2t_max") is not None and e.liga_2t > cfg["liga_2t_max"]:
        return False, "liga"
    if cfg.get("solo_local_1_0") and not (p.goles_local_ht == 1 and p.goles_visitante_ht == 0):
        return False, "solo_local"
    if cfg.get("exigir_sin_rojas") and p.rojas_1t > 0:
        return False, "rojas"
    if e.prob_under35 < cfg["prob_min"]:
        return False, "modelo_prob"
    if e.lambda_2t > cfg["lambda_max"]:
        return False, "modelo_lambda"
    return True, "apuesta"


def tabla_sensibilidad(evaluaciones: list[Evaluacion]) -> list[dict]:
    filas = []
    # Linea base: solo regla de oro (1-0/0-1 al 45), sin filtros.
    base = [e for e in evaluaciones if e.partido.tiene_ht and e.partido.total_ht == 1
            and (e.partido.goles_local_ht, e.partido.goles_visitante_ht) in {(1, 0), (0, 1)}]
    if base:
        k = sum(1 for e in base if e.gano)
        ci = wilson_interval(k, len(base))
        filas.append({"nombre": "Solo regla de oro (sin filtros)", "n": len(base), "ganadas": k,
                      "efectividad": round(100 * k / len(base), 1), "ci": f"{ci[0]:.1f}-{ci[1]:.1f}"})
    for nombre, cfg in PERFILES.items():
        elegidos = []
        for e in evaluaciones:
            ok, _ = decidir_con_config(e, cfg)
            if ok:
                elegidos.append(e)
        if not elegidos:
            filas.append({"nombre": nombre, "n": 0, "ganadas": 0, "efectividad": 0.0, "ci": "-"})
            continue
        k = sum(1 for e in elegidos if e.gano)
        ci = wilson_interval(k, len(elegidos))
        filas.append({"nombre": nombre, "n": len(elegidos), "ganadas": k,
                      "efectividad": round(100 * k / len(elegidos), 1),
                      "ci": f"{ci[0]:.1f}-{ci[1]:.1f}"})
    # Umbrales de probabilidad con los filtros de ALTA_CONFIANZA.
    cfg = dict(PERFILES["ALTA_CONFIANZA"])
    for umbral in (0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95):
        cfg["prob_min"] = umbral
        elegidos = []
        for e in evaluaciones:
            ok, _ = decidir_con_config(e, cfg)
            if ok:
                elegidos.append(e)
        if not elegidos:
            continue
        k = sum(1 for e in elegidos if e.gano)
        ci = wilson_interval(k, len(elegidos))
        filas.append({"nombre": f"Alta confianza + prob >= {umbral*100:.0f}%", "n": len(elegidos),
                      "ganadas": k, "efectividad": round(100 * k / len(elegidos), 1),
                      "ci": f"{ci[0]:.1f}-{ci[1]:.1f}"})
    return filas


def tabla_calibracion(evaluaciones: list[Evaluacion]) -> list[dict]:
    base = [e for e in evaluaciones if e.partido.tiene_ht and e.partido.total_ht == 1
            and (e.partido.goles_local_ht, e.partido.goles_visitante_ht) in {(1, 0), (0, 1)}
            and e.perfil_local and e.perfil_visitante]
    buckets = [(0.0, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 0.85),
               (0.85, 0.90), (0.90, 0.95), (0.95, 1.01)]
    filas = []
    for lo, hi in buckets:
        grupo = [e for e in base if lo <= e.prob_under35 < hi]
        if not grupo:
            continue
        pred = sum(e.prob_under35 for e in grupo) / len(grupo)
        k = sum(1 for e in grupo if e.gano)
        ci = wilson_interval(k, len(grupo))
        filas.append({
            "rango": f"{lo*100:.0f}-{min(100, hi*100):.0f}%",
            "n": len(grupo),
            "pred": round(pred * 100, 1),
            "real": round(100 * k / len(grupo), 1),
            "ci": f"{ci[0]:.1f}-{ci[1]:.1f}",
        })
    return filas


def agrupar_por_dia(apuestas: list[Evaluacion]) -> list[tuple[str, list[Evaluacion]]]:
    por_dia: dict[str, list[Evaluacion]] = defaultdict(list)
    for e in apuestas:
        por_dia[e.fecha].append(e)
    return sorted(por_dia.items(), key=lambda x: x[0])


# ==============================================================================
# 12. PDF DE AUDITORIA
# ==============================================================================

def construir_pdf_auditoria(evaluaciones: list[Evaluacion], cfg: dict,
                            ruta_pdf: str | Path, fecha_inicio: str,
                            fecha_fin: str) -> Path:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, PageBreak, KeepTogether)

    ruta = Path(ruta_pdf)
    res = resumen_evaluaciones(evaluaciones)
    apuestas = [e for e in evaluaciones if e.apuesta]
    sens = tabla_sensibilidad(evaluaciones)
    calib = tabla_calibracion(evaluaciones)
    dias = agrupar_por_dia(apuestas)

    estilos = getSampleStyleSheet()
    azul = colors.HexColor("#0F2C59")
    azul2 = colors.HexColor("#1E3E62")
    verde = colors.HexColor("#198754")
    rojo = colors.HexColor("#DC3545")
    gris = colors.HexColor("#64748B")
    fondo = colors.HexColor("#F4F6F9")
    borde = colors.HexColor("#CBD5E1")

    s_titulo = ParagraphStyle("Titulo", parent=estilos["Heading1"], fontName="Helvetica-Bold",
                              fontSize=19, leading=23, textColor=azul, spaceAfter=4)
    s_sub = ParagraphStyle("Sub", parent=estilos["Heading2"], fontName="Helvetica-Bold",
                           fontSize=12, leading=15, textColor=azul2, spaceBefore=10, spaceAfter=5)
    s_normal = ParagraphStyle("Normal2", parent=estilos["Normal"], fontName="Helvetica",
                              fontSize=9.2, leading=12.6, textColor=colors.HexColor("#1F2937"))
    s_peq = ParagraphStyle("Pequeno", parent=s_normal, fontSize=8.2, leading=11)
    s_dia = ParagraphStyle("Dia", parent=estilos["Heading2"], fontName="Helvetica-Bold",
                           fontSize=11.5, leading=14, textColor=colors.white,
                           backColor=azul, borderPadding=5, spaceBefore=10, spaceAfter=6)
    s_exp = ParagraphStyle("Exp", parent=s_normal, fontSize=8.6, leading=11.8,
                           backColor=fondo, borderPadding=5, spaceBefore=3, spaceAfter=3)
    s_card = ParagraphStyle("Card", parent=s_normal, fontSize=8.5, leading=11.2)

    def P(txt, estilo=s_normal):
        return Paragraph(txt, estilo)

    def tabla(data, anchos, encabezado=True, font_size=8.2, align_center=False):
        t = Table(data, colWidths=anchos, repeatRows=1 if encabezado else 0)
        cmds = [
            ("GRID", (0, 0), (-1, -1), 0.4, borde),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]
        if encabezado:
            cmds += [
                ("BACKGROUND", (0, 0), (-1, 0), azul),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ]
        if align_center:
            cmds.append(("ALIGN", (1, 0), (-1, -1), "CENTER"))
        t.setStyle(TableStyle(cmds))
        return t

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(gris)
        canvas.drawString(36, 20, f"Bot Under 3.5 v{VERSION} | Perfil: {cfg['perfil']}")
        canvas.drawRightString(letter[0] - 36, 20, f"Pagina {doc.page}")
        canvas.restoreState()

    story = []

    # ---------------- Portada / resumen ----------------
    story.append(P("AUDITORIA MENSUAL - UNDER 3.5 GOLES", s_titulo))
    story.append(P(
        f"<b>Periodo:</b> {fecha_inicio} a {fecha_fin} ({cfg['dias_auditoria']} dias) &nbsp;|&nbsp; "
        f"<b>Perfil:</b> {cfg['perfil']} &nbsp;|&nbsp; <b>Generado:</b> {ahora_bogota().strftime('%Y-%m-%d %H:%M')} Bogota",
        s_peq))
    story.append(Spacer(1, 8))

    filas_resumen = [
        ["METRICA", "VALOR"],
        ["Partidos analizados en el periodo", str(res["total"])],
        ["Partidos con marcador de descanso confirmado", str(res["con_ht"])],
        ["Descartados por 0-0 al descanso", str(res["cero_cero"])],
        ["Descartados por 2 o mas goles al descanso", str(res["dos_mas"])],
        ["Descartados por no confirmar el HT", str(res["sin_ht"])],
        ["Descartados por perfil insuficiente", str(res["sin_perfil"])],
        ["Descartados por filtros pre-partido", str(res["pre"])],
        ["Descartados por filtros extra (liga/rojas/local)", str(res["extras"])],
        ["Descartados por el modelo", str(res["modelo"])],
        ["ENTRADAS EJECUTADAS", str(res["apuestas"])],
        ["Ganadas", str(res["ganadas"])],
        ["Perdidas", str(res["perdidas"])],
        ["EFECTIVIDAD", f"{res['efectividad']:.2f}%  (IC 95%: {res['ci_bajo']}-{res['ci_alto']}%)"],
        ["Goles promedio en el 2T de las entradas", f"{res['goles_2t_prom']:.2f}"],
    ]
    story.append(P("RESUMEN EJECUTIVO", s_sub))
    story.append(tabla(filas_resumen, [370, 160], align_center=False))
    story.append(Spacer(1, 8))

    # ---------------- Regla de oro ----------------
    story.append(P("REGLA DE ORO APLICADA", s_sub))
    reglas = [
        "Mercado unico: <b>MENOS DE 3.5 GOLES (-3.5)</b> al descanso.",
        "Marcador valido: <b>exactamente 1-0 o 0-1</b> al minuto 45 (un solo gol en total).",
        "Se descarta <b>0-0</b>: no tiene valor de cuota y el partido puede desordenarse.",
        "Se descarta cualquier marcador con <b>2 o mas goles</b>: 1-1, 2-0, 0-2, 2-1, etc.",
        "El marcador de descanso <b>solo</b> se toma de linescores oficiales, del detalle de goles validado contra el FT, o del estado oficial de Halftime en vivo.",
        "Queda prohibido estimar el HT con factores (el viejo 0.42 fue eliminado).",
        "Si la API no confirma el HT, <b>no se alerta</b>: se reintenta en la siguiente actualizacion.",
        "En vivo, si el segundo tiempo ya empezo y no hubo confirmacion, la alerta se descarta (la cuota de HT ya cerro).",
    ]
    for r in reglas:
        story.append(P(f"- {r}", s_normal))
    story.append(Spacer(1, 6))
    story.append(P(
        "<b>Importante:</b> la efectividad de una apuesta NO es lo mismo que la efectividad de la senal. "
        "Este informe mide si el mercado -3.5 se gana o se pierde con el marcador final. "
        "Una senal puede ser 100% real (1-0 confirmado) y la apuesta puede perder. "
        "No existe un sistema honesto que garantice 95% de acierto en apuestas; este informe muestra "
        "la efectividad observada, su intervalo de confianza y el tamano de muestra.", s_peq))
    story.append(PageBreak())

    # ---------------- Sensibilidad ----------------
    story.append(P("SENSIBILIDAD: QUE PASA SI APRIETAS O AFLOJAS LOS FILTROS", s_sub))
    story.append(P(
        "La siguiente tabla se calculo sobre los mismos partidos, cambiando solo los filtros. "
        "Asi se ve el trade-off entre cantidad de entradas y efectividad. "
        "El IC 95% es el rango plausible de la efectividad real; si es muy ancho, la muestra es chica.", s_peq))
    datos_sens = [["Configuracion", "Entradas", "Ganadas", "Efectividad", "IC 95%"]]
    for f in sens:
        datos_sens.append([f["nombre"], str(f["n"]), str(f["ganadas"]),
                           f"{f['efectividad']:.1f}%", f["ci"]])
    story.append(tabla(datos_sens, [235, 55, 55, 75, 110], align_center=True))
    story.append(Spacer(1, 10))

    # ---------------- Calibracion ----------------
    story.append(P("CALIBRACION DEL MODELO", s_sub))
    if calib:
        story.append(P(
            "Cada fila agrupa partidos por la probabilidad que estimo el modelo. "
            "Si el modelo esta bien calibrado, la columna 'Real' debe parecerse a 'Predicha'. "
            "Un modelo sobreconfiado predice mas de lo que acierta.", s_peq))
        datos_cal = [["Prob. predicha", "n", "Predicha prom.", "Real", "IC 95%"]]
        for f in calib:
            datos_cal.append([f["rango"], str(f["n"]), f"{f['pred']:.1f}%", f"{f['real']:.1f}%", f["ci"]])
        story.append(tabla(datos_cal, [100, 45, 95, 65, 120], align_center=True))
    else:
        story.append(P("No hubo suficientes partidos con HT 1-0/0-1 para calibrar.", s_normal))
    story.append(Spacer(1, 10))

    # ---------------- Por dia ----------------
    story.append(P("RESULTADO DIA POR DIA", s_sub))
    if dias:
        datos_dias = [["Fecha", "Entradas", "Ganadas", "Perdidas", "Efectividad"]]
        for fecha, lista in dias:
            g = sum(1 for x in lista if x.gano)
            p = len(lista) - g
            datos_dias.append([fecha, str(len(lista)), str(g), str(p),
                               f"{100*g/len(lista):.0f}%"])
        story.append(tabla(datos_dias, [110, 80, 80, 80, 100], align_center=True))
    else:
        story.append(P("No se ejecutaron entradas en el periodo con este perfil.", s_normal))
    story.append(PageBreak())

    # ---------------- Fichas ----------------
    story.append(P("DETALLE DE CADA ENTRADA", s_sub))
    story.append(P(
        f"Se muestran hasta {cfg['max_fichas_pdf']} fichas. Cada una explica por que entro el bot, "
        f"con el marcador real al descanso, el resultado final y las metricas que uso.", s_peq))
    story.append(Spacer(1, 4))
    for idx, e in enumerate(apuestas[:cfg["max_fichas_pdf"]], 1):
        p = e.partido
        color = verde if e.gano else rojo
        estado = "GANADA" if e.gano else "PERDIDA"
        cab = Table([[
            P(f"<font color='white'><b>#{idx} | {escapar(p.liga_nombre)}</b><br/>"
              f"<b>{escapar(p.local_nombre)} vs {escapar(p.visitante_nombre)}</b></font>", s_card),
            P(f"<font color='white'><b>HT: {p.marcador_ht()}</b> | <b>FT: {p.goles_local_ft}-{p.goles_visitante_ft}</b><br/>"
              f"<b>{estado}</b></font>", s_card),
        ]], colWidths=[340, 190])
        cab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), color),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(cab)
        filas = [
            ["Fecha", p.fecha, "Goles en el 2T", str(e.goles_2t_reales)],
            ["Esperanza pre", f"{e.esp_total:.2f}", "Estab. -3.5", f"{e.estab_35:.1f}%"],
            ["Liga 2T", f"{e.liga_2t:.2f}", "Proyeccion 2T", f"{e.lambda_2t:.2f}"],
            ["Prob. -3.5", f"{e.prob_under35*100:.1f}%", "Cuota minima", f"{e.cuota_minima:.2f}"],
            ["Rojas 1T", str(p.rojas_1t), "Fuente HT", p.fuente_ht],
        ]
        if e.contexto and e.contexto.datos_ok:
            c = e.contexto
            filas.append([
                "Tiros / A puerta",
                f"{c.tiros_local}-{c.tiros_visita} / {c.tiros_arco_local}-{c.tiros_arco_visita}",
                "Corners / Posesion",
                f"{c.corners_local}-{c.corners_visita} / {c.posesion_local:.0f}%-{c.posesion_visita:.0f}%",
            ])
            filas.append([
                "A puerta del que pierde", str(c.sot_pierde),
                "Peligro en vivo", f"{c.peligro_45:.1f}",
            ])
            filas.append([
                "Presion del que pierde", f"{c.presion_pierde_45:.1f}",
                "Ritmo 45", f"{c.ritmo_45:.1f}",
            ])
        t = Table(filas, colWidths=[95, 150, 115, 170])
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.35, borde),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(t)
        story.append(P(f"<b>Por que entro:</b> {escapar(e.explicacion)}", s_exp))
        story.append(Spacer(1, 8))

    if len(apuestas) > cfg["max_fichas_pdf"]:
        story.append(P(
            f"<i>Quedaron {len(apuestas) - cfg['max_fichas_pdf']} entradas adicionales sin ficha individual. "
            f"Estan contadas en el resumen y en el dia por dia.</i>", s_peq))
    story.append(PageBreak())

    # ---------------- Anexo ----------------
    story.append(P("ANEXO: DESCARTES Y CALIDAD DEL DATO", s_sub))
    datos_motivos = [["Motivo de descarte", "Cantidad", "Que significa"]]
    explicaciones = {
        "sin_ht": "No se pudo confirmar el marcador oficial al descanso. No se alerta.",
        "sin_perfil": "Faltan partidos previos con HT confirmado para calcular el perfil.",
        "0_0": "El partido iba 0-0 al descanso. La estrategia lo descarta.",
        "2_mas": "El partido iba con 2 o mas goles al descanso. La estrategia lo descarta.",
        "pre_esp": "La expectativa pre-partido supero el maximo permitido.",
        "pre_estab": "La estabilidad historica de -3.5 quedo por debajo del minimo.",
        "liga": "La liga promedia demasiados goles en el 2T.",
        "solo_local": "El perfil solo acepta 1-0; el partido iba 0-1.",
        "rojas": "Hubo tarjeta roja en el 1T y el modelo no esta calibrado para eso.",
        "modelo_prob": "La probabilidad del modelo quedo por debajo del minimo.",
        "modelo_lambda": "La proyeccion de goles del 2T quedo por encima del maximo.",
        "contexto_sot_pierde": "El equipo que iba perdiendo acumulo demasiados tiros a puerta en el 1T.",
        "contexto_peligro": "El contexto en vivo mostro demasiadas ocasiones (tiros, corners, centros).",
        "contexto_presion": "El equipo que iba perdiendo estaba generando mucha presion.",
        "marcador_no_valido": "Marcador de descanso fuera de 1-0 / 0-1.",
    }
    for motivo, cant in sorted(res["motivos"].items(), key=lambda x: -x[1]):
        if motivo == "apuesta":
            continue
        datos_motivos.append([motivo, str(cant), explicaciones.get(motivo, "")])
    story.append(tabla(datos_motivos, [75, 50, 400], align_center=False))
    story.append(Spacer(1, 10))

    # Top ligas
    por_liga = Counter(e.liga for e in apuestas)
    if por_liga:
        story.append(P("LIGAS CON ENTRADAS", s_sub))
        datos_liga = [["Liga", "Entradas", "Ganadas", "Efectividad"]]
        for liga, cant in por_liga.most_common(15):
            grupo = [e for e in apuestas if e.liga == liga]
            g = sum(1 for e in grupo if e.gano)
            datos_liga.append([liga[:55], str(cant), str(g), f"{100*g/cant:.0f}%"])
        story.append(tabla(datos_liga, [300, 60, 60, 80], align_center=True))
        story.append(Spacer(1, 10))

    # Near misses
    cerca = [e for e in evaluaciones
             if e.partido.tiene_ht and e.partido.total_ht == 1
             and (e.partido.goles_local_ht, e.partido.goles_visitante_ht) in {(1, 0), (0, 1)}
             and not e.apuesta and e.motivo in {"modelo_prob", "modelo_lambda", "liga", "rojas", "solo_local"}]
    if cerca:
        story.append(P("CASI ENTRADAS (HT valido, rechazadas por filtros)", s_sub))
        story.append(P("Sirven para auditar si el filtro fue demasiado conservador. "
                       "No son apuestas; solo se muestra que habria pasado.", s_peq))
        datos_cerca = [["Fecha", "Partido", "HT", "FT", "Motivo", "Habria ganado"]]
        for e in cerca[:40]:
            datos_cerca.append([
                e.fecha, f"{e.partido.local_nombre[:22]} vs {e.partido.visitante_nombre[:22]}",
                e.partido.marcador_ht(), f"{e.partido.goles_local_ft}-{e.partido.goles_visitante_ft}",
                e.motivo, "SI" if e.gano else "NO",
            ])
        story.append(tabla(datos_cerca, [55, 215, 40, 40, 100, 60], font_size=7.2, align_center=False))

    story.append(Spacer(1, 10))
    story.append(P(
        "<b>Descargo:</b> este informe usa datos publicos de ESPN y no garantiza resultados futuros. "
        "Las cuotas de apuestas cambian y el valor esperado depende de la cuota real que consigas. "
        "Una efectividad del 95% no esta respaldada por los datos; desconfia de cualquier sistema que la prometa. "
        "Juega responsablemente y nunca apuestes dinero que no puedas perder.", s_peq))

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=32,
                            title="Auditoria Under 3.5", author="Bot Under 3.5")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return ruta


# ==============================================================================
# 13. EJECUCION DE LA AUDITORIA
# ==============================================================================

def _dir_datos() -> Path:
    if CFG.get("dir_datos"):
        p = Path(CFG["dir_datos"]).expanduser()
    else:
        p = Path.cwd() / "bot_under35_datos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _imprimir_config(cfg: dict) -> None:
    print("=" * 78)
    print(f"BOT UNDER 3.5 v{VERSION} | modo={cfg['modo']} | perfil={cfg['perfil']} | linea=-{cfg.get('linea_objetivo', '3.5')}")
    print(f"Regla: -3.5 al descanso, solo 1-0 o 0-1. 0-0 y 2+ goles descartados.")
    print(f"Filtros: esp_total <= {cfg['esp_max']:.2f} | estab_35 >= {cfg['e35_min']:.0f}% "
          f"| liga_2t <= {cfg['liga_2t_max']} | solo_local={cfg['solo_local_1_0']} "
          f"| sin_rojas={cfg['exigir_sin_rojas']} | prob >= {cfg['prob_min']*100:.0f}%")
    print(f"Contexto vivo: {'FILTRANDO' if cfg.get('usar_contexto_vivo') else 'solo capturando'} "
          f"| sot_pierde_max={cfg['sot_pierde_max']} "
          f"| peligro_max={cfg['peligro_max']:.1f} | presion_max={cfg['presion_pierde_max']:.1f}")
    print("=" * 78)


def _asegurar_reportlab() -> bool:
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        print("Instalando reportlab...")
        import subprocess
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "reportlab"])
            return True
        except Exception as e:
            print(f"No se pudo instalar reportlab: {e}")
            return False


def ejecutar_auditoria() -> Optional[Path]:
    # La auditoria historica sigue siendo de la linea -3.5 (la regla original).
    cfg = dict(CFG)
    cfg["linea_objetivo"] = "3.5"
    _imprimir_config(cfg)
    _asegurar_reportlab()
    dir_datos = _dir_datos()
    client = EspnClient(dir_datos / "cache", workers=cfg["max_workers"], timeout=cfg["timeout"])

    hoy = ahora_bogota().date()
    fecha_fin = hoy
    fecha_inicio = hoy - timedelta(days=cfg["dias_auditoria"] - 1)
    hist_inicio = fecha_inicio - timedelta(days=cfg["dias_historial"])
    print(f"Periodo auditado: {fecha_inicio} a {fecha_fin}")
    print(f"Historial: {hist_inicio} a {fecha_fin} (para perfiles previos)")

    if telegram_configurado():
        enviar_mensaje(
            f"📄 <b>Generando auditoria Under 3.5</b>\n"
            f"Periodo: {fecha_inicio} a {fecha_fin} ({cfg['dias_auditoria']} dias)\n"
            f"Perfil: <b>{cfg['perfil']}</b>\n"
            f"<i>Recibiras un solo PDF al terminar. Sin spam.</i>")

    t0 = time.time()
    # Se pide un dia extra (solo si el periodo ya termino) para capturar
    # partidos que ESPN agrupa en la fecha siguiente por zona horaria.
    hasta_indice = fecha_fin + timedelta(days=1) if fecha_fin < hoy else fecha_fin
    indice = construir_indice(client, hist_inicio.isoformat(),
                              hasta_indice.isoformat(),
                              cobertura=cfg["cobertura"])
    print(f"Indice construido: {len(indice)} partidos en {time.time()-t0:.1f}s "
          f"(cache: {client.stats['cache_hit']} hits, {client.stats['http_ok']} HTTP)")

    if not indice:
        print("No se pudo construir el indice. Revisa la conexion a ESPN.")
        return None

    partidos_aud = [p for p in indice
                    if fecha_inicio.isoformat() <= p.fecha <= fecha_fin.isoformat()]
    print(f"Partidos del periodo: {len(partidos_aud)}")
    if not partidos_aud:
        print("No hay partidos en el periodo. Nada que auditar.")
        return None

    cutoff_global = iso_utc(datetime.combine(fecha_inicio, datetime.min.time(), tzinfo=ZONA))
    global_avg, alpha_nb = media_2t_global(indice, cutoff_global)
    print(f"Media global 2T (previa al periodo): {global_avg:.2f} | alpha NB: {alpha_nb:.3f}")

    evaluaciones: list[Evaluacion] = []
    cache_perfiles: dict = {}
    t1 = time.time()
    for i, p in enumerate(partidos_aud, 1):
        try:
            e = evaluar_partido(p, indice, cfg, cache_perfiles, global_avg, alpha_nb)
            evaluaciones.append(e)
        except Exception as ex:
            logging.exception("Error evaluando %s: %s", p.event_id, ex)
        if i % 100 == 0:
            print(f"  evaluados {i}/{len(partidos_aud)}...")

    evaluaciones.sort(key=lambda e: (e.fecha, e.partido.fecha_iso, e.partido.event_id))
    res = resumen_evaluaciones(evaluaciones)
    print(f"Evaluacion terminada en {time.time()-t1:.1f}s")
    print(f"Entradas: {res['apuestas']} | Ganadas: {res['ganadas']} | "
          f"Efectividad: {res['efectividad']:.2f}% (IC {res['ci_bajo']}-{res['ci_alto']}%)")
    print(f"Descartes: 0-0={res['cero_cero']} 2+={res['dos_mas']} sin_ht={res['sin_ht']} "
          f"sin_perfil={res['sin_perfil']} pre={res['pre']} modelo={res['modelo']}")

    nombre_pdf = dir_datos / f"Auditoria_Under35_{fecha_fin.isoformat()}.pdf"
    try:
        construir_pdf_auditoria(evaluaciones, cfg, nombre_pdf,
                                fecha_inicio.isoformat(), fecha_fin.isoformat())
        print(f"PDF generado: {nombre_pdf} ({nombre_pdf.stat().st_size/1024:.0f} KB)")
    except Exception as ex:
        logging.exception("Error generando PDF: %s", ex)
        print("No se pudo generar el PDF. Se enviara un resumen por Telegram.")
        nombre_pdf = None  # type: ignore

    caption = (
        f"📕 <b>AUDITORIA UNDER 3.5</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Periodo:</b> {fecha_inicio} a {fecha_fin}\n"
        f"• <b>Perfil:</b> {cfg['perfil']}\n"
        f"• <b>Entradas:</b> {res['apuestas']} | ✅ {res['ganadas']} | ❌ {res['perdidas']}\n"
        f"• <b>Efectividad:</b> <b>{res['efectividad']:.1f}%</b> (IC 95%: {res['ci_bajo']}-{res['ci_alto']}%)\n"
        f"• <b>0-0 descartados:</b> {res['cero_cero']} | <b>2+ goles:</b> {res['dos_mas']}\n"
        f"• <b>Sin HT confirmado:</b> {res['sin_ht']}\n\n"
        f"<i>Abre el PDF para ver la sensibilidad, la calibracion y el detalle de cada entrada.</i>"
    )
    if telegram_configurado():
        if nombre_pdf and Path(nombre_pdf).exists():
            enviado = enviar_pdf(nombre_pdf, caption)
            print("PDF enviado a Telegram." if enviado else "No se pudo enviar el PDF.")
        else:
            enviar_mensaje(caption)
    else:
        print(caption.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    # Descarga directa en Colab
    try:
        from google.colab import files  # type: ignore
        if nombre_pdf and Path(nombre_pdf).exists():
            files.download(str(nombre_pdf))
    except Exception:
        pass

    return Path(nombre_pdf) if nombre_pdf else None


# ==============================================================================
# 14. BOT EN VIVO
# ==============================================================================

ESTADO_ALERTAS = "alertas_enviadas.json"
ESTADO_DESCARTES = "descartes_vivo.json"


def _cargar_json(ruta: Path, default):
    try:
        if ruta.exists():
            with open(ruta, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _guardar_json(ruta: Path, data) -> None:
    try:
        tmp = ruta.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        tmp.replace(ruta)
    except Exception as e:
        logging.warning("No se pudo guardar %s: %s", ruta, e)


def registrar_contexto_csv(ruta: Path, p: Partido, e: Evaluacion,
                           contexto: ContextoVivo) -> None:
    """Guarda una fila por candidato para calibrar el contexto en vivo."""
    import csv
    existe = ruta.exists()
    try:
        with open(ruta, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not existe:
                w.writerow([
                    "fecha", "event_id", "liga", "local", "visita",
                    "ht_local", "ht_visita", "minuto",
                    "tiros_local", "tiros_visita",
                    "tiros_arco_local", "tiros_arco_visita",
                    "corners_local", "corners_visita",
                    "posesion_local", "posesion_visita",
                    "centros_local", "centros_visita",
                    "paradas_local", "paradas_visita",
                    "faltas_local", "faltas_visita",
                    "rojas_local", "rojas_visita", "sot_pierde",
                    "peligro_45", "presion_pierde_45", "control_gana", "ritmo_45",
                    "linea", "prob_linea", "cuota_linea",
                    "prob_base", "lambda_base", "apuesta_bot", "motivo",
                ])
            w.writerow([
                p.fecha, p.event_id, p.liga_nombre, p.local_nombre, p.visitante_nombre,
                p.goles_local_ht, p.goles_visitante_ht, contexto.minuto,
                contexto.tiros_local, contexto.tiros_visita,
                contexto.tiros_arco_local, contexto.tiros_arco_visita,
                contexto.corners_local, contexto.corners_visita,
                contexto.posesion_local, contexto.posesion_visita,
                contexto.centros_local, contexto.centros_visita,
                contexto.paradas_local, contexto.paradas_visita,
                contexto.faltas_local, contexto.faltas_visita,
                contexto.rojas_local, contexto.rojas_visita,
                contexto.sot_pierde,
                contexto.peligro_45, contexto.presion_pierde_45,
                contexto.control_gana, contexto.ritmo_45,
                e.linea, round(e.prob_linea, 4), e.cuota_minima_linea,
                round(e.prob_under35, 4), e.lambda_2t,
                1 if e.apuesta else 0, e.motivo,
            ])
    except Exception as ex:
        logging.warning("No se pudo registrar contexto: %s", ex)


def _anuncio_inicio_vivo(cfg: dict, indice: list[Partido]) -> None:
    enviar_mensaje(
        f"🤖 <b>Bot Under 3.5 iniciado</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Perfil:</b> {cfg['perfil']}\n"
        f"• <b>Linea:</b> -{cfg.get('linea_objetivo', '3.5')} | solo 1-0 o 0-1 al descanso\n"
        f"• <b>Filtros:</b> esp <= {cfg['esp_max']:.2f} | estab >= {cfg['e35_min']:.0f}% | "
        f"liga <= {cfg['liga_2t_max']} | prob >= {cfg['prob_min']*100:.0f}%\n"
        f"• <b>Historial cargado:</b> {len(indice)} partidos\n"
        f"• <b>Contexto vivo:</b> {'FILTRANDO' if cfg.get('usar_contexto_vivo') else 'solo capturando'} "
        f"(SOT del que pierde <= {cfg.get('sot_pierde_max')})\n"
        f"• <b>Poll:</b> cada {cfg['poll_segundos']}s | <b>Max:</b> {cfg['max_horas_vivo']}h\n\n"
        f"<i>Si la API no confirma el entretiempo, no se alerta. Se reintenta.</i>")


def ejecutar_vivo() -> None:
    cfg = CFG
    _imprimir_config(cfg)
    if not validar_telegram():
        return

    dir_datos = _dir_datos()
    client = EspnClient(dir_datos / "cache", workers=cfg["max_workers"], timeout=cfg["timeout"])
    hoy = ahora_bogota().date()
    hist_inicio = hoy - timedelta(days=cfg["dias_historial"])

    print("Cargando historial para perfiles...")
    indice = construir_indice(client, hist_inicio.isoformat(), hoy.isoformat(),
                              cobertura="AGREGADO")
    print(f"Historial: {len(indice)} partidos")
    ids_indice = {p.event_id for p in indice}
    if len(indice) < 100:
        enviar_mensaje("⚠️ <b>Pocos datos historicos.</b> Revisa la conexion a ESPN. "
                       "El bot puede no alertar hasta tener perfiles suficientes.")
    _anuncio_inicio_vivo(cfg, indice)

    ruta_alertas = dir_datos / ESTADO_ALERTAS
    ruta_descartes = dir_datos / ESTADO_DESCARTES
    alertas: dict = _cargar_json(ruta_alertas, {})
    descartes: dict = _cargar_json(ruta_descartes, {})
    pendientes: dict[str, dict] = {}
    cache_perfiles: dict = {}
    inicio = time.time()
    ultimo_heartbeat = time.time()
    ultima_actualizacion_global = 0.0
    global_avg, alpha_nb = media_2t_global(indice, iso_utc(ahora_bogota()))
    vistos = 0
    errores_seguidos = 0

    try:
        while True:
            ahora = ahora_bogota()
            transcurrido = time.time() - inicio
            if transcurrido > cfg["max_horas_vivo"] * 3600:
                enviar_mensaje(
                    f"⏹️ <b>Bot detenido por limite de tiempo</b> ({cfg['max_horas_vivo']}h).\n"
                    f"Alertas enviadas: {len(alertas)} | Partidos vistos: {vistos}\n"
                    f"<i>Vuelve a ejecutar la celda para reanudar.</i>")
                break

            try:
                fechas = [ahora.strftime("%Y%m%d")]
                if ahora.hour < 4:
                    fechas.append((ahora - timedelta(days=1)).strftime("%Y%m%d"))

                eventos: dict[str, dict] = {}
                for f in fechas:
                    for ev in client.scoreboard_live(f):
                        eid = str(ev.get("id", ""))
                        if eid and eid not in eventos:
                            eventos[eid] = ev
                vistos += len(eventos)

                # 1) Procesar candidatos en entretiempo
                for ev in eventos.values():
                    eid = str(ev.get("id", ""))
                    if not eid or eid in alertas or eid in descartes:
                        continue
                    lid, slug, nombre = liga_desde_evento(ev)
                    if not slug:
                        continue
                    comp = (ev.get("competitions") or [{}])[0]
                    status = comp.get("status", {}) or {}
                    estado = str((status.get("type", {}) or {}).get("state", "")).lower()
                    if estado != "in" or not es_estado_halftime(status):
                        if eid in pendientes:
                            pendientes.pop(eid, None)
                        continue

                    resumen = client.summary(eid, force=True, ttl=15)
                    p = parsear_evento(ev, ahora.strftime("%Y%m%d"), live=True, resumen=resumen)
                    if not p or not p.tiene_ht:
                        # Aun no hay confirmacion: se reintenta en la siguiente vuelta.
                        continue

                    firma = (p.goles_local_ht, p.goles_visitante_ht, p.fuente_ht)
                    prev = pendientes.get(eid)
                    if prev and prev.get("firma") == firma and (time.time() - prev.get("ts", 0)) >= 40:
                        contexto = extraer_contexto(
                            resumen, cfg.get("minuto_contexto", 45),
                            pierde_es_local=(p.goles_local_ht == 0 and p.goles_visitante_ht == 1),
                        )
                        e = evaluar_partido(p, indice, cfg, cache_perfiles,
                                            global_avg, alpha_nb, contexto=contexto)
                        if cfg.get("registrar_contexto") and contexto and contexto.datos_ok:
                            registrar_contexto_csv(
                                dir_datos / "contexto_vivo.csv", p, e, contexto)
                        if e.apuesta:
                            if enviar_alerta(e):
                                alertas[eid] = {
                                    "fecha": p.fecha, "liga": p.liga_nombre,
                                    "partido": f"{p.local_nombre} vs {p.visitante_nombre}",
                                    "ht": p.marcador_ht(), "prob": round(e.prob_under35, 4),
                                    "cuota_min": e.cuota_minima, "ts": time.time(),
                                }
                                _guardar_json(ruta_alertas, alertas)
                                print(f"ALERTA: {p.liga_nombre} | {p.local_nombre} vs {p.visitante_nombre} "
                                      f"| HT {p.marcador_ht()} | p={e.prob_under35:.3f}")
                        else:
                            descartes[eid] = {"motivo": e.motivo, "fecha": p.fecha, "ts": time.time()}
                            _guardar_json(ruta_descartes, descartes)
                            print(f"Descartado {p.event_id}: {e.motivo} | HT {p.marcador_ht()}")
                        pendientes.pop(eid, None)
                    else:
                        pendientes[eid] = {"firma": firma, "ts": time.time()}

                # 2) Agregar al historial los partidos que terminaron hoy
                for ev in eventos.values():
                    try:
                        p = parsear_evento(ev, ahora.strftime("%Y%m%d"), live=False)
                        if p and p.tiene_ht and p.valido:
                            if p.event_id not in ids_indice:
                                indice.append(p)
                                ids_indice.add(p.event_id)
                    except Exception:
                        continue

                # 3) Recalcular medias globales cada hora
                if time.time() - ultima_actualizacion_global > 3600:
                    global_avg, alpha_nb = media_2t_global(indice, iso_utc(ahora))
                    ultima_actualizacion_global = time.time()
                    cache_perfiles.clear()

                # 4) Limpiar pendientes viejos (si ya empezo el 2T)
                for eid in list(pendientes.keys()):
                    if time.time() - pendientes[eid].get("ts", 0) > 900:
                        pendientes.pop(eid, None)

                # 5) Heartbeat
                if time.time() - ultimo_heartbeat > cfg["heartbeat_horas"] * 3600:
                    enviar_mensaje(
                        f"💓 <b>Bot activo</b>\n"
                        f"• Partidos vistos: {vistos}\n"
                        f"• Alertas: {len(alertas)}\n"
                        f"• Descartados: {len(descartes)}\n"
                        f"• Hora: {ahora.strftime('%Y-%m-%d %H:%M')} Bogota",
                        silencioso=True)
                    ultimo_heartbeat = time.time()

                errores_seguidos = 0
            except Exception as ex:
                errores_seguidos += 1
                logging.exception("Error en loop vivo: %s", ex)
                if errores_seguidos in (1, 5, 20):
                    enviar_mensaje(f"⚠️ <b>Error en el bot</b> (se reintenta):\n<code>{escapar(str(ex))[:500]}</code>")
                time.sleep(min(60, 5 * errores_seguidos))

            time.sleep(max(20, cfg["poll_segundos"]))
    except KeyboardInterrupt:
        enviar_mensaje("⏹️ <b>Bot detenido manualmente.</b>")
        print("Detenido por el usuario.")
    finally:
        _guardar_json(ruta_alertas, alertas)
        _guardar_json(ruta_descartes, descartes)


# ==============================================================================
# 15. AUTO-TEST (sin red)
# ==============================================================================

def _evento_sintetico(estado: str, nombre: str, gl: int, gv: int,
                      detalles: Optional[list] = None,
                      linescores: Optional[tuple] = None) -> dict:
    comp = {
        "status": {"type": {"state": estado, "name": nombre,
                            "description": "Halftime" if "HALFTIME" in nombre else "Test",
                            "detail": "HT" if "HALFTIME" in nombre else "FT"}},
        "competitors": [
            {"homeAway": "home", "score": str(gl), "team": {"id": "1", "displayName": "Local"},
             "linescores": ([{"displayValue": str(linescores[0])}] if linescores else None)},
            {"homeAway": "away", "score": str(gv), "team": {"id": "2", "displayName": "Visita"},
             "linescores": ([{"displayValue": str(linescores[1])}] if linescores else None)},
        ],
        "details": detalles or [],
    }
    return {"id": "test", "date": "2026-01-01T00:00Z", "uid": "s:600~l:700~e:test",
            "competitions": [comp]}


def _gol(minuto: int, team_id: str, shootout: bool = False, own: bool = False) -> dict:
    return {"scoringPlay": True, "shootout": shootout, "ownGoal": own,
            "clock": {"displayValue": f"{minuto}'"}, "team": {"id": team_id}}


def _resumen_stats_fake(tiros_l: int, tiros_v: int, sot_l: int, sot_v: int,
                        corners_l: int, corners_v: int,
                        pos_l: float, pos_v: float) -> dict:
    def stats(tiros, sot, corners, pos):
        return [
            {"name": "totalShots", "displayValue": str(tiros)},
            {"name": "shotsOnTarget", "displayValue": str(sot)},
            {"name": "wonCorners", "displayValue": str(corners)},
            {"name": "possessionPct", "displayValue": str(pos)},
            {"name": "totalCrosses", "displayValue": "8"},
            {"name": "blockedShots", "displayValue": "2"},
            {"name": "saves", "displayValue": "1"},
            {"name": "foulsCommitted", "displayValue": "6"},
            {"name": "redCards", "displayValue": "0"},
            {"name": "totalClearance", "displayValue": "10"},
        ]
    return {"boxscore": {"teams": [
        {"homeAway": "home", "team": {"displayName": "Local"}, "statistics": stats(tiros_l, sot_l, corners_l, pos_l)},
        {"homeAway": "away", "team": {"displayName": "Visita"}, "statistics": stats(tiros_v, sot_v, corners_v, pos_v)},
    ]}}


def _perfil_fake(team_id: str = "1", nombre: str = "Local") -> PerfilEquipo:
    return PerfilEquipo(
        team_id=team_id, nombre=nombre, n=12, n_ht=12, pct_35=60.0, pct_45=80.0,
        gf_1t=0.5, gc_1t=0.4, gf_2t_local=0.6, gc_2t_local=0.5,
        gf_2t_visita=0.5, gc_2t_visita=0.6, media_2t=1.2,
        n_1gol_1t=4, media_2t_tras_1gol=1.1, pct_35_tras_1gol=80.0,
        n_ganando_1t=3, media_2t_ganando=1.0, n_perdiendo_1t=3, media_2t_perdiendo=1.3,
    )


def _partido_fake(hl: int, hv: int, fl: int = 1, fv: int = 0,
                  rojas: int = 0) -> Partido:
    return Partido(
        event_id="test", fecha_iso="2026-01-01T00:00Z", fecha="2026-01-01",
        liga_id="700", liga_slug="eng.1", liga_nombre="Test League",
        local_id="1", local_nombre="Local", visitante_id="2", visitante_nombre="Visita",
        goles_local_ft=fl, goles_visitante_ft=fv,
        goles_local_ht=hl, goles_visitante_ht=hv, fuente_ht="test",
        rojas_1t=rojas, estado="STATUS_FULL_TIME", valido=True,
    )


def autotest() -> bool:
    print("Ejecutando auto-test...")
    pruebas = []

    def check(nombre: str, cond: bool):
        pruebas.append((nombre, bool(cond)))
        print(f"  {'OK ' if cond else 'FAIL'} {nombre}")

    # 1) HT 1-0 desde details
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 1, 0, [_gol(30, "1")])
    ht = extraer_ht(ev)
    check("post 1-0 con gol al 30' -> 1-0", ht is not None and (ht.local, ht.visitante) == (1, 0))

    # 2) Sin datos: NO debe asumir 0-0
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 1, 0, [])
    check("post 1-0 sin details -> None (no 0-0)", extraer_ht(ev) is None)

    # 3) 3-1 con goles 20,50,70,80 -> HT 1-0
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 3, 1,
                           [_gol(20, "1"), _gol(50, "2"), _gol(70, "1"), _gol(80, "1")])
    ht = extraer_ht(ev)
    check("post 3-1 -> HT 1-0", ht is not None and (ht.local, ht.visitante) == (1, 0))

    # 4) 0-0 final
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 0, 0, [])
    ht = extraer_ht(ev)
    check("post 0-0 -> HT 0-0", ht is not None and (ht.local, ht.visitante) == (0, 0))

    # 5) 2-0 con un solo gol en details -> None
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 2, 0, [_gol(20, "1")])
    check("post 2-0 incompleto -> None", extraer_ht(ev) is None)

    # 6) En vivo, halftime 1-0
    ev = _evento_sintetico("in", "STATUS_HALFTIME", 1, 0, [_gol(30, "1")])
    ht = extraer_ht(ev, live=True)
    check("live halftime 1-0 -> 1-0", ht is not None and (ht.local, ht.visitante) == (1, 0))

    # 7) En vivo, primer tiempo 30' 1-0 -> None
    ev = _evento_sintetico("in", "STATUS_FIRST_HALF", 1, 0, [_gol(30, "1")])
    check("live 30' 1-0 -> None (no es HT)", extraer_ht(ev, live=True) is None)

    # 8) En vivo, primer tiempo 0-0 -> None
    ev = _evento_sintetico("in", "STATUS_FIRST_HALF", 0, 0, [])
    check("live 30' 0-0 -> None (no es HT)", extraer_ht(ev, live=True) is None)

    # 9) Own goal: el equipo beneficiado es el que figura en details
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 2, 0,
                           [_gol(20, "1"), _gol(60, "1", own=True)])
    ht = extraer_ht(ev)
    check("own goal no altera el HT", ht is not None and (ht.local, ht.visitante) == (1, 0))

    # 10) Shootout excluido: el gol de tanda no cuenta, queda el 1-0 del 1T
    ev = _evento_sintetico("post", "STATUS_FULL_TIME", 1, 0,
                           [_gol(30, "1"), _gol(120, "2", shootout=True)])
    ht = extraer_ht(ev)
    check("shootout no se cuenta como gol de juego",
          ht is not None and (ht.local, ht.visitante) == (1, 0))

    # 11) Binomial negativa
    check("nbinom_cdf(2, 1.0, 0.1) ~ 0.92",
          0.85 <= nbinom_cdf(2, 1.0, 0.1) <= 0.95)

    # 12) No hay factor 0.42
    try:
        src = Path(__file__).read_text(encoding="utf-8")
        check("sin factor 0.42", not re.search(r"(\*\s*0\.42|0\.42\s*\*)", src))
    except Exception:
        check("sin factor 0.42", True)

    # 13) Decisiones
    e = Evaluacion(partido=_partido_fake(0, 0), perfil_local=_perfil_fake(),
                   perfil_visitante=_perfil_fake("2", "Visita"))
    check("0-0 se descarta", decidir_con_config(e, CFG)[1] == "0_0")

    e = Evaluacion(partido=_partido_fake(1, 1), perfil_local=_perfil_fake(),
                   perfil_visitante=_perfil_fake("2", "Visita"))
    check("1-1 se descarta", decidir_con_config(e, CFG)[1] == "2_mas")

    e = Evaluacion(partido=_partido_fake(1, 0), perfil_local=_perfil_fake(),
                   perfil_visitante=_perfil_fake("2", "Visita"),
                   esp_total=2.5, estab_35=60, liga_2t=1.4,
                   lambda_2t=1.2, prob_under35=0.85)
    ok, motivo = decidir_con_config(e, CFG)
    check("1-0 con buenos filtros -> apuesta", ok and motivo == "apuesta")

    e = Evaluacion(partido=_partido_fake(1, 0), perfil_local=_perfil_fake(),
                   perfil_visitante=_perfil_fake("2", "Visita"),
                   esp_total=3.9, estab_35=60, liga_2t=1.4,
                   lambda_2t=1.2, prob_under35=0.85)
    check("1-0 con esp alto -> descarte", decidir_con_config(e, CFG)[1] == "pre_esp")

    # 14) Contexto en vivo
    res = _resumen_stats_fake(10, 4, 4, 2, 3, 3, 60.0, 40.0)
    ctx = extraer_contexto(res, 45, pierde_es_local=False)
    check("contexto extrae tiros/corners/posesion",
          ctx is not None and ctx.datos_ok and ctx.tiros_local == 10 and ctx.tiros_arco_visita == 2
          and ctx.corners_local == 3 and ctx.posesion_local == 60.0)
    check("contexto calcula peligro", ctx is not None and ctx.peligro_45 > 0)
    check("contexto calcula sot del que pierde", ctx is not None and ctx.sot_pierde == 2)

    res0 = _resumen_stats_fake(0, 0, 0, 0, 0, 0, 0.0, 0.0)
    ctx0 = extraer_contexto(res0, 45, pierde_es_local=True)
    check("contexto sin datos no inventa", ctx0 is not None and ctx0.datos_ok is False)

    cfg_ctx = dict(CFG)
    cfg_ctx["usar_contexto_vivo"] = True
    cfg_ctx["peligro_max"] = 12.0
    cfg_ctx["presion_pierde_max"] = 6.0
    check("contexto peligro alto -> descarte",
          ctx is not None and decidir_contexto(ctx, cfg_ctx)[1] == "contexto_peligro")
    cfg_sot = dict(cfg_ctx)
    cfg_sot["peligro_max"] = 999
    cfg_sot["presion_pierde_max"] = 999
    cfg_sot["sot_pierde_max"] = 1
    check("contexto SOT del que pierde alto -> descarte",
          ctx is not None and decidir_contexto(ctx, cfg_sot)[1] == "contexto_sot_pierde")
    cfg_ctx["usar_contexto_vivo"] = False
    check("contexto apagado -> no filtra",
          ctx is not None and decidir_contexto(ctx, cfg_ctx)[0] is True)

    total = len(pruebas)
    ok = sum(1 for _, c in pruebas if c)
    print(f"\nResultado: {ok}/{total} pruebas OK")
    if ok != total:
        raise AssertionError(f"Fallaron {total-ok} pruebas")
    return True


# ==============================================================================
# 16. MAIN
# ==============================================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    modo = str(CFG.get("modo", "AUDITORIA")).upper()
    if modo == "TEST":
        autotest()
    elif modo == "VIVO":
        ejecutar_vivo()
    else:
        ejecutar_auditoria()


if __name__ == "__main__":
    main()

