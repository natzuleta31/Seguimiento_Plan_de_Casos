"""Capa de persistencia SQLite; claves foráneas y transacciones."""
import sqlite3
from contextlib import contextmanager

@contextmanager
def conexion(ruta):
    db=sqlite3.connect(ruta)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def buscar_por_codigo(db,codigo):
    row=db.execute('SELECT b.*, g.nombre AS gestora FROM beneficiarios b JOIN gestoras g ON g.codigo_gestora=b.codigo_gestora WHERE b.codigo_beneficiario=?',(codigo,)).fetchone()
    return dict(row) if row else None
