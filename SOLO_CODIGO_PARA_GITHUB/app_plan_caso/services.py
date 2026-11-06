"""Capa de aplicación: expedientes, seguimientos y administración."""
import datetime,json,math,uuid,hashlib
from .infrastructure import conexion,buscar_por_codigo
from .security import autorizar,admin
from .domain import validar_codigo,validar_activo

def texto(value,limite=20000):
    if not isinstance(value,str): raise ValueError('El campo de texto tiene un formato inválido')
    if len(value)>limite: raise ValueError('El texto supera el tamaño permitido')
    return value.strip()

def fecha(value,requerida=False):
    if not value and not requerida:return None
    try:return datetime.date.fromisoformat(value).isoformat()
    except (TypeError,ValueError):raise ValueError('Fecha inválida; usa AAAA-MM-DD')

def listar(ruta,user):
    with conexion(ruta) as db:
        where='' if user['rol']=='admin' else ' WHERE b.codigo_gestora=?'
        args=() if user['rol']=='admin' else (user['codigo_gestora'],)
        return [dict(r) for r in db.execute('SELECT b.codigo_beneficiario,b.nombre_completo,b.nombres,b.documento,b.sim,b.activo,b.codigo_gestora,g.nombre AS gestora,b.centro_zonal,b.nivel_riesgo_origen,(SELECT COUNT(*) FROM seguimientos s WHERE s.codigo_beneficiario=b.codigo_beneficiario) AS total_seguimientos FROM beneficiarios b JOIN gestoras g ON b.codigo_gestora=g.codigo_gestora'+where+' ORDER BY b.nombre_completo',args)]

def detalle(ruta,user,codigo):
    validar_codigo(codigo)
    with conexion(ruta) as db:
        b=buscar_por_codigo(db,codigo);autorizar(user,b)
        residence=db.execute('SELECT * FROM residencias WHERE codigo_beneficiario=? ORDER BY id DESC LIMIT 1',(codigo,)).fetchone()
        b['residencia']=dict(residence) if residence else None
        b['seguimientos']=[dict(r) for r in db.execute('SELECT id,fecha_elaboracion,nivel_riesgo,estado FROM seguimientos WHERE codigo_beneficiario=? ORDER BY fecha_elaboracion DESC,id DESC',(codigo,))]
        return b

def guardar_residencia(ruta,user,codigo,p):
    validar_codigo(codigo)
    direction=texto(p.get('direccion',''),1000);city=texto(p.get('municipio',''),200)
    if not direction or not city:raise ValueError('Completa dirección y municipio de residencia')
    if p.get('confirmado') is not True:raise ValueError('Confirma que los datos corresponden a la residencia del beneficiario')
    lat=p.get('latitud');lon=p.get('longitud');accuracy=p.get('precision_m')
    if (lat is None)!=(lon is None):raise ValueError('Se necesitan ambas coordenadas')
    if lat is not None:
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in [lat,lon]):raise ValueError('Coordenadas inválidas')
        if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Coordenadas fuera de rango')
        if type(accuracy) not in (int,float) or not math.isfinite(accuracy) or accuracy<0:raise ValueError('Precisión GPS inválida')
    else:accuracy=None
    stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conexion(ruta) as db:
        db.execute('BEGIN IMMEDIATE')
        benef=buscar_por_codigo(db,codigo);autorizar(user,benef)
        request_id=p.get('solicitud_id')
        if request_id:
            try:request_id=str(uuid.UUID(request_id))
            except (ValueError,TypeError,AttributeError):raise ValueError('Identificador de sincronización inválido')
            old=db.execute('SELECT id,codigo_beneficiario FROM residencias WHERE solicitud_id=?',(request_id,)).fetchone()
            if old:
                if old['codigo_beneficiario']!=codigo:raise ValueError('La solicitud ya corresponde a otro caso')
                return old['id']
        ident=db.execute('INSERT INTO residencias(codigo_beneficiario,direccion,municipio,latitud,longitud,precision_m,procedencia,fecha_captura,confirmado_por) VALUES(?,?,?,?,?,?,?,?,?)',(codigo,direction,city,lat,lon,accuracy,'GPS_DISPOSITIVO' if lat is not None else 'MANUAL',stamp,user['id'])).lastrowid
        db.execute('UPDATE beneficiarios SET direccion_residencia=?,municipio_residencia=? WHERE codigo_beneficiario=?',(direction,city,codigo))
        if request_id:db.execute('UPDATE residencias SET solicitud_id=? WHERE id=?',(request_id,ident))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'registrar_residencia',codigo))
        return ident

def gestoras(ruta,user,nombre=None):
    with conexion(ruta) as db:
        if nombre is not None:
            admin(user);nombre=texto(nombre,150)
            if not nombre:raise ValueError('Indica el nombre de la gestora')
            if db.execute('SELECT 1 FROM gestoras WHERE UPPER(nombre)=UPPER(?)',(nombre,)).fetchone():raise ValueError('La gestora ya está registrada')
            n=(db.execute('SELECT MAX(CAST(SUBSTR(codigo_gestora,5) AS INTEGER)) FROM gestoras').fetchone()[0] or 0)+1
            db.execute('INSERT INTO gestoras VALUES(?,?,1)',(f'GES-{n:04d}',nombre))
            db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'alta_gestora',f'GES-{n:04d}'))
        return [dict(r) for r in db.execute('SELECT * FROM gestoras ORDER BY nombre')]

