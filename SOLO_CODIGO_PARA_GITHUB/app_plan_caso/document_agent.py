"""Agente documental local: extracción, revisión humana y archivo; sin APIs de pago."""
import hashlib,io,json,os,re
from pathlib import Path
from .infrastructure import conexion,buscar_por_codigo
from .security import autorizar,AccesoDenegado,admin
from .incidents import registrar

def raiz(ruta):
    path=Path(os.environ.get('APP_DOCUMENTS_DIR') or str(Path(ruta).resolve().parent/'documentos')).resolve()
    path.mkdir(parents=True,exist_ok=True);return path

def permiso(db,user,document):
    if not document:raise ValueError('Documento no encontrado')
    if document['codigo_beneficiario']:
        benef=buscar_por_codigo(db,document['codigo_beneficiario'])
        if benef:autorizar(user,benef);return
    if user['rol']!='admin' and user['id']!=document['usuario_id']:raise AccesoDenegado('No tienes acceso a este documento')

def extraer(content):
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(content),strict=False)
    if reader.is_encrypted:raise ValueError('El PDF está protegido con contraseña; carga una copia autorizada sin cifrado')
    if len(reader.pages)>100:raise ValueError('Máximo 100 páginas por documento')
    text='\n'.join((page.extract_text() or '') for page in reader.pages)[:500000]
    attachments=reader.attachments.get('plan-caso.json',[])
    if attachments:
        if len(attachments)!=1 or len(attachments[0])>1_000_000:raise ValueError('Adjunto estructurado ambiguo o demasiado grande')
        data=json.loads(attachments[0])
        if data.get('formato')!='plan-caso/1' or not isinstance(data.get('registro'),dict):raise ValueError('Formato estructurado no reconocido')
        record=data['registro']
        if not all(isinstance(record.get(k,[]),list) and len(record.get(k,[]))<=100 and all(isinstance(x,dict) for x in record.get(k,[])) for k in ['avances','nuevas_atenciones','participantes']):raise ValueError('Filas del adjunto inválidas')
        if not isinstance(record.get('generales',{}),dict):raise ValueError('Datos generales del adjunto inválidos')
        return record,text,'revision','PDF con datos estructurados. Revisa y confirma antes de registrar; el adjunto no acredita autenticidad.'
    record={'generales':{},'avances':[],'nuevas_atenciones':[],'participantes':[]}
    code=re.search(r'\bBEN-\d{6}\b',text)
    if code:record['codigo_beneficiario']=code.group()
    day=re.search(r'Fecha de elaboración\s*[:\n]?\s*(\d{4}-\d{2}-\d{2})',text,re.I)
    if day:record['fecha_elaboracion']=day.group(1)
    if len(text.strip())<30:return record,text,'requiere_ocr','No se encontró texto suficiente. Requiere OCR o diligenciamiento manual; no se creó ningún seguimiento.'
    return record,text,'revision_manual','Texto extraído. Este PDF no contiene el formato estructurado de la app; transcribe y valida sus campos en el formulario.'

def recibir(ruta,user,content,nombre):
    if not content.startswith(b'%PDF-'):raise ValueError('El archivo no es un PDF válido')
    if len(content)>8_000_000:raise ValueError('Máximo 8 MB por PDF')
    digest=hashlib.sha256(content).hexdigest();name=Path(nombre.replace('\\','/')).name[:150] or 'documento.pdf'
    with conexion(ruta) as db:
        old=db.execute('SELECT * FROM documentos_pdf WHERE sha256=?',(digest,)).fetchone()
        if old:
            permiso(db,user,old);return {'id':old['id'],'duplicado':True,'estado':old['estado']}
    try:record,text,status,message=extraer(content)
    except Exception as error:
        record,text,status={},'','error'
        message=str(error)[:1000] if isinstance(error,(ValueError,json.JSONDecodeError)) else 'No fue posible leer el PDF. Revisa que no esté dañado.'
    code=record.get('codigo_beneficiario')
    if code and not re.fullmatch(r'BEN-\d{6}',str(code)):code=None;record.pop('codigo_beneficiario',None)
    with conexion(ruta) as db:
        db.execute('BEGIN IMMEDIATE')
        if code:
            benef=buscar_por_codigo(db,code)
            if benef:
                autorizar(user,benef)
                doc=record.get('generales',{}).get('documento')
                if doc and str(doc)!=str(benef['documento']):status='revision_manual';message='El documento de identidad del PDF no coincide con el maestro. Revisa la vinculación antes de guardar.'
            else:code=None;message+=' El código no está en el maestro; selecciona el beneficiario correcto.'
        folder=raiz(ruta)/'entrada';folder.mkdir(exist_ok=True);original=folder/(digest+'.pdf')
        original.write_bytes(content)
        ident=db.execute('INSERT INTO documentos_pdf(sha256,nombre,usuario_id,codigo_beneficiario,estado,datos_json,texto,mensaje,ruta_original) VALUES(?,?,?,?,?,?,?,?,?)',(digest,name,user['id'],code,status,json.dumps(record,ensure_ascii=False),text,message,str(original))).lastrowid
        if status!='revision':registrar(db,user['id'],message,fuente=name,tipo='lectura_pdf',codigo=code)
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'leer_pdf',str(ident)))
        return {'id':ident,'duplicado':False,'estado':status}

