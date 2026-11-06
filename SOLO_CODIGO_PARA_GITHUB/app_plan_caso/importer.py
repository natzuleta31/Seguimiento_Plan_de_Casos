"""Importación revisable de maestro: códigos centrales y cambios atómicos."""
import datetime,hashlib,io,json,re,secrets,time,zipfile
from .infrastructure import conexion
from .security import admin
from .incidents import registrar

def fingerprint(db):
    data={'beneficiarios':[tuple(r) for r in db.execute('SELECT * FROM beneficiarios ORDER BY codigo_beneficiario')],'gestoras':[tuple(r) for r in db.execute('SELECT * FROM gestoras ORDER BY codigo_gestora')]}
    return hashlib.sha256(json.dumps(data,default=str).encode()).hexdigest()

def boolean(value):
    if value is None or value=='':return None
    if value is True or str(value).strip().upper() in ('TRUE','VERDADERO','1'):return 1
    if value is False or str(value).strip().upper() in ('FALSE','FALSO','0'):return 0
    raise ValueError('activo debe contener TRUE, FALSE o estar vacío')

def previsualizar(ruta,user,contenido):
    admin(user)
    import openpyxl
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as archive:
            if sum(i.file_size for i in archive.infolist())>40_000_000:raise ValueError('El Excel descomprimido es demasiado grande')
        wb=openpyxl.load_workbook(io.BytesIO(contenido),read_only=True,data_only=False)
    except (zipfile.BadZipFile,KeyError):raise ValueError('Carga un archivo Excel .xlsx válido')
    sh=wb['Beneficiarios'] if 'Beneficiarios' in wb.sheetnames else wb.worksheets[0]
    stream=sh.iter_rows(values_only=True);headers=[str(v or '').strip() for v in next(stream,[])]
    if not {'documento','tipo_documento','nombre_completo','codigo_gestora','activo'}.issubset(headers):raise ValueError('Usa la plantilla del maestro de la app; faltan columnas obligatorias')
    if len(headers)!=len(set(headers)):raise ValueError('Hay encabezados duplicados')
    with conexion(ruta) as db:
        db.execute('BEGIN')
        allowed={r['name'] for r in db.execute('PRAGMA table_info(beneficiarios)')}
        existing_managers={r['codigo_gestora']:dict(r) for r in db.execute('SELECT * FROM gestoras')}
        managers={r['codigo_gestora'] for r in existing_managers.values() if r['activo']}
        manager_changes=[];manager_errors=[]
        if 'Gestoras' in wb.sheetnames:
            gs=wb['Gestoras'].iter_rows(values_only=True);gh=[str(v or '').strip() for v in next(gs,[])];manager_seen=set()
            if not {'codigo_gestora','nombre'}.issubset(gh):manager_errors.append('La hoja Gestoras necesita codigo_gestora y nombre')
            else:
                for line,vals in enumerate(gs,2):
                    if not any(v is not None for v in vals):continue
                    if line>1002:raise ValueError('Máximo 1000 gestoras por importación')
                    r=dict(zip(gh,vals));code=str(r.get('codigo_gestora') or '').strip();name=str(r.get('nombre') or '').strip()
                    if not re.fullmatch(r'GES-\d{4}',code) or not name or code in manager_seen:manager_errors.append(f'Gestoras, fila {line}: código o nombre inválido o duplicado');continue
                    manager_seen.add(code)
                    if code in existing_managers:
                        if existing_managers[code]['nombre'].strip().casefold()!=name.casefold():manager_errors.append(f'Gestoras, fila {line}: el nombre no coincide con el código existente {code}')
                    else:
                        try:active=boolean(r.get('activo',True))
                        except ValueError:manager_errors.append(f'Gestoras, fila {line}: activo inválido');continue
                        manager_changes.append({'codigo_gestora':code,'nombre':name,'activo':1 if active is None else active})
                        if active is not False and active!=0:managers.add(code)
        bycode={r['codigo_beneficiario']:dict(r) for r in db.execute('SELECT * FROM beneficiarios')}
        bydoc={(r['tipo_documento'],r['documento']):r for r in bycode.values()}
        seen=set();codes=set();changes=[];errors=list(manager_errors);warnings=[]
        n=max([int(c.split('-')[1]) for c in bycode] or [0])
        for line,values in enumerate(stream,2):
            if line>5002:raise ValueError('Máximo 5000 filas por importación')
            if not any(v is not None for v in values):continue
            try:
                row={k:(v.isoformat()[:10] if isinstance(v,(datetime.date,datetime.datetime)) else str(v).strip() if v is not None else None) for k,v in zip(headers,values) if k in allowed}
                if any(isinstance(v,str) and v.startswith('=') for v in values):raise ValueError('Reemplaza las fórmulas por valores antes de importar')
                for k in ['documento','tipo_documento','nombre_completo','codigo_gestora']:
                    if not row.get(k):raise ValueError(f'Completa {k}')
                row['tipo_documento']=row['tipo_documento'].upper()
                key=(row['tipo_documento'],row['documento'])
                if key in seen:raise ValueError('Documento duplicado en el archivo')
                seen.add(key)
                if row['codigo_gestora'] not in managers:raise ValueError('Código de gestora inexistente o inactivo')
                code=row.get('codigo_beneficiario');old=bycode.get(code) if code else bydoc.get(key)
                if code and not old:
                    if bycode:raise ValueError('Para una persona nueva deja codigo_beneficiario vacío; la app lo asigna')
                    if not re.fullmatch(r'BEN-\d{6}',code):raise ValueError('Código inicial inválido')
                    n=max(n,int(code.split('-')[1]))
                if code in codes:raise ValueError('Código repetido en el archivo')
                if key in bydoc and (not old or bydoc[key]['codigo_beneficiario']!=old['codigo_beneficiario']):raise ValueError('Documento asignado a otro beneficiario')
                if old:code=old['codigo_beneficiario']
                elif not code:n+=1;code=f'BEN-{n:06d}'
                codes.add(code)
                row['codigo_beneficiario']=code;row['qr_contenido']='PLAN-CASO:'+code
                value=boolean(dict(zip(headers,values)).get('activo'))
                if value is None and old and old['activo'] is not None:
                    value=old['activo'];warnings.append(f'Fila {line}: activo vacío; se conserva el estado confirmado.')
                row['activo']=value
                row['estado_validacion']='PENDIENTE_CONFIRMAR_ACTIVO' if value is None else 'ESTADO_CONFIRMADO'
                if value==0 and (not row.get('fecha_retiro') or not row.get('motivo_retiro')):raise ValueError('Un retiro requiere fecha_retiro y motivo_retiro')
                for k,v in row.items():
                    if k.startswith('fecha_') and v:
                        try:datetime.date.fromisoformat(v)
                        except ValueError:
                            if k in ('fecha_retiro','fecha_nacimiento'):raise ValueError(f'Fecha inválida en {k}')
                            warnings.append(f'Fila {line}: {k} contiene texto o una fecha inválida. Se conserva como referencia; completa una fecha válida al elaborar el informe.')
                if value==1:row['fecha_retiro']=None;row['motivo_retiro']=None
                different=[k for k,v in row.items() if not old or (str(old.get(k)) if old.get(k) is not None else None)!=(str(v) if v is not None else None)]
                if different:changes.append({'codigo':code,'tipo':'alta' if not old else 'actualizacion','campos':different,'row':row,'anterior':old['activo'] if old else None})
            except ValueError as e:errors.append(f'Fila {line}: {e}')
        wb.close()
        for tipo,messages in [('error',errors),('advertencia',warnings)]:
            for message in messages:
                line=re.search(r'Fila (\d+):',message);field=re.search(r'(fecha_\w+)',message)
                registrar(db,user['id'],message,fuente='maestro.xlsx / Beneficiarios',tipo=tipo,fila=int(line.group(1)) if line else None,campo=field.group(1) if field else None)
        token=secrets.token_urlsafe(24)
        payload={'fingerprint':fingerprint(db),'cambios':changes,'gestoras_nuevas':manager_changes}
        if not errors:
            db.execute('DELETE FROM importaciones WHERE vence<?',(int(time.time()),))
            db.execute('INSERT INTO importaciones VALUES(?,?,?,?)',(token,user['id'],json.dumps(payload),int(time.time())+1800))
        return {'token':token if not errors else None,'altas':sum(c['tipo']=='alta' for c in changes),'actualizaciones':sum(c['tipo']=='actualizacion' for c in changes),'gestoras_nuevas':manager_changes,'errores':errors,'avisos':warnings,'cambios':[{k:c[k] for k in ['codigo','tipo','campos']} for c in changes]}