def estado(ruta,user,codigo,payload):
    admin(user);validar_codigo(codigo);value=validar_activo(payload.get('activo'))
    if value is None:raise ValueError('Selecciona activo o retirado')
    day=fecha(payload.get('fecha'),True);reason=texto(payload.get('motivo',''),2000)
    if not reason:raise ValueError('Indica el motivo del cambio')
    with conexion(ruta) as db:
        b=buscar_por_codigo(db,codigo);autorizar(user,b)
        if b['activo']==value:return
        db.execute('UPDATE beneficiarios SET activo=?,estado_validacion=?,fecha_retiro=?,motivo_retiro=? WHERE codigo_beneficiario=?',(value,'ESTADO_CONFIRMADO',day if value==0 else None,reason if value==0 else None,codigo))
        db.execute('INSERT INTO historial_estados(codigo_beneficiario,activo_anterior,activo_nuevo,fecha,motivo) VALUES(?,?,?,?,?)',(codigo,b['activo'],value,day,reason))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'cambio_estado',codigo))

GENERALES=['regional','operador','modalidad','escolaridad','centro_zonal','autoridad_competente','fecha_apertura_pard','fecha_ingreso_modalidad','motivo_ingreso','concepto_evaluacion_integradora']
def hoy():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-5))).date().isoformat()

