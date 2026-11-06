from .infrastructure import conexion
from .security import admin

def registrar(db,usuario_id,descripcion,fuente='maestro.xlsx',tipo='error',codigo=None,fila=None,campo=None):
    db.execute('INSERT INTO fallos_documentales(usuario_id,codigo_beneficiario,fila_origen,campo,fuente,tipo,descripcion) VALUES(?,?,?,?,?,?,?)',(usuario_id,codigo,fila,campo,fuente,tipo,descripcion))

def listar(ruta,user):
    admin(user)
    with conexion(ruta) as db:return [dict(r) for r in db.execute('SELECT * FROM fallos_documentales ORDER BY id DESC LIMIT 1000')]

def resolver(ruta,user,ident,nota):
    admin(user)
    if not isinstance(nota,str) or not nota.strip() or len(nota)>4000:raise ValueError('Documenta la corrección o decisión tomada (máximo 4000 caracteres)')
    with conexion(ruta) as db:
        if not db.execute('SELECT 1 FROM fallos_documentales WHERE id=?',(ident,)).fetchone():raise ValueError('Fallo no encontrado')
        db.execute("UPDATE fallos_documentales SET estado='resuelto',nota_resolucion=?,fecha_resolucion=CURRENT_TIMESTAMP WHERE id=?",(nota.strip(),ident))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'resolver_fallo',str(ident)))
