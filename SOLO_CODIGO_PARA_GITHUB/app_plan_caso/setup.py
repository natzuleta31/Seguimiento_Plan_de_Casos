"""Inicialización y migraciones sin datos personales incorporados al código."""
from pathlib import Path
from .infrastructure import conexion

def inicializar(ruta):
    Path(ruta).parent.mkdir(parents=True,exist_ok=True)
    with conexion(ruta) as db:
        db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf8'))
        db.executescript('''
        CREATE TABLE IF NOT EXISTS usuarios(id INTEGER PRIMARY KEY,usuario TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,rol TEXT NOT NULL CHECK(rol IN('admin','gestora')),codigo_gestora TEXT REFERENCES gestoras(codigo_gestora),activo INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS sesiones(token_hash TEXT PRIMARY KEY,usuario_id INTEGER NOT NULL REFERENCES usuarios(id),csrf TEXT NOT NULL,vence INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS intentos_login(clave TEXT PRIMARY KEY,intentos INTEGER NOT NULL,inicio INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS auditoria(id INTEGER PRIMARY KEY,usuario_id INTEGER REFERENCES usuarios(id),accion TEXT NOT NULL,referencia TEXT,fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS importaciones(id TEXT PRIMARY KEY,usuario_id INTEGER NOT NULL REFERENCES usuarios(id),contenido TEXT NOT NULL,vence INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS fallos_documentales(id INTEGER PRIMARY KEY,fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,usuario_id INTEGER REFERENCES usuarios(id),codigo_beneficiario TEXT,fila_origen INTEGER,campo TEXT,fuente TEXT NOT NULL,tipo TEXT NOT NULL,descripcion TEXT NOT NULL,estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN('pendiente','resuelto')),nota_resolucion TEXT,fecha_resolucion TEXT);
        CREATE TABLE IF NOT EXISTS documentos_pdf(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,nombre TEXT NOT NULL,usuario_id INTEGER NOT NULL REFERENCES usuarios(id),codigo_beneficiario TEXT,estado TEXT NOT NULL,datos_json TEXT NOT NULL DEFAULT '{}',texto TEXT,mensaje TEXT,seguimiento_id INTEGER REFERENCES seguimientos(id),ruta_original TEXT,ruta_carpeta TEXT,creado TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS residencias(id INTEGER PRIMARY KEY,codigo_beneficiario TEXT NOT NULL REFERENCES beneficiarios(codigo_beneficiario),direccion TEXT NOT NULL,municipio TEXT NOT NULL,latitud REAL,longitud REAL,precision_m REAL,procedencia TEXT NOT NULL,fecha_captura TEXT NOT NULL,confirmado_por INTEGER NOT NULL REFERENCES usuarios(id));
        CREATE TABLE IF NOT EXISTS configuracion_privada(clave TEXT PRIMARY KEY,valor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS oauth_drive_estados(id TEXT PRIMARY KEY,usuario_id INTEGER NOT NULL REFERENCES usuarios(id),vence INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS drive_extracciones(id TEXT PRIMARY KEY,usuario_id INTEGER NOT NULL REFERENCES usuarios(id),codigo_gestora TEXT NOT NULL REFERENCES gestoras(codigo_gestora),fecha TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,filas INTEGER NOT NULL,ruta TEXT NOT NULL,token_revision TEXT);
        ''')
        for table,columns in [('usuarios',[('pin_hash','TEXT'),('politica_version','TEXT'),('politica_aceptada','TEXT')]),('sesiones',[('actividad','INTEGER NOT NULL DEFAULT 0'),('bloqueada','INTEGER NOT NULL DEFAULT 1')]),('beneficiarios',[('direccion_residencia','TEXT')]),('seguimientos',[('solicitud_id','TEXT'),('solicitud_hash','TEXT')]),('residencias',[('solicitud_id','TEXT')])]:
            existing={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for name,kind in columns:
                if name not in existing:db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        for name,kind in [('datos_generales_json','TEXT'),('creado_por','INTEGER REFERENCES usuarios(id)')]:
            if name not in {r['name'] for r in db.execute('PRAGMA table_info(seguimientos)')}:
                db.execute(f'ALTER TABLE seguimientos ADD COLUMN {name} {kind}')
        db.execute('CREATE INDEX IF NOT EXISTS idx_gestora ON beneficiarios(codigo_gestora)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_seguimiento_benef ON seguimientos(codigo_beneficiario)')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_seguimiento_solicitud ON seguimientos(solicitud_id) WHERE solicitud_id IS NOT NULL')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_residencia_solicitud ON residencias(solicitud_id) WHERE solicitud_id IS NOT NULL')