def aplicar(ruta,user,token):
    admin(user)
    with conexion(ruta) as db:
        db.execute('BEGIN IMMEDIATE')
        record=db.execute('SELECT * FROM importaciones WHERE id=? AND usuario_id=? AND vence>?',(token,user['id'],int(time.time()))).fetchone()
        if not record:raise ValueError('La revisión expiró; vuelve a cargar el maestro')
        p=json.loads(record['contenido'])
        if p['fingerprint']!=fingerprint(db):raise ValueError('El maestro cambió desde la revisión; vuelve a cargarlo')
        for g in p.get('gestoras_nuevas',[]):db.execute('INSERT INTO gestoras(codigo_gestora,nombre,activo) VALUES(?,?,?)',(g['codigo_gestora'],g['nombre'],g['activo']))
        for c in p['cambios']:
            row=c['row'];keys=list(row)
            if c['tipo']=='alta':db.execute('INSERT INTO beneficiarios('+','.join(keys)+') VALUES('+','.join('?' for k in keys)+')',[row[k] for k in keys])
            else:db.execute('UPDATE beneficiarios SET '+','.join(k+'=?' for k in keys if k!='codigo_beneficiario')+' WHERE codigo_beneficiario=?',[row[k] for k in keys if k!='codigo_beneficiario']+[c['codigo']])
            if {'direccion_residencia','municipio_residencia'} & set(c['campos']):
                current=db.execute('SELECT direccion_residencia,municipio_residencia FROM beneficiarios WHERE codigo_beneficiario=?',(c['codigo'],)).fetchone()
                if current['direccion_residencia'] and current['municipio_residencia']:db.execute("INSERT INTO residencias(codigo_beneficiario,direccion,municipio,procedencia,fecha_captura,confirmado_por) VALUES(?,?,?,'MAESTRO_CONFIRMADO',CURRENT_TIMESTAMP,?)",(c['codigo'],current['direccion_residencia'],current['municipio_residencia'],user['id']))
            if row['activo']!=c['anterior']:
                db.execute('INSERT INTO historial_estados(codigo_beneficiario,activo_anterior,activo_nuevo,fecha,motivo) VALUES(?,?,?,?,?)',(c['codigo'],c['anterior'],row['activo'],row.get('fecha_retiro') or datetime.date.today().isoformat(),row.get('motivo_retiro') or 'Actualización confirmada del maestro'))
        db.execute('DELETE FROM importaciones WHERE id=?',(token,))
        db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'importar_maestro',str(len(p['cambios']))))
        return len(p['cambios'])
