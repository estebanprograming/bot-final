# -*- coding: utf-8 -*-
"""
BASE DE DATOS Y GESTOR DE ESTADÍSTICAS PERSISTENTES — BOT 14/15 EN VIVO
- Registra cada partido analizado día a día (conteo diario y acumulado).
- Registra cada apuesta ejecutada (HT, SOT, línea, FT, estado, ganada/perdida).
- Actualiza resultados automáticamente al terminar el encuentro.
- Genera el reporte oficial en PDF ante el comando 'SOLICITAR ESTADISTICA'.
"""
import os
import sqlite3
import html
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from contextlib import contextmanager

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

DB_PATH = Path(__file__).parent / "registro_operaciones.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=25)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        # 1. Tabla de partidos analizados (conteo de auditoria y radar)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS partidos_analizados (
                event_id TEXT PRIMARY KEY,
                fecha TEXT,
                hora TEXT,
                liga TEXT,
                local TEXT,
                visitante TEXT,
                ht TEXT,
                estado TEXT,
                diagnostico TEXT,
                creado_at TEXT
            )
        """)
        # Migraciones si la tabla ya existia previamente
        for col in ["hora", "diagnostico"]:
            try:
                cursor.execute(f"ALTER TABLE partidos_analizados ADD COLUMN {col} TEXT DEFAULT ''")
            except Exception:
                pass
        # 2. Tabla de apuestas ejecutadas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS apuestas (
                event_id TEXT PRIMARY KEY,
                fecha TEXT,
                hora TEXT,
                liga TEXT,
                local TEXT,
                visitante TEXT,
                ht TEXT,
                sot_pierde INTEGER,
                sot_tot INTEGER,
                linea REAL,
                ft TEXT,
                estado TEXT,
                win INTEGER,
                motivo TEXT
            )
        """)
        # 3. Resumen diario acumulado
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS resumen_diario (
                fecha TEXT PRIMARY KEY,
                total_analizados INTEGER DEFAULT 0,
                total_candidatos_ht INTEGER DEFAULT 0,
                total_entradas INTEGER DEFAULT 0,
                ganadas INTEGER DEFAULT 0,
                perdidas INTEGER DEFAULT 0,
                pendientes INTEGER DEFAULT 0
            )
        """)
        conn.commit()


def registrar_partido_analizado(event_id: str, fecha: str, hora: str, liga: str, local: str, visitante: str, ht: str, estado: str, diagnostico: str = ""):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO partidos_analizados (event_id, fecha, hora, liga, local, visitante, ht, estado, diagnostico, creado_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET 
                hora = CASE WHEN excluded.hora != '' THEN excluded.hora ELSE partidos_analizados.hora END,
                ht = excluded.ht, 
                estado = excluded.estado,
                diagnostico = CASE WHEN excluded.diagnostico != '' THEN excluded.diagnostico ELSE partidos_analizados.diagnostico END
        """, (str(event_id), str(fecha), str(hora), str(liga), str(local), str(visitante), str(ht), str(estado), str(diagnostico), datetime.now().isoformat()))
        conn.commit()


def registrar_lote_partidos_analizados(lote: list):
    """Inserta o actualiza un lote completo de partidos en una única transacción ultraveloz."""
    if not lote:
        return
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO partidos_analizados (event_id, fecha, hora, liga, local, visitante, ht, estado, diagnostico, creado_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET 
                hora = CASE WHEN excluded.hora != '' THEN excluded.hora ELSE partidos_analizados.hora END,
                ht = CASE WHEN excluded.ht != '' THEN excluded.ht ELSE partidos_analizados.ht END,
                estado = excluded.estado,
                diagnostico = CASE WHEN excluded.diagnostico != '' THEN excluded.diagnostico ELSE partidos_analizados.diagnostico END
        """, lote)
        conn.commit()


def obtener_radar_hoy(fecha_hoy: str) -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT hora, liga, local, visitante, ht, estado, diagnostico
            FROM partidos_analizados
            WHERE fecha = ?
            ORDER BY hora ASC, liga ASC
        """, (str(fecha_hoy),))
        return [dict(row) for row in cursor.fetchall()]