def guardar_seguimiento(ruta,user,codigo,p,editar_id=None):
    validar_codigo(codigo)
    day=fecha(p.get('fecha_elaboracion'),True)
    if day>hoy():raise ValueError('La fecha del seguimiento no puede ser futura')
    risk=p.get('nivel_riesgo') or None
    if risk not in (None,'Alto/Emergencia','Media','Bajo'):raise ValueError('Nivel de riesgo inválido')
    status=p.get('estado','borrador')
    if status not in ('borrador','finalizado'):raise ValueError('Estado del informe inválido')
    advances=p.get('avances',[]);actions=p.get('nuevas_atenciones',[]);people=p.get('participantes',[])
    if not all(isinstance(a,list) and len(a)<=100 and all(isinstance(row,dict) for row in a) for a in [advances,actions,people]):raise ValueError('Cada sección debe contener un máximo de 100 filas válidas')
    conclusion=texto(p.get('conclusiones',''));perception=texto(p.get('percepcion_beneficiario_red',''))
    sent=fecha(p.get('fecha_envio_autoridad'))
    if sent and sent<day:raise ValueError('El envío no puede ser anterior a la elaboración')
    if sent and sent>hoy():raise ValueError('La fecha de envío no puede ser futura')
    with conexion(ruta) as db:
        db.execute('BEGIN IMMEDIATE')
        b=buscar_por_codigo(db,codigo);autorizar(user,b)
        document_id=p.get('documento_revision_id')
        request_id=p.get('solicitud_id')
        request_hash=hashlib.sha256(json.dumps({k:v for k,v in p.items() if k not in ('solicitud_id','codigo_beneficiario')},sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
        if request_id and not editar_id:
            try:request_id=str(uuid.UUID(request_id))
            except (ValueError,TypeError,AttributeError):raise ValueError('Identificador de sincronización inválido')
            old=db.execute('SELECT id,codigo_beneficiario,estado,solicitud_hash FROM seguimientos WHERE solicitud_id=?',(request_id,)).fetchone()
            if old:
                if old['codigo_beneficiario']!=codigo:raise ValueError('La solicitud ya corresponde a otro caso')
                if old['estado']=='finalizado':
                    if old['solicitud_hash']!=request_hash:raise ValueError('La solicitud ya se finalizó con otros datos; el registro local debe revisarse sin sobrescribir el informe emitido')
                    return old['id']
                editar_id=old['id']
        if document_id:
            from .document_agent import permiso
            if type(document_id) is not int:raise ValueError('Referencia documental inválida')
            document=db.execute('SELECT * FROM documentos_pdf WHERE id=?',(document_id,)).fetchone();permiso(db,user,document)
            if document['estado']=='error':raise ValueError('El PDF tiene errores de lectura y no puede vincularse')
            if document['seguimiento_id'] and document['seguimiento_id']!=editar_id:raise ValueError('Este PDF ya fue registrado; consulta su seguimiento para evitar duplicados')
            if p.get('documento_revisado') is not True:raise ValueError('Confirma que revisaste el PDF y la vinculación del beneficiario')
        if b['activo']==0:raise ValueError('El beneficiario está retirado. Confirma su reingreso antes de registrar una atención.')
        snap=dict(b)
        general=p.get('generales',{})
        if not isinstance(general,dict):raise ValueError('Información general inválida')
        for key in GENERALES:
            if key in general:
                if key.startswith('fecha_') and snap.get(key):
                    try:datetime.date.fromisoformat(snap[key])
                    except ValueError:snap[key+'_referencia_origen']=snap[key]
                snap[key]=fecha(general[key]) if key.startswith('fecha_') else texto(general[key])
        if status=='finalizado':
            missing=[k for k in ['regional','centro_zonal','operador','modalidad','escolaridad','autoridad_competente','fecha_apertura_pard','fecha_ingreso_modalidad'] if not snap.get(k)]
            if missing:raise ValueError('Completa la información general antes de finalizar: '+', '.join(missing))
            if not risk or not conclusion or not advances or not people:raise ValueError('Para finalizar completa riesgo, avances, conclusiones y participantes')
        if editar_id:
            existing=db.execute('SELECT * FROM seguimientos WHERE id=? AND codigo_beneficiario=?',(editar_id,codigo)).fetchone()
            if not existing or existing['estado']!='borrador':raise ValueError('Solo se pueden editar borradores existentes')
            ident=editar_id
            db.execute('UPDATE seguimientos SET fecha_elaboracion=?,nivel_riesgo=?,conclusiones=?,percepcion_beneficiario_red=?,fecha_envio_autoridad=?,estado=?,datos_generales_json=? WHERE id=?',(day,risk,conclusion,perception,sent,status,json.dumps(snap,ensure_ascii=False),ident))
            for table in ['avances','nuevas_atenciones','participantes']:db.execute(f'DELETE FROM {table} WHERE seguimiento_id=?',(ident,))
        else:
            ident=db.execute('INSERT INTO seguimientos(codigo_beneficiario,codigo_gestora,fecha_elaboracion,nivel_riesgo,conclusiones,percepcion_beneficiario_red,fecha_envio_autoridad,estado,datos_generales_json,creado_por) VALUES(?,?,?,?,?,?,?,?,?,?)',(codigo,b['codigo_gestora'],day,risk,conclusion,perception,sent,status,json.dumps(snap,ensure_ascii=False),user['id'])).lastrowid
        for a in advances:
            vals=[texto(a.get(k,'')) for k in ['situacion_abordada','atencion_realizada','avance_cuantitativo','avance_cualitativo']]
            if status=='finalizado' and not all(vals):raise ValueError('Completa los cuatro campos de cada avance')
            extra=validar_activo(a.get('requiere_atenciones_adicionales'))
            if status=='finalizado' and extra is None:raise ValueError('Indica si el avance requiere atención adicional')
            db.execute('INSERT INTO avances(seguimiento_id,situacion_abordada,atencion_realizada,avance_cuantitativo,avance_cualitativo,requiere_atenciones_adicionales) VALUES(?,?,?,?,?,?)',(ident,*vals,extra))
        for a in actions:
            gp=a.get('prioridad');nat=a.get('nivel_atencion')
            if type(gp) is not int or gp not in (1,2,3) or nat not in ('I','F','S'):raise ValueError('Revisa prioridad GP y nivel NAT')
            due=fecha(a.get('fecha_limite'),status=='finalizado')
            vals=[texto(a.get(k,'')) for k in ['situacion','resultado_esperado','atencion_necesaria','responsable']]
            if status=='finalizado' and not all(vals):raise ValueError('Completa cada nueva atención')
            db.execute('INSERT INTO nuevas_atenciones(seguimiento_id,situacion,prioridad,nivel_atencion,resultado_esperado,atencion_necesaria,responsable,fecha_limite) VALUES(?,?,?,?,?,?,?,?)',(ident,vals[0],gp,nat,*vals[1:],due))
        for a in people:
            vals=[texto(a.get(k,''),2000) for k in ['rol_en_caso','nombre','profesion']]
            if status=='finalizado' and not all(vals):raise ValueError('Completa rol, nombre y profesión de los participantes')
            db.execute('INSERT INTO participantes(seguimiento_id,rol_en_caso,nombre,profesion) VALUES(?,?,?,?)',(ident,*vals))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'editar_seguimiento' if editar_id else 'crear_seguimiento',str(ident)))
        if request_id:db.execute('UPDATE seguimientos SET solicitud_id=?,solicitud_hash=? WHERE id=?',(request_id,request_hash,ident))
        if document_id:db.execute("UPDATE documentos_pdf SET codigo_beneficiario=?,seguimiento_id=?,estado='pendiente_archivo' WHERE id=?",(codigo,ident,document_id))
        return ident

def informe(ruta,user,ident):
    with conexion(ruta) as db:
        s=db.execute('SELECT * FROM seguimientos WHERE id=?',(ident,)).fetchone()
        if not s:raise ValueError('Seguimiento no encontrado')
        s=dict(s);autorizar(user,buscar_por_codigo(db,s['codigo_beneficiario']))
        doc=db.execute('SELECT id FROM documentos_pdf WHERE seguimiento_id=?',(ident,)).fetchone()
        if doc:s['documento_revision_id']=doc['id']
        s['generales']=json.loads(s.pop('datos_generales_json') or '{}')
        for key in ['avances','nuevas_atenciones','participantes']:
            s[key]=[dict(r) for r in db.execute(f'SELECT * FROM {key} WHERE seguimiento_id=?',(ident,))]
        return s
