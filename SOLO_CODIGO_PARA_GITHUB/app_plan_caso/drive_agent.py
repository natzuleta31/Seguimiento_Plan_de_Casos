"""Agente de lectura Google Sheets: OAuth institucional y extracción por gestora."""
import datetime,io,json,os,re,secrets,time,unicodedata,uuid
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,quote
from .infrastructure import conexion
from .security import admin,AccesoDenegado
from . import importer,exports

SCOPES=['openid','https://www.googleapis.com/auth/userinfo.email','https://www.googleapis.com/auth/spreadsheets.readonly']
SOURCE_FIELDS=['consecutivo_origen','nombre_completo','nombres','primer_nombre','apellidos','sexo','tipo_documento','documento','sim','fecha_nacimiento','edad_origen','dia_cumpleanos','mes_cumpleanos','lugar_nacimiento','situacion_legal','resolucion','autoridad_boleta_ingreso','municipio_residencia','fecha_apertura_pard','fecha_ingreso_modalidad','motivo_ingreso','concepto_evaluacion_integradora','fecha_ingreso_hogar_actual','permanencia_hogar_origen','madre_padre_sustituto','autoridad_competente','centro_zonal','diagnostico','eps','regimen','cuenta_con_tutela','fecha_seguimiento_origen','nivel_riesgo_origen','nivel_atencion_origen','seguimientos_mensuales_origen','fecha_reunion_origen','fecha_reunion_actualizada_origen','gestora_origen','observaciones_yo_te_cuido']
def normal(value):return ' '.join(unicodedata.normalize('NFKC',str(value or '')).strip().upper().split())
def config(ruta):
    with conexion(ruta) as db:return {r['clave']:r['valor'] for r in db.execute('SELECT * FROM configuracion_privada')}
def guardar_config(ruta,user,p):
    admin(user);url=p.get('url','');account=str(p.get('cuenta','')).strip().casefold();parts=urlsplit(url)
    m=re.fullmatch(r'/spreadsheets/d/([A-Za-z0-9_-]+)/edit/?',parts.path)
    if parts.scheme!='https' or parts.hostname!='docs.google.com' or not m:raise ValueError('Introduce el enlace de edición de la hoja compartida de Google Sheets')
    gid=(parse_qs(parts.query).get('gid') or parse_qs(parts.fragment).get('gid') or ['0'])[0]
    if not gid.isdigit() or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',account):raise ValueError('Cuenta o identificador de pestaña inválidos')
    with conexion(ruta) as db:
        for k,v in [('drive_sheet_id',m.group(1)),('drive_gid',gid),('drive_account',account)]:db.execute('INSERT OR REPLACE INTO configuracion_privada VALUES(?,?)',(k,v))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'configurar_fuente_drive','hoja_compartida'))
def estado(ruta,user):
    admin(user);c=config(ruta)
    return {'url':'https://docs.google.com/spreadsheets/d/'+c['drive_sheet_id']+'/edit?gid='+c.get('drive_gid','0') if c.get('drive_sheet_id') else '', 'cuenta':c.get('drive_account',''),'conectada':bool(c.get('drive_token')),'cliente_configurado':bool(os.environ.get('APP_GOOGLE_CLIENT_FILE'))}
def _fernet(ruta):
    from cryptography.fernet import Fernet
    path=Path(ruta).resolve().parent/'.google-token-key'
    if not path.exists():
        with path.open('xb') as f:f.write(Fernet.generate_key())
        try:path.chmod(0o600)
        except OSError:pass
    return Fernet(path.read_bytes())
def _flow(redirect):
    from google_auth_oauthlib.flow import Flow
    path=os.environ.get('APP_GOOGLE_CLIENT_FILE')
    if not path or not Path(path).is_file():raise ValueError('Falta configurar el cliente OAuth privado de Google en el servidor; no publiques sus credenciales en GitHub')
    return Flow.from_client_secrets_file(path,scopes=SCOPES,redirect_uri=redirect)
def _redirect():
    base=os.environ.get('APP_BASE_URL','').rstrip('/')
    u=urlsplit(base)
    if u.scheme!='https' and not(u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):raise ValueError('Configura APP_BASE_URL con el origen HTTPS de la app')
    return base+'/api/drive/callback'
def autorizar(ruta,user):
    admin(user);c=config(ruta)
    if not c.get('drive_account'):raise ValueError('Guarda primero la cuenta institucional y la hoja de origen')
    nonce=secrets.token_urlsafe(32);flow=_flow(_redirect())
    url,_=flow.authorization_url(access_type='offline',prompt='consent',login_hint=c['drive_account'],state=nonce)
    with conexion(ruta) as db:
        db.execute('DELETE FROM oauth_drive_estados WHERE vence<?',(int(time.time()),))
        db.execute('INSERT INTO oauth_drive_estados VALUES(?,?,?)',(nonce,user['id'],int(time.time())+600))
    return url
def terminar_autorizacion(ruta,user,code,nonce):
    admin(user)
    with conexion(ruta) as db:
        row=db.execute('SELECT 1 FROM oauth_drive_estados WHERE id=? AND usuario_id=? AND vence>?',(nonce,user['id'],int(time.time()))).fetchone()
        if not row:raise ValueError('La autorización expiró o no corresponde a tu sesión')
        db.execute('DELETE FROM oauth_drive_estados WHERE id=?',(nonce,))
    flow=_flow(_redirect());flow.fetch_token(code=code)
    response=flow.authorized_session().get('https://openidconnect.googleapis.com/v1/userinfo',timeout=30)
    if response.status_code!=200:raise ValueError('No se pudo verificar la cuenta autorizada de Google')
    email=response.json().get('email','').casefold();c=config(ruta)
    if email!=c.get('drive_account'):raise AccesoDenegado('Se autorizó una cuenta distinta de la configurada. Selecciona la cuenta institucional correcta.')
    encrypted=_fernet(ruta).encrypt(flow.credentials.to_json().encode()).decode()
    with conexion(ruta) as db:
        db.execute("INSERT OR REPLACE INTO configuracion_privada VALUES('drive_token',?)",(encrypted,))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'autorizar_drive',email))
