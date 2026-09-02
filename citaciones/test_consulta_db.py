"""Consulta diagnóstica: imprime todas las tablas y sus datos en consola.

Uso:
    python -m citaciones.consulta_db
"""

import os
import sys

from dotenv import load_dotenv
import psycopg2


def main():
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL no está configurada en .env")
        sys.exit(1)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )
            tablas = [row[0] for row in cur.fetchall()]

            if not tablas:
                print("No hay tablas en el esquema public.")
                return

            for tabla in tablas:
                cur.execute(f'SELECT * FROM "{tabla}"')
                columnas = [desc[0] for desc in cur.description]
                filas = cur.fetchall()

                print(f"\n{'=' * 60}")
                print(f"  {tabla}  ({len(filas)} filas)")
                print(f"{'=' * 60}")
                print("  " + " | ".join(columnas))
                print(f"  {'-' * (len(' | '.join(columnas)))}")
                for fila in filas:
                    print("  " + " | ".join(str(v) for v in fila))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
