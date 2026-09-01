"""Pool de conexión a PostgreSQL para el módulo de citaciones.

Lee DATABASE_URL de las variables de entorno. No se conecta al importar el
módulo: el pool se crea perezosamente en el primer get_conn().
"""

import os

from psycopg2.pool import SimpleConnectionPool

_pool = None


def _crear_pool():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Falta la variable de entorno DATABASE_URL. Agrégala al .env, por "
            "ejemplo: DATABASE_URL=postgresql://usuario:clave@host:5432/citaciones"
        )
    return SimpleConnectionPool(1, 5, url)


def get_pool():
    """Crea el pool en el primer uso y lo reutiliza después."""
    global _pool
    if _pool is None:
        _pool = _crear_pool()
    return _pool


def get_conn():
    """Toma una conexión prestada del pool."""
    return get_pool().getconn()


def put_conn(conn):
    """Devuelve la conexión al pool."""
    get_pool().putconn(conn)