def leer_google(ruta):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession,Request
    c=config(ruta)
    if not c.get('drive_token'):raise ValueError('Conecta la cuenta institucional de Drive antes de actualizar')
    creds=Credentials.from_authorized_user_info(json.loads(_fernet(ruta).decrypt(c['drive_token'].encode())),SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        with conexion(ruta) as db:db.execute("UPDATE configuracion_privada SET valor=? WHERE clave='drive_token'",(_fernet(ruta).encrypt(creds.to_json().encode()).decode(),))
    session=AuthorizedSession(creds);base='https://sheets.googleapis.com/v4/spreadsheets/'+c['drive_sheet_id']
    response=session.get(base,params={'fields':'sheets.properties'},timeout=30)
    if response.status_code in (403,404):raise ValueError('La cuenta autorizada no tiene acceso de lectura a esta hoja compartida')
    if response.status_code!=200:raise ValueError('Google no pudo entregar la estructura de la hoja; intenta más tarde')
    target=next((s['properties'] for s in response.json().get('sheets',[]) if str(s['properties']['sheetId'])==c.get('drive_gid')),None)
    if not target:raise ValueError('No se encontró la pestaña configurada en la hoja')
    area="'"+target['title'].replace("'","''")+"'!A1:AM10000"
    result=session.get(base+'/values/'+quote(area,safe=''),params={'valueRenderOption':'UNFORMATTED_VALUE','dateTimeRenderOption':'FORMATTED_STRING'},timeout=45)
    if result.status_code!=200:raise ValueError('No se pudieron leer las filas de la hoja compartida')
    rows=result.json().get('values',[])
    if len(rows)>=10000:raise ValueError('La fuente alcanzó el límite de 10000 filas; amplía el rango antes de actualizar para no omitir registros')
    return rows

def preparar(ruta,user,codigo_gestora,reader=None):
    admin(user)
    with conexion(ruta) as db:
        manager=db.execute('SELECT * FROM gestoras WHERE codigo_gestora=? AND activo=1',(codigo_gestora,)).fetchone()
        if not manager:raise ValueError('Selecciona una gestora activa de la tabla Gestoras')
        manager=dict(manager)
        columns=[r['name'] for r in db.execute('PRAGMA table_info(beneficiarios)')]
        existing={(normal(r['tipo_documento']),str(r['documento']).strip()):dict(r) for r in db.execute('SELECT * FROM beneficiarios')}
    source=(reader or leer_google)(ruta)
    header=next((i for i,row in enumerate(source[:20]) if len(row)>37 and normal(row[37])=='GESTORA'),None)
    if header is None:raise ValueError('No se encontró GESTORA en la columna AL. No se modificó el maestro.')
    rows=[]
    for raw in source[header+1:]:
        raw=list(raw)+[None]*max(0,39-len(raw))
        if normal(raw[37])!=normal(manager['nombre']):continue
        values={k:(str(v).strip() if v is not None else None) for k,v in zip(SOURCE_FIELDS,raw)}
        doc=str(values.get('documento') or '').strip();kind=normal(values.get('tipo_documento'));old=existing.get((kind,doc),{})
        row={**old,**values,'codigo_beneficiario':old.get('codigo_beneficiario'),'codigo_gestora':codigo_gestora,'activo':None if old.get('activo') is None else bool(old['activo'])}
        row['estado_validacion']=old.get('estado_validacion','PENDIENTE_CONFIRMAR_ACTIVO');row['qr_contenido']=old.get('qr_contenido')
        for k,v in list(row.items()):
            if k.startswith('fecha_') and isinstance(v,str) and v:
                for fmt in ('%d/%m/%Y','%d/%m/%y','%Y-%m-%d'):
                    try:row[k]=datetime.datetime.strptime(v,fmt).date().isoformat();break
                    except ValueError:pass
        rows.append(row)
    if not rows:raise ValueError('No hay filas para la gestora seleccionada; no se eliminaron ni retiraron casos')
    rows=[{k:r.get(k) for k in columns} for r in rows]
    blob=exports.excel({'Beneficiarios':exports.tabla(rows),'Gestoras':exports.tabla([manager])})
    preview=importer.previsualizar(ruta,user,blob)
    ident=str(uuid.uuid4());folder=Path(ruta).resolve().parent/'maestros_drive'/ident;folder.mkdir(parents=True,exist_ok=True)
    day=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date().isoformat();path=folder/(day+'_Maestro_'+codigo_gestora+'.xlsx');path.write_bytes(blob)
    with conexion(ruta) as db:
        db.execute('INSERT INTO drive_extracciones(id,usuario_id,codigo_gestora,filas,ruta,token_revision) VALUES(?,?,?,?,?,?)',(ident,user['id'],codigo_gestora,len(rows),str(path),preview['token']))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'extraer_drive',ident))
    return {'extraccion_id':ident,'filas':len(rows),'gestora':manager['nombre'],**preview}
def descargar(ruta,user,ident):
    admin(user)
    with conexion(ruta) as db:r=db.execute('SELECT ruta FROM drive_extracciones WHERE id=?',(ident,)).fetchone()
    if not r:raise ValueError('Extracción no encontrada')
    path=Path(r['ruta']).resolve();root=Path(ruta).resolve().parent/'maestros_drive'
    if not path.is_relative_to(root):raise ValueError('Ruta de extracción inválida')
    return path.name,path.read_bytes()