def registrar_apuesta(event_id: str, fecha: str, liga: str, local: str, visitante: str,
                       ht: str, sot_pierde: int, sot_tot: int, linea: float, motivo: str):
    hora_actual = datetime.now().strftime("%H:%M:%S")
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO apuestas 
            (event_id, fecha, hora, liga, local, visitante, ht, sot_pierde, sot_tot, linea, ft, estado, win, motivo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EN JUEGO', 'EN_JUEGO', NULL, ?)
        """, (str(event_id), str(fecha), hora_actual, str(liga), str(local), str(visitante),
              str(ht), int(sot_pierde), int(sot_tot), float(linea), str(motivo)))
        
        # Actualizar resumen diario
        cursor.execute("""
            INSERT INTO resumen_diario (fecha, total_analizados, total_candidatos_ht, total_entradas, ganadas, perdidas, pendientes)
            VALUES (?, 1, 1, 1, 0, 0, 1)
            ON CONFLICT(fecha) DO UPDATE SET 
                total_candidatos_ht = total_candidatos_ht + 1,
                total_entradas = total_entradas + 1,
                pendientes = pendientes + 1
        """, (str(fecha),))
        conn.commit()


def actualizar_resultado_apuesta(event_id: str, ft: str, win: int):
    with get_connection() as conn:
        cursor = conn.cursor()
        # Obtener fecha de la apuesta
        cursor.execute("SELECT fecha, win FROM apuestas WHERE event_id = ?", (str(event_id),))
        row = cursor.fetchone()
        if not row:
            return
        fecha = row["fecha"]
        prev_win = row["win"]

        cursor.execute("""
            UPDATE apuestas 
            SET ft = ?, estado = 'FINALIZADO', win = ?
            WHERE event_id = ?
        """, (str(ft), int(win), str(event_id)))

        # Actualizar resumen diario si no estaba previamente finalizada
        if prev_win is None:
            if win == 1:
                cursor.execute("""
                    UPDATE resumen_diario 
                    SET ganadas = ganadas + 1, pendientes = max(0, pendientes - 1)
                    WHERE fecha = ?
                """, (str(fecha),))
            else:
                cursor.execute("""
                    UPDATE resumen_diario 
                    SET perdidas = perdidas + 1, pendientes = max(0, pendientes - 1)
                    WHERE fecha = ?
                """, (str(fecha),))
        conn.commit()


def actualizar_resultado_ft(event_id: str, ft: str, total_ft: int):
    """Calcula automáticamente win (1 o 0) comparando con la línea apostada y actualiza en DB."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT linea FROM apuestas WHERE event_id = ?", (str(event_id),))
        row = cursor.fetchone()
        if not row:
            return
        linea = float(row["linea"])
        win = 1 if total_ft < linea else 0
    actualizar_resultado_apuesta(event_id, ft, win)


