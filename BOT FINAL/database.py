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
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

DB_PATH = Path(__file__).parent / "registro_operaciones.db"


def get_connection():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        # 1. Tabla de partidos analizados (conteo de auditoría)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS partidos_analizados (
                event_id TEXT PRIMARY KEY,
                fecha TEXT,
                liga TEXT,
                local TEXT,
                visitante TEXT,
                ht TEXT,
                estado TEXT,
                creado_at TEXT
            )
        """)
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


def registrar_partido_analizado(event_id: str, fecha: str, liga: str, local: str, visitante: str, ht: str, estado: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO partidos_analizados (event_id, fecha, liga, local, visitante, ht, estado, creado_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(event_id), str(fecha), str(liga), str(local), str(visitante), str(ht), str(estado), datetime.now().isoformat()))
        
        # Actualizar contador diario
        cursor.execute("""
            INSERT INTO resumen_diario (fecha, total_analizados, total_candidatos_ht, total_entradas, ganadas, perdidas, pendientes)
            VALUES (?, 1, 0, 0, 0, 0, 0)
            ON CONFLICT(fecha) DO UPDATE SET total_analizados = total_analizados + 1
        """, (str(fecha),))
        conn.commit()


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


def obtener_metricas_globales(fecha_hoy: str) -> dict:
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Total analizados hoy
        cursor.execute("SELECT total_analizados FROM resumen_diario WHERE fecha = ?", (fecha_hoy,))
        r_hoy = cursor.fetchone()
        analizados_hoy = r_hoy["total_analizados"] if r_hoy else 0

        # Total analizados histórico acumulado
        cursor.execute("SELECT SUM(total_analizados) as tot FROM resumen_diario")
        r_tot = cursor.fetchone()
        analizados_historico = r_tot["tot"] if (r_tot and r_tot["tot"]) else analizados_hoy

        # Métricas de apuestas
        cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) as w, SUM(CASE WHEN win=0 THEN 1 ELSE 0 END) as l, SUM(CASE WHEN estado='EN_JUEGO' THEN 1 ELSE 0 END) as p FROM apuestas")
        r_ap = cursor.fetchone()
        total_apuestas = r_ap["total"] or 0
        ganadas = r_ap["w"] or 0
        perdidas = r_ap["l"] or 0
        pendientes = r_ap["p"] or 0

        resueltas = ganadas + perdidas
        efectividad = (100.0 * ganadas / resueltas) if resueltas > 0 else 0.0

        return {
            "analizados_hoy": analizados_hoy,
            "analizados_historico": analizados_historico,
            "total_apuestas": total_apuestas,
            "ganadas": ganadas,
            "perdidas": perdidas,
            "pendientes": pendientes,
            "efectividad": efectividad
        }


def obtener_desglose_dias() -> list:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT fecha, total_analizados, total_entradas, ganadas, perdidas, pendientes FROM resumen_diario ORDER BY fecha DESC")
        return [dict(row) for row in cursor.fetchall()]


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
    desglose_dias = obtener_desglose_dias()
    apuestas = obtener_todas_apuestas()

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
    h2_style = ParagraphStyle('H2', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=colors.HexColor("#2B6CB0"), spaceBefore=5, spaceAfter=3)
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
    # PÁGINA 1: PORTADA, CUADRO DE MANDOS Y DESGLOSE POR DÍA
    # =========================================================================
    story.append(Paragraph("INFORME ESTADÍSTICO EN VIVO — BOT 14/15 DEFINITIVO", title_style))
    story.append(Paragraph(f"Auditoría Oficial Generada a Petición del Usuario | Fecha de Corte: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=5))

    # Cajas KPI
    kpi_box = [
        [
            Paragraph(f"<b>PARTIDOS ANALIZADOS HOY:</b><br/><font size='11' color='#1A365D'><b>{metricas['analizados_hoy']}</b></font>", body_bold),
            Paragraph(f"<b>PARTIDOS ANALIZADOS HISTÓRICO:</b><br/><font size='11' color='#1A365D'><b>{metricas['analizados_historico']}</b></font>", body_bold),
            Paragraph(f"<b>TOTAL ENTRADAS REALIZADAS:</b><br/><font size='11' color='#2B6CB0'><b>{metricas['total_apuestas']}</b></font>", body_bold),
        ],
        [
            Paragraph(f"<b>APUESTAS GANADAS:</b><br/><font size='11' color='#22543D'><b>{metricas['ganadas']}</b></font>", body_bold),
            Paragraph(f"<b>APUESTAS PERDIDAS:</b><br/><font size='11' color='#742A2A'><b>{metricas['perdidas']}</b></font>", body_bold),
            Paragraph(f"<b>EFECTIVIDAD REAL EN VIVO:</b><br/><font size='11' color='#22543D'><b>{metricas['efectividad']:.1f}%</b></font>", body_bold),
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

    story.append(Paragraph("1. Desglose Estadístico Día por Día", h2_style))
    
    t_dias_data = [[
        Paragraph("<b>Fecha</b>", th_style),
        Paragraph("<b>Analizados</b>", th_style),
        Paragraph("<b>Entradas HT</b>", th_style),
        Paragraph("<b>Ganadas</b>", th_style),
        Paragraph("<b>Perdidas</b>", th_style),
        Paragraph("<b>En Juego</b>", th_style),
        Paragraph("<b>Efectividad Día</b>", th_style),
    ]]

    if not desglose_dias:
        t_dias_data.append([
            Paragraph("Sin registros aún", tc_center),
            Paragraph("0", tc_center), Paragraph("0", tc_center),
            Paragraph("0", tc_center), Paragraph("0", tc_center),
            Paragraph("0", tc_center), Paragraph("100.0%", tc_center)
        ])
    else:
        for d in desglose_dias:
            res_d = d["ganadas"] + d["perdidas"]
            ef_d = f"{(100.0 * d['ganadas'] / res_d):.1f}%" if res_d > 0 else "N/A"
            t_dias_data.append([
                Paragraph(str(d["fecha"]), tc_norm),
                Paragraph(str(d["total_analizados"]), tc_center),
                Paragraph(f"<b>{d['total_entradas']}</b>", tc_bold),
                Paragraph(str(d["ganadas"]), tc_win),
                Paragraph(str(d["perdidas"]), tc_loss if d["perdidas"] > 0 else tc_center),
                Paragraph(str(d["pendientes"]), tc_pend if d["pendientes"] > 0 else tc_center),
                Paragraph(f"<b>{ef_d}</b>", tc_win if ef_d != "N/A" and float(ef_d.replace('%','')) >= 90 else tc_bold),
            ])

    t_dias = Table(t_dias_data, colWidths=[80, 80, 75, 75, 75, 75, 80])
    t_dias.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 3),
    ]))
    story.append(t_dias)
    story.append(Spacer(1, 10))

    # =========================================================================
    # DETALLE DE APUESTAS REALIZADAS
    # =========================================================================
    story.append(Paragraph("2. Historial Detallado de Apuestas Realizadas al Minuto 45", h2_style))
    
    t_ap_data = [[
        Paragraph("<b>Fecha/Hora</b>", th_style),
        Paragraph("<b>Partido</b>", th_style),
        Paragraph("<b>Liga</b>", th_style),
        Paragraph("<b>HT</b>", th_style),
        Paragraph("<b>SOT</b>", th_style),
        Paragraph("<b>Línea</b>", th_style),
        Paragraph("<b>FT</b>", th_style),
        Paragraph("<b>Resultado</b>", th_style),
    ]]

    if not apuestas:
        t_ap_data.append([
            Paragraph("En espera de partidos", tc_center),
            Paragraph("No se han ejecutado entradas aún en la sesión", tc_norm),
            Paragraph("-", tc_center), Paragraph("-", tc_center),
            Paragraph("-", tc_center), Paragraph("-", tc_center),
            Paragraph("-", tc_center), Paragraph("EN ESPERA", tc_center)
        ])
    else:
        for a in apuestas:
            st_res = tc_win if a["win"] == 1 else (tc_loss if a["win"] == 0 else tc_pend)
            res_txt = "GANADA" if a["win"] == 1 else ("PERDIDA" if a["win"] == 0 else "EN JUEGO")
            sot_info = f"pierde={a['sot_pierde']}" if a["linea"] == 3.5 else f"tot={a['sot_tot']}"
            t_ap_data.append([
                Paragraph(f"{str(a['fecha'])[-5:]} {a['hora'][:5]}", tc_norm),
                Paragraph(f"{a['local']} vs {a['visitante']}"[:30], tc_norm),
                Paragraph(str(a["liga"])[:8], tc_center),
                Paragraph(str(a["ht"]), tc_center),
                Paragraph(sot_info, tc_center),
                Paragraph(f"-{a['linea']}", tc_center),
                Paragraph(str(a["ft"]), tc_center),
                Paragraph(res_txt, st_res),
            ])

    t_ap = Table(t_ap_data, colWidths=[65, 175, 55, 35, 65, 40, 45, 60])
    t_ap.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
        ('GRID', (0,0), (-1,-1), 0.4, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 2.5),
    ]))
    story.append(t_ap)

    doc.build(story, canvasmaker=NumberedCanvas)
    return ruta_pdf

# Inicializar DB al importar
init_db()