def listar(ruta,user):
    result=[]
    with conexion(ruta) as db:
        for row in db.execute('SELECT * FROM documentos_pdf ORDER BY id DESC LIMIT 500'):
            try:permiso(db,user,row)
            except AccesoDenegado:continue
            result.append({k:row[k] for k in ['id','nombre','codigo_beneficiario','estado','mensaje','seguimiento_id','creado']})
    return result

def detalle(ruta,user,ident):
    with conexion(ruta) as db:
        row=db.execute('SELECT * FROM documentos_pdf WHERE id=?',(ident,)).fetchone();permiso(db,user,row)
        result=dict(row);result['registro']=json.loads(result.pop('datos_json'));result.pop('ruta_original',None);result.pop('ruta_carpeta',None);return result

def archivo(ruta,user,ident,tipo):
    with conexion(ruta) as db:
        row=db.execute('SELECT * FROM documentos_pdf WHERE id=?',(ident,)).fetchone();permiso(db,user,row)
        if tipo=='original':path=Path(row['ruta_original'])
        elif tipo=='excel' and row['ruta_carpeta']:
            from .services import informe
            from .template_export import nombre_archivo
            s=informe(ruta,user,row['seguimiento_id']);path=Path(row['ruta_carpeta'])/(nombre_archivo(s)+'.xlsx')
        else:raise ValueError('El archivo todavía no está disponible')
        if not path.resolve().is_relative_to(raiz(ruta)):raise ValueError('Ruta de archivo inválida')
        return path.read_bytes()

def archivar(ruta,user,seguimiento_id):
    from .services import informe
    from .exports import generar_informe
    from .template_export import nombre_archivo,generar
    s=informe(ruta,user,seguimiento_id)
    with conexion(ruta) as db:doc=db.execute('SELECT * FROM documentos_pdf WHERE seguimiento_id=?',(seguimiento_id,)).fetchone()
    if not doc:return None
    basename=nombre_archivo(s)
    folder=raiz(ruta)/s['codigo_beneficiario']/basename/str(seguimiento_id)
    try:
        folder.mkdir(parents=True,exist_ok=True)
        (folder/(basename+'.pdf')).write_bytes(Path(doc['ruta_original']).read_bytes())
        (folder/(basename+'.xlsx')).write_bytes(generar(ruta,s))
        (folder/(basename+'.json')).write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf8')
        with conexion(ruta) as db:db.execute("UPDATE documentos_pdf SET estado='archivado',ruta_carpeta=?,mensaje=? WHERE id=?",(str(folder),'Registro confirmado y archivado con su PDF y Excel.',doc['id']))
        return 'archivado'
    except (OSError,ValueError) as error:
        with conexion(ruta) as db:
            db.execute("UPDATE documentos_pdf SET estado='error_archivo',mensaje='Registro guardado en BD; archivo pendiente. Puedes reintentar sin duplicarlo.' WHERE id=?",(doc['id'],))
            registrar(db,user['id'],'Registro guardado en BD; archivo pendiente: '+str(error)[:700],fuente=doc['nombre'],tipo='archivo_pdf',codigo=s['codigo_beneficiario'])
        return 'error_archivo'

def actualizar_carpeta(ruta,user):
    admin(user)
    inbox=Path(os.environ.get('APP_INBOX_DIR') or str(raiz(ruta)/'pendientes')).resolve();inbox.mkdir(parents=True,exist_ok=True)
    results=[]
    for file in sorted(inbox.glob('*.pdf'))[:50]:
        if file.is_symlink() or not file.resolve().is_relative_to(inbox):continue
        try:
            if file.stat().st_size>8_000_000:raise ValueError('Máximo 8 MB por PDF')
            results.append({'archivo':file.name,**recibir(ruta,user,file.read_bytes(),file.name)})
        except (ValueError,AccesoDenegado) as e:results.append({'archivo':file.name,'error':str(e)})
    return results