def obtener_apuestas_pendientes(fecha_hoy: str = None) -> list:
    """Devuelve las apuestas en juego para recuperar memoria tras reinicios de Render."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if fecha_hoy:
            cursor.execute("""
                SELECT event_id, fecha, liga, local, visitante, ht, linea, estado
                FROM apuestas
                WHERE estado = 'EN_JUEGO' AND fecha = ?
            """, (str(fecha_hoy),))
        else:
            cursor.execute("""
                SELECT event_id, fecha, liga, local, visitante, ht, linea, estado
                FROM apuestas
                WHERE estado = 'EN_JUEGO'
            """)
        return [dict(r) for r in cursor.fetchall()]


def obtener_apuestas_hoy(fecha_hoy: str) -> list:
    """Devuelve únicamente las apuestas ejecutadas en la fecha actual."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fecha, hora, liga, local, visitante, ht, sot_pierde, sot_tot, linea, ft, estado, win, motivo
            FROM apuestas
            WHERE fecha = ?
            ORDER BY hora ASC
        """, (str(fecha_hoy),))
        return [dict(row) for row in cursor.fetchall()]


def obtener_partidos_descartados_hoy(fecha_hoy: str) -> list:
    """Devuelve la lista de partidos de hoy con su motivo exacto de descarte o diagnóstico."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT hora, liga, local, visitante, ht, estado, diagnostico
            FROM partidos_analizados
            WHERE fecha = ? AND (diagnostico LIKE 'Descartado%' OR diagnostico != '')
            ORDER BY hora ASC, liga ASC
        """, (str(fecha_hoy),))
        return [dict(row) for row in cursor.fetchall()]


def obtener_metricas_globales(fecha_hoy: str) -> dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Conteo exacto de partidos únicos analizados hoy
        cursor.execute("SELECT COUNT(DISTINCT event_id) as cnt FROM partidos_analizados WHERE fecha = ?", (fecha_hoy,))
        r_hoy = cursor.fetchone()
        analizados_hoy = r_hoy["cnt"] if r_hoy else 0

        # Conteo exacto de partidos únicos histórico acumulado
        cursor.execute("SELECT COUNT(DISTINCT event_id) as cnt FROM partidos_analizados")
        r_tot = cursor.fetchone()
        analizados_historico = r_tot["cnt"] if r_tot else analizados_hoy

        # Métricas de apuestas de HOY
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as w,
                SUM(CASE WHEN win=0 THEN 1 ELSE 0 END) as l,
                SUM(CASE WHEN estado='EN_JUEGO' THEN 1 ELSE 0 END) as p
            FROM apuestas
            WHERE fecha = ?
        """, (fecha_hoy,))
        r_ap_hoy = cursor.fetchone()
        apuestas_hoy = r_ap_hoy["total"] or 0
        ganadas_hoy = r_ap_hoy["w"] or 0
        perdidas_hoy = r_ap_hoy["l"] or 0
        pendientes_hoy = r_ap_hoy["p"] or 0
        resueltas_hoy = ganadas_hoy + perdidas_hoy
        efectividad_hoy = (100.0 * ganadas_hoy / resueltas_hoy) if resueltas_hoy > 0 else 0.0

        # Métricas de apuestas HISTÓRICO ACUMULADO
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as w,
                SUM(CASE WHEN win=0 THEN 1 ELSE 0 END) as l,
                SUM(CASE WHEN estado='EN_JUEGO' THEN 1 ELSE 0 END) as p
            FROM apuestas
        """)
        r_ap_hist = cursor.fetchone()
        total_historico = r_ap_hist["total"] or 0
        ganadas_historico = r_ap_hist["w"] or 0
        perdidas_historico = r_ap_hist["l"] or 0
        pendientes_historico = r_ap_hist["p"] or 0
        resueltas_hist = ganadas_historico + perdidas_historico
        efectividad_historico = (100.0 * ganadas_historico / resueltas_hist) if resueltas_hist > 0 else 0.0

        return {
            "analizados_hoy": analizados_hoy,
            "analizados_historico": analizados_historico,
            # Balance de Hoy
            "apuestas_hoy": apuestas_hoy,
            "ganadas_hoy": ganadas_hoy,
            "perdidas_hoy": perdidas_hoy,
            "pendientes_hoy": pendientes_hoy,
            "efectividad_hoy": efectividad_hoy,
            # Balance Histórico Acumulado
            "total_historico": total_historico,
            "ganadas_historico": ganadas_historico,
            "perdidas_historico": perdidas_historico,
            "pendientes_historico": pendientes_historico,
            "efectividad_historico": efectividad_historico,
            # Compatibilidad
            "total_apuestas": total_historico,
            "ganadas": ganadas_historico,
            "perdidas": perdidas_historico,
            "pendientes": pendientes_historico,
            "efectividad": efectividad_historico,
        }


def obtener_desglose_dias() -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                p.fecha, 
                COUNT(DISTINCT p.event_id) as total_analizados,
                COUNT(a.event_id) as total_entradas,
                SUM(CASE WHEN a.win=1 THEN 1 ELSE 0 END) as ganadas,
                SUM(CASE WHEN a.win=0 THEN 1 ELSE 0 END) as perdidas,
                SUM(CASE WHEN a.estado='EN_JUEGO' THEN 1 ELSE 0 END) as pendientes
            FROM partidos_analizados p
            LEFT JOIN apuestas a ON p.fecha = a.fecha
            GROUP BY p.fecha
            ORDER BY p.fecha DESC
        """)
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            cursor.execute("SELECT fecha, total_analizados, total_entradas, ganadas, perdidas, pendientes FROM resumen_diario ORDER BY fecha DESC")
            rows = [dict(row) for row in cursor.fetchall()]
        return rows


