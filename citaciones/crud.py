"""Funciones SQL puras sobre la tabla citaciones. Sin LangChain, sin Teams.

Cada función toma su propia conexión del pool y la devuelve en el finally.
Los valores de usuario siempre van como parámetros (%s), nunca concatenados
en el texto SQL.
"""

from . import db
from .models import Citacion, desde_fila

_COLUMNAS = (
    "id, persona_citada, tipo_citacion, fecha_citacion, autoridad, "
    "estado, registrado_por, creado_en, actualizado_en"
)


def crear_citacion(citacion: Citacion) -> Citacion:
    """Inserta una citación nueva y devuelve la fila con id/timestamps ya asignados."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO citaciones
                    (persona_citada, tipo_citacion, fecha_citacion, autoridad,
                     estado, registrado_por)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING {_COLUMNAS}
                """,
                (citacion.persona_citada, citacion.tipo_citacion,
                 citacion.fecha_citacion, citacion.autoridad,
                 citacion.estado, citacion.registrado_por),
            )
            fila = cur.fetchone()
        conn.commit()
        return desde_fila(fila)
    finally:
        db.put_conn(conn)


def buscar_citaciones(estado=None, tipo_citacion=None, desde=None, hasta=None):
    """Busca citaciones con filtros opcionales; None omite ese filtro."""
    condiciones, valores = [], []
    if estado is not None:
        condiciones.append("estado = %s")
        valores.append(estado)
    if tipo_citacion is not None:
        condiciones.append("tipo_citacion = %s")
        valores.append(tipo_citacion)
    if desde is not None:
        condiciones.append("fecha_citacion >= %s")
        valores.append(desde)
    if hasta is not None:
        condiciones.append("fecha_citacion <= %s")
        valores.append(hasta)

    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {_COLUMNAS} FROM citaciones {where} "
                "ORDER BY fecha_citacion DESC",
                valores,
            )
            filas = cur.fetchall()
        return [desde_fila(fila) for fila in filas]
    finally:
        db.put_conn(conn)


def obtener_citacion(id_citacion: int):
    """Trae una citación por id, o None si no existe."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_COLUMNAS} FROM citaciones WHERE id = %s", (id_citacion,))
            fila = cur.fetchone()
        return desde_fila(fila) if fila else None
    finally:
        db.put_conn(conn)


def actualizar_estado(id_citacion: int, nuevo_estado: str):
    """Actualiza el estado de una citación; devuelve la fila actualizada o None si no existe."""
    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE citaciones
                SET estado = %s, actualizado_en = now()
                WHERE id = %s
                RETURNING {_COLUMNAS}
                """,
                (nuevo_estado, id_citacion),
            )
            fila = cur.fetchone()
        conn.commit()
        return desde_fila(fila) if fila else None
    finally:
        db.put_conn(conn)
