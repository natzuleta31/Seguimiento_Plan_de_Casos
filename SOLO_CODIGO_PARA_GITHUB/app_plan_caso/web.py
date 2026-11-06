"""Capa HTTP WSGI. Los controladores delegan en servicios y adaptadores."""
import hashlib,hmac,json,logging,mimetypes,os,re,sqlite3
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs,quote,unquote
from . import services,importer,exports,privacy,incidents,document_agent,template_export,drive_agent
from .infrastructure import conexion
from .security import AccesoDenegado,login,sesion,admin,tocar_sesion,gestionar_pin,autorizar_dispositivo

STATIC=Path(__file__).resolve().parent.parent/'frontend'
MIMES={'pdf':'application/pdf','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','csv':'text/csv; charset=utf-8','svg':'image/svg+xml'}

def application(env,start_response):
    dbpath=os.environ.get('APP_DB','data/plan_caso.sqlite')
    path=env.get('PATH_INFO','/');method=env.get('REQUEST_METHOD','GET')
    headers=[('X-Content-Type-Options','nosniff'),('Referrer-Policy','no-referrer'),('Cache-Control','no-store'),('X-Frame-Options','DENY'),('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")]
    def respond(data,status='200 OK',mime='application/json; charset=utf-8',extra=()):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode('utf8')
        start_response(status,headers+[('Content-Type',mime),('Content-Length',str(len(raw)))]+list(extra));return [raw]
    def payload():
        length=int(env.get('CONTENT_LENGTH') or 0)
        if length>8_000_000:raise ValueError('Máximo 8 MB por solicitud')
        return env['wsgi.input'].read(length)
    def obj():
        try:
            value=json.loads(payload())
            if not isinstance(value,dict):raise ValueError('Se esperaba un objeto')
            return value
        except (json.JSONDecodeError,UnicodeDecodeError):raise ValueError('La solicitud no contiene datos válidos')
    production=os.environ.get('APP_ENV')=='production'
    cookie_flags='; HttpOnly; SameSite=Lax; Path=/'+('; Secure' if production else '')
    try:
        if path=='/api/health' and method=='GET':return respond({'status':'ok'})
        if path=='/api/politica' and method=='GET':return respond(privacy.politica())
        if not path.startswith('/api/'):
            if method not in ('GET','HEAD'):return respond({'error':'Método no permitido'},'405 Method Not Allowed')
            files={'/':'index.html','/index.html':'index.html','/styles.css':'styles.css','/app.js':'app.js','/offline.js':'offline.js','/sw.js':'sw.js','/icon.svg':'icon.svg','/manifest.webmanifest':'manifest.webmanifest'}
            map_files={'/mapas/mapa.html':'mapa.html','/mapas/mapa.js':'mapa.js','/mapas/mapa.css':'mapa.css','/mapas/datos.js':'datos.js'}
            if path in map_files:
                target=STATIC.parent/'mapas'/map_files[path]
                return respond(target.read_bytes(),mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
            if path not in files:return respond({'error':'Página no encontrada'},'404 Not Found')
            target=STATIC/files[path]
            if not target.exists():return respond({'error':'Interfaz no disponible'},'404 Not Found')
            mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            return respond(target.read_bytes(),'200 OK',mime)
        if path=='/api/login' and method=='POST':
            p=obj();username=p.get('usuario','');password=p.get('password','')
            if not isinstance(username,str) or not isinstance(password,str) or len(username)>150 or len(password)>1024:raise ValueError('Credenciales inválidas')
            token,csrf=login(dbpath,username.strip(),password,env.get('REMOTE_ADDR','local'))
            return respond({'csrf':csrf},extra=[('Set-Cookie','session='+token+cookie_flags+'; Max-Age=28800')])
        cookie=SimpleCookie();cookie.load(env.get('HTTP_COOKIE',''));token=cookie['session'].value if 'session' in cookie else ''
        user=sesion(dbpath,token)
        if method not in ('GET','HEAD') and not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN',''),user['csrf']):return respond({'error':'Solicitud no autorizada. Recarga la página.'},'403 Forbidden')
        if path=='/api/me' and method=='GET':return respond(user)
        if path=='/api/logout' and method=='POST':
            with conexion(dbpath) as db:db.execute('DELETE FROM sesiones WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
            return respond({'ok':True},extra=[('Set-Cookie','session=; Max-Age=0'+cookie_flags)])
        if path=='/api/drive/callback' and method=='GET':
            query=parse_qs(env.get('QUERY_STRING',''))
            if query.get('error'):raise ValueError('La autorización de Google fue cancelada o denegada')
            drive_agent.terminar_autorizacion(dbpath,user,(query.get('code') or [''])[0],(query.get('state') or [''])[0])
            return respond('<!doctype html><html lang="es"><meta charset="utf-8"><title>Drive autorizado</title><h1>Cuenta de Drive autorizada</h1><p>La app tiene acceso de lectura a la hoja configurada.</p><a href="/">Volver a Plan de Caso</a></html>'.encode(),mime='text/html; charset=utf-8')
        if path=='/api/bloquear' and method=='POST':
            tocar_sesion(dbpath,token,True);return respond({'bloqueada':True})
        if path in ('/api/pin/configurar','/api/pin/desbloquear','/api/pin/recuperar') and method=='POST':
            gestionar_pin(dbpath,user,token,obj(),path.rsplit('/',1)[1]);return respond(sesion(dbpath,token))
        if user['bloqueada']:return respond({'error':'La app está bloqueada. Ingresa tu PIN para continuar.','bloqueada':True,'requiere_pin':not user['tiene_pin']},'423 Locked')
        if path=='/api/politica/aceptar' and method=='POST':
            if obj().get('version')!=privacy.VERSION:raise ValueError('Lee la versión vigente de la política')
            with conexion(dbpath) as db:
                db.execute('UPDATE usuarios SET politica_version=?,politica_aceptada=CURRENT_TIMESTAMP WHERE id=?',(privacy.VERSION,user['id']))
                db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'lectura_politica',privacy.VERSION))
            tocar_sesion(dbpath,token);return respond({'ok':True})
        if user['politica_version']!=privacy.VERSION:return respond({'error':'Lee y acepta el compromiso de confidencialidad para continuar.','politica_requerida':True},'428 Precondition Required')
        tocar_sesion(dbpath,token)
        if path=='/api/actividad' and method=='POST':return respond({'ok':True})
        if path=='/api/dispositivo/autorizar' and method=='POST':
            autorizar_dispositivo(dbpath,user,obj());return respond({'ok':True})
        if path=='/api/drive/config' and method=='GET':return respond(drive_agent.estado(dbpath,user))
        if path=='/api/drive/config' and method=='POST':
            drive_agent.guardar_config(dbpath,user,obj());return respond({'ok':True})
        if path=='/api/drive/autorizar' and method=='POST':return respond({'url':drive_agent.autorizar(dbpath,user)})
        if path=='/api/drive/actualizar' and method=='POST':return respond(drive_agent.preparar(dbpath,user,obj().get('codigo_gestora')))
        match=re.fullmatch(r'/api/drive/extracciones/([a-f0-9-]+)',path)
        if match and method=='GET':
            name,data=drive_agent.descargar(dbpath,user,match.group(1));return respond(data,mime=MIMES['xlsx'],extra=[('Content-Disposition',f'attachment; filename="{name}"')])
        if path=='/api/fallos' and method=='GET':return respond(incidents.listar(dbpath,user))
        if path=='/api/documentos' and method=='GET':return respond(document_agent.listar(dbpath,user))
        if path=='/api/plantilla' and method=='GET':
            admin(user);return respond({'cargada':template_export.template_path(dbpath).exists()})
        if path=='/api/plantilla' and method=='POST':
            template_export.guardar_plantilla(dbpath,user,payload());return respond({'ok':True})
        if path=='/api/documentos/cargar' and method=='POST':return respond(document_agent.recibir(dbpath,user,payload(),unquote(env.get('HTTP_X_FILE_NAME','documento.pdf'))),'201 Created')
        if path=='/api/agente/actualizar' and method=='POST':return respond(document_agent.actualizar_carpeta(dbpath,user))
        match=re.fullmatch(r'/api/documentos/(\d+)(?:/(original|excel|reintentar))?',path)
        if match:
            ident,action=match.groups();ident=int(ident)
            if method=='GET' and action is None:return respond(document_agent.detalle(dbpath,user,ident))
            if method=='GET' and action in ('original','excel'):
                data=document_agent.archivo(dbpath,user,ident,action)
                ext='pdf' if action=='original' else 'xlsx'
                if action=='excel':
                    d=document_agent.detalle(dbpath,user,ident);s=services.informe(dbpath,user,d['seguimiento_id']);name=template_export.nombre_archivo(s)+'.xlsx'
                    disposition=f'attachment; filename="registro_{ident}.xlsx"; filename*=UTF-8\'\'{quote(name)}'
                else:disposition=f'attachment; filename="documento_{ident}.{ext}"'
                return respond(data,mime=MIMES[ext],extra=[('Content-Disposition',disposition)])
            if method=='POST' and action=='reintentar':
                d=document_agent.detalle(dbpath,user,ident)
                if not d['seguimiento_id']:raise ValueError('Revisa y guarda el documento antes de archivarlo')
                return respond({'estado':document_agent.archivar(dbpath,user,d['seguimiento_id'])})
        match=re.fullmatch(r'/api/fallos/(\d+)/resolver',path)
        if match and method=='POST':
            incidents.resolver(dbpath,user,int(match.group(1)),obj().get('nota'));return respond({'ok':True})
        if path=='/api/beneficiarios' and method=='GET':return respond(services.listar(dbpath,user))
        if path=='/api/gestoras':
            if method=='GET':return respond(services.gestoras(dbpath,user))
            if method=='POST':return respond(services.gestoras(dbpath,user,obj().get('nombre','')),'201 Created')
        if path=='/api/importar/revisar' and method=='POST':return respond(importer.previsualizar(dbpath,user,payload()))
        if path=='/api/importar/aplicar' and method=='POST':return respond({'aplicados':importer.aplicar(dbpath,user,obj().get('token'))})
        if path=='/api/maestro.xlsx' and method=='GET':
            admin(user)
            with conexion(dbpath) as db:
                records=[dict(r) for r in db.execute('SELECT * FROM beneficiarios ORDER BY codigo_beneficiario')];managers=[dict(r) for r in db.execute('SELECT * FROM gestoras')]
                failures=[dict(r) for r in db.execute('SELECT * FROM fallos_documentales ORDER BY id')]
                residences=[dict(r) for r in db.execute('SELECT * FROM residencias ORDER BY id')]
            return respond(exports.maestro_excel(records,managers,failures,residences),mime=MIMES['xlsx'],extra=[('Content-Disposition','attachment; filename="maestro_beneficiarios.xlsx"')])
        match=re.fullmatch(r'/api/beneficiarios/(BEN-\d{6})(?:/(estado|seguimientos|qr|residencia))?',path)
        if match:
            code,action=match.groups()
            if action is None and method=='GET':return respond(services.detalle(dbpath,user,code))
            if action=='estado' and method=='POST':services.estado(dbpath,user,code,obj());return respond({'ok':True})
            if action=='residencia' and method=='POST':return respond({'id':services.guardar_residencia(dbpath,user,code,obj())},'201 Created')
            if action=='seguimientos' and method=='POST':
                ident=services.guardar_seguimiento(dbpath,user,code,obj())
                archived=document_agent.archivar(dbpath,user,ident)
                return respond({'id':ident,'archivo':archived},'201 Created')
            if action=='qr' and method=='GET':
                b=services.detalle(dbpath,user,code)
                return respond(exports.qr_svg(b['qr_contenido']),mime=MIMES['svg'],extra=[('Content-Disposition',f'attachment; filename="{code}.svg"')])
        match=re.fullmatch(r'/api/seguimientos/(\d+)(?:\.(pdf|csv|xlsx))?',path)
        if match and method in ('GET','PUT'):
            ident,fmt=match.groups();s=services.informe(dbpath,user,int(ident))
            if method=='PUT':
                if fmt:raise ValueError('Ruta de edición inválida')
                updated=services.guardar_seguimiento(dbpath,user,s['codigo_beneficiario'],obj(),int(ident))
                return respond({'id':updated,'archivo':document_agent.archivar(dbpath,user,updated)})
            if not fmt:return respond(s)
            content=template_export.generar(dbpath,s) if fmt=='xlsx' else exports.generar_informe(s,fmt)
            with conexion(dbpath) as db:
                db.execute('INSERT INTO entregables(seguimiento_id,formato,fecha_generacion) VALUES(?,?,CURRENT_TIMESTAMP)',(ident,fmt))
                db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'exportar_'+fmt,ident))
            filename=template_export.nombre_archivo(s)+'.'+fmt
            return respond(content,mime=MIMES[fmt],extra=[('Content-Disposition',f'attachment; filename="registro_{ident}.{fmt}"; filename*=UTF-8\'\'{quote(filename)}')])
        return respond({'error':'Ruta o método no disponible'},'404 Not Found')
    except AccesoDenegado as e:return respond({'error':str(e)},'403 Forbidden' if 'user' in locals() else '401 Unauthorized')
    except (ValueError,TypeError,KeyError) as e:
        if 'user' in locals() and path in ('/api/importar/revisar','/api/plantilla','/api/documentos/cargar'):
            with conexion(dbpath) as db:incidents.registrar(db,user['id'],str(e),fuente='carga de maestro' if path=='/api/importar/revisar' else 'carga de plantilla' if path=='/api/plantilla' else 'carga PDF')
        if 'user' in locals() and re.fullmatch(r'/api/beneficiarios/BEN-\d{6}/seguimientos|/api/seguimientos/\d+',path) and method in ('POST','PUT'):
            with conexion(dbpath) as db:incidents.registrar(db,user['id'],str(e),fuente='formulario de seguimiento',codigo=path.split('/')[3] if '/beneficiarios/' in path else None)
        return respond({'error':str(e)},'400 Bad Request')
    except sqlite3.IntegrityError:return respond({'error':'Los datos entran en conflicto con un registro existente'},'409 Conflict')
    except Exception:
        logging.exception('Error en la aplicación')
        return respond({'error':'No se pudo completar la operación. Revisa la configuración del servidor.'},'500 Internal Server Error')