def obtener_todas_apuestas() -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT fecha, hora, liga, local, visitante, ht, sot_pierde, sot_tot, linea, ft, estado, win, motivo FROM apuestas ORDER BY fecha DESC, hora DESC")
        return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# GENERADOR DEL PDF AL COMANDO: SOLICITAR ESTADISTICA
# ---------------------------------------------------------------------------
class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super(NumberedCanvas, self).__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super(NumberedCanvas, self).showPage()
        super(NumberedCanvas, self).save()

    def draw_page_decorations(self, num_pages):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#718096"))
        if self._pageNumber > 1:
            self.drawString(36, 755, "REPORTE ESTADÍSTICO EN VIVO — BOT 14/15 (SOLICITUD TELEGRAM)")
            self.drawRightString(576, 755, "AUDITORÍA EN TIEMPO REAL")
            self.setStrokeColor(colors.HexColor("#CBD5E0"))
            self.setLineWidth(0.6)
            self.line(36, 748, 576, 748)
            
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.6)
        self.line(36, 36, 576, 36)
        self.setFont("Helvetica", 7.5)
        self.drawString(36, 24, "GENERADO AUTOMÁTICAMENTE ANTE COMANDO 'SOLICITAR ESTADISTICA' | MOTOR EN VIVO")
        self.drawRightString(576, 24, f"Página {self._pageNumber} de {num_pages}")
        self.restoreState()


def generar_pdf_estadisticas(ruta_pdf: str, fecha_hoy: str) -> str:
    metricas = obtener_metricas_globales(fecha_hoy)
    apuestas_hoy = obtener_apuestas_hoy(fecha_hoy)
    partidos_hoy = obtener_radar_hoy(fecha_hoy)

    doc = SimpleDocTemplate(
        ruta_pdf,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=colors.HexColor("#1A365D"), spaceAfter=2)
    sub_style = ParagraphStyle('DocSub', parent=styles['Normal'], fontName='Helvetica', fontSize=8, leading=11, textColor=colors.HexColor("#4A5568"), spaceAfter=6)
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=colors.HexColor("#2B6CB0"), spaceBefore=8, spaceAfter=4)
    body_bold = ParagraphStyle('BodyB', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, leading=10, textColor=colors.HexColor("#1A202C"))

    th_style = ParagraphStyle('TH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#1A365D"), alignment=1)
    tc_norm = ParagraphStyle('TCN', parent=styles['Normal'], fontName='Helvetica', fontSize=6.5, leading=8, textColor=colors.HexColor("#2D3748"))
    tc_center = ParagraphStyle('TCC', parent=styles['Normal'], fontName='Helvetica', fontSize=6.5, leading=8, textColor=colors.HexColor("#2D3748"), alignment=1)
    tc_bold = ParagraphStyle('TCB', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#1A365D"), alignment=1)
    tc_win = ParagraphStyle('TCW', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#22543D"), alignment=1)
    tc_loss = ParagraphStyle('TCL', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#742A2A"), alignment=1)
    tc_pend = ParagraphStyle('TCP', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=6.5, leading=8, textColor=colors.HexColor("#2B6CB0"), alignment=1)

    story = []

    # =========================================================================
    # ENCABEZADO Y CUADRO DE MANDOS DEL DÍA DE HOY
    # =========================================================================
    story.append(Paragraph("AUDITORÍA OFICIAL DEL DÍA — BOT 14/15 EN VIVO", title_style))
    story.append(Paragraph(f"Auditoría Oficial de la Jornada: <b>{fecha_hoy}</b> | Generado en vivo: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=6))

    # Cajas KPI centradas en el DÍA DE HOY
    ef_hoy_txt = f"{metricas['efectividad_hoy']:.1f}%" if (metricas['ganadas_hoy'] + metricas['perdidas_hoy']) > 0 else "100.0%"
    kpi_box = [
        [
            Paragraph(f"<b>PARTIDOS MONITOREADOS HOY:</b><br/><font size='11' color='#1A365D'><b>{metricas['analizados_hoy']}</b></font>", body_bold),
            Paragraph(f"<b>APUESTAS EJECUTADAS HOY:</b><br/><font size='11' color='#2B6CB0'><b>{metricas['apuestas_hoy']}</b></font>", body_bold),
            Paragraph(f"<b>EFECTIVIDAD DE HOY:</b><br/><font size='11' color='#22543D'><b>{ef_hoy_txt}</b></font>", body_bold),
        ],
        [
            Paragraph(f"<b>GANADAS HOY:</b><br/><font size='11' color='#22543D'><b>{metricas['ganadas_hoy']}</b></font>", body_bold),
            Paragraph(f"<b>PERDIDAS HOY:</b><br/><font size='11' color='#742A2A'><b>{metricas['perdidas_hoy']}</b></font>", body_bold),
            Paragraph(f"<b>EN JUEGO AHORA:</b><br/><font size='11' color='#2B6CB0'><b>{metricas['pendientes_hoy']}</b></font>", body_bold),
        ]
    ]
    t_kpi = Table(kpi_box, colWidths=[180, 180, 180])
    t_kpi.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor("#2B6CB0")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_kpi)
    story.append(Spacer(1, 8))

    # =========================================================================
    # SECCIÓN 1: APUESTAS EJECUTADAS HOY
    # =========================================================================
    story.append(Paragraph(f"1. Apuestas Ejecutadas Hoy ({fecha_hoy})", h2_style))
    
    t_ap_data = [[
        Paragraph("<b>Hora</b>", th_style),
        Paragraph("<b>Partido (Local vs Visitante)</b>", th_style),
        Paragraph("<b>Liga</b>", th_style),
        Paragraph("<b>HT</b>", th_style),
        Paragraph("<b>SOT</b>", th_style),
        Paragraph("<b>Línea</b>", th_style),
        Paragraph("<b>FT</b>", th_style),
        Paragraph("<b>Resultado</b>", th_style),
    ]]

    if not apuestas_hoy:
        t_ap_data.append([
            Paragraph("-", tc_center),
            Paragraph("<i>Sin apuestas ejecutadas aún hoy (Filtros protegiendo capital ante tiros excesivos o sin valor)</i>", tc_norm),
            Paragraph("-", tc_center), Paragraph("-", tc_center),
            Paragraph("-", tc_center), Paragraph("-", tc_center),
            Paragraph("-", tc_center), Paragraph("EN ESPERA", tc_center)
        ])
    else:
        for a in apuestas_hoy:
            st_res = tc_win if a["win"] == 1 else (tc_loss if a["win"] == 0 else tc_pend)
            res_txt = "GANADA" if a["win"] == 1 else ("PERDIDA" if a["win"] == 0 else "EN JUEGO")
            sot_info = f"pierde={a['sot_pierde']}" if a["linea"] == 3.5 else f"tot={a['sot_tot']}"
            loc_a = html.escape(str(a.get("local", ""))[:20])
            vis_a = html.escape(str(a.get("visitante", ""))[:20])
            liga_a = html.escape(str(a.get("liga", ""))[:18])
            t_ap_data.append([
                Paragraph(str(a.get('hora', ''))[:5], tc_center),
                Paragraph(f"<b>{loc_a}</b> vs {vis_a}", tc_norm),
                Paragraph(liga_a, tc_norm),
                Paragraph(str(a["ht"]), tc_center),
                Paragraph(sot_info, tc_center),
                Paragraph(f"-{a['linea']}", tc_center),
                Paragraph(str(a.get("ft", "-")), tc_center),
                Paragraph(res_txt, st_res),
            ])

    t_ap = Table(t_ap_data, colWidths=[40, 190, 110, 35, 55, 35, 35, 40])
    t_ap.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 2.5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(t_ap)
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECCIÓN 2: AUDITORÍA DETALLADA DE PARTIDOS DESCARTADOS HOY Y MOTIVOS EXACTOS
    # =========================================================================
    story.append(KeepTogether([
        Paragraph("2. Auditoría Detallada de Partidos Analizados y Descartados Hoy", h2_style),
        Paragraph(f"Registro en tiempo real de todos los partidos monitoreados hoy ({len(partidos_hoy)} eventos) con su estado y el motivo técnico exacto de descarte.", sub_style)
    ]))

    t_rad_data = [[
        Paragraph("<b>Hora</b>", th_style),
        Paragraph("<b>Liga</b>", th_style),
        Paragraph("<b>Partido (Local vs Visitante)</b>", th_style),
        Paragraph("<b>Marcador/HT</b>", th_style),
        Paragraph("<b>Dictamen Técnico Oficial (Motivo Exacto)</b>", th_style),
    ]]

    if not partidos_hoy:
        t_rad_data.append([
            Paragraph("-", tc_center), Paragraph("-", tc_center),
            Paragraph("<i>Iniciando radar de la jornada...</i>", tc_norm),
            Paragraph("-", tc_center), Paragraph("Sondeando Flashscore y ESPN", tc_norm)
        ])
    else:
        for p in partidos_hoy[:120]:
            loc_s = html.escape(str(p.get("local", ""))[:18])
            vis_s = html.escape(str(p.get("visitante", ""))[:18])
            liga_s = html.escape(str(p.get("liga", ""))[:20])
            ht_val = html.escape(str(p.get("ht", "")))
            if not ht_val or ht_val == "SIN_HT":
                ht_val = "1T"
            diag_s = html.escape(str(p.get("diagnostico", p.get("estado", "")))[:65])
            t_rad_data.append([
                Paragraph(html.escape(str(p.get("hora", ""))[:5]), tc_center),
                Paragraph(liga_s, tc_norm),
                Paragraph(f"<b>{loc_s}</b> vs {vis_s}", tc_norm),
                Paragraph(ht_val, tc_center),
                Paragraph(diag_s, tc_norm),
            ])

    t_rad = Table(t_rad_data, colWidths=[38, 110, 160, 48, 184])
    t_rad.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#2B6CB0")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 2.2),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(t_rad)
    story.append(Spacer(1, 10))

    # =========================================================================
    # SECCIÓN 3: CUADRO DE CERTIFICACIÓN HISTÓRICA ACUMULADA
    # =========================================================================
    story.append(Paragraph("3. Respaldo y Certificación Histórica Acumulada", h2_style))
    hist_txt = (
        f"<b>Partidos auditados histórico total:</b> {metricas['analizados_historico']} | "
        f"<b>Total apuestas ejecutadas:</b> {metricas['total_historico']} "
        f"({metricas['ganadas_historico']}W - {metricas['perdidas_historico']}L) | "
        f"<b>Efectividad histórica certificada:</b> {metricas['efectividad_historico']:.1f}%"
    )
    p_hist = Paragraph(hist_txt, body_bold)
    t_hist = Table([[p_hist]], colWidths=[540])
    t_hist.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EDF2F7")),
        ('BOX', (0,0), (-1,-1), 0.8, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 5),
    ]))
    story.append(t_hist)

    doc.build(story, canvasmaker=NumberedCanvas)
    return ruta_pdf

    doc.build(story, canvasmaker=NumberedCanvas)
    return ruta_pdf

# Inicializar DB al importar
init_db()
