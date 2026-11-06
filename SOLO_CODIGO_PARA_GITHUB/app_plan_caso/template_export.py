"""Plantilla Excel privada: conserva celdas, combinaciones y estilos del formato fuente."""
import datetime,io,os,re,warnings,zipfile
from pathlib import Path
from .infrastructure import conexion
from .security import admin

GENERAL='INF. GENERAL';FOLLOW='SEGUM.PLAN DE CASO'

def template_path(ruta):
    return Path(os.environ.get('APP_TEMPLATE_PATH') or str(Path(ruta).resolve().parent/'plantilla_plan_caso.xlsx'))

def nombre_archivo(s):
    g=s['generales'];name=g.get('nombres') or g.get('nombre_completo') or s['codigo_beneficiario'];doc=g.get('documento') or s['codigo_beneficiario']
    def clean(value):return re.sub(r'[^\w.-]+','_',str(value),flags=re.UNICODE).strip('._')[:100] or 'registro'
    return clean(s['fecha_elaboracion'])+'_'+clean(name)+'_'+clean(doc)

def _book(content):
    import openpyxl
    from openpyxl.cell.cell import MergedCell
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        if sum(i.file_size for i in z.infolist())>40_000_000:raise ValueError('Plantilla demasiado grande')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore');wb=openpyxl.load_workbook(io.BytesIO(content),keep_links=False)
    if not {GENERAL,FOLLOW}.issubset(wb.sheetnames):raise ValueError('La plantilla debe contener INF. GENERAL y SEGUM.PLAN DE CASO')
    for sheet in list(wb):
        if sheet.title not in (GENERAL,FOLLOW,'LISTA'):wb.remove(sheet)
        else:
            for row in sheet:
                for cell in row:
                    if not isinstance(cell,MergedCell):cell.comment=None;cell.hyperlink=None
    def clear(sh,area):
        for row in sh[area]:
            for c in row:
                if not isinstance(c,MergedCell):c.value=None
    for area in ['A6:P6','A8:P10','A12:P17','K19:P21']:clear(wb[GENERAL],area)
    for area in ['A6:P7','A15:P15','A17:P17','A19:P22','G23:P23']:clear(wb[FOLLOW],area)
    wb.properties.title='Formato seguimiento al plan de caso';wb.properties.subject='Plantilla de diligenciamiento';wb.properties.description=''
    return wb

def guardar_plantilla(ruta,user,content):
    admin(user)
    if len(content)>8_000_000:raise ValueError('Máximo 8 MB para la plantilla')
    try:wb=_book(content)
    except (zipfile.BadZipFile,KeyError):raise ValueError('Carga la plantilla original en formato .xlsx')
    target=template_path(ruta);target.parent.mkdir(parents=True,exist_ok=True);wb.save(target);wb.close()
    with conexion(ruta) as db:db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'cargar_plantilla','formato_plan_caso'))

def generar(ruta,s):
    from openpyxl.styles import Alignment,Font,PatternFill
    target=template_path(ruta)
    if not target.exists():raise ValueError('Carga primero la plantilla Excel original desde Maestro para generar el formato institucional')
    wb=_book(target.read_bytes());a=wb[GENERAL];b=wb[FOLLOW];g=s['generales']
    def put(sh,cell,value):
        c=sh[cell]
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):value="'"+value
        c.value=value;c.alignment=Alignment(horizontal=c.alignment.horizontal or 'left',vertical='top',wrap_text=True)
    def day(value):
        if not value:return None
        try:return datetime.date.fromisoformat(value)
        except ValueError:return value
    mapping={'A6':'sim','B6':'regional','C6':'centro_zonal','F6':'operador','J6':'modalidad','A8':'nombres','D8':'tipo_documento','F8':'documento','J8':'escolaridad','K8':'autoridad_competente','N8':'fecha_apertura_pard','P8':'fecha_ingreso_modalidad','A12':'motivo_ingreso','H12':'concepto_evaluacion_integradora'}
    for cell,key in mapping.items():
        value=g.get(key)
        if key=='nombres':value=value or g.get('nombre_completo')
        put(a,cell,day(value) if key.startswith('fecha_') else value)
        if key.startswith('fecha_'):a[cell].number_format='dd/mm/yyyy'
    put(a,'N6',day(s['fecha_elaboracion']));a['N6'].number_format='dd/mm/yyyy'
    if g.get('fecha_nacimiento'):
        birth=day(g['fecha_nacimiento']);at=day(s['fecha_elaboracion'])
        if isinstance(birth,datetime.date) and isinstance(at,datetime.date):
            months=(at.year-birth.year)*12+at.month-birth.month-(at.day<birth.day);put(a,'I8',f'{months//12} años, {months%12} meses')
    risk=s.get('nivel_riesgo');put(a,'N19',risk)
    for cell,value in [('K19','Alto/Emergencia'),('K20','Media'),('K21','Bajo')]:put(a,cell,'Sí' if value==risk else 'No')
    for i,row in enumerate(s['avances'][:2],6):
        for col,key in [('A','situacion_abordada'),('D','atencion_realizada'),('F','avance_cuantitativo'),('I','avance_cualitativo')]:put(b,f'{col}{i}',row.get(key))
        extra=row.get('requiere_atenciones_adicionales');put(b,f'P{i}','Sí' if extra==1 else 'No' if extra==0 else '')
    if s['nuevas_atenciones']:
        row=s['nuevas_atenciones'][0]
        for cell,key in [('A15','situacion'),('D15','prioridad'),('E15','nivel_atencion'),('F15','resultado_esperado'),('I15','atencion_necesaria'),('L15','responsable'),('O15','fecha_limite')]:put(b,cell,day(row.get(key)) if key=='fecha_limite' else row.get(key))
        b['O15'].number_format='dd/mm/yyyy'
    conclusion=s.get('conclusiones') or ''
    if s.get('percepcion_beneficiario_red'):conclusion+='\n\nPercepción del beneficiario, familia o red: '+s['percepcion_beneficiario_red']
    if len(s['avances'])>2 or len(s['nuevas_atenciones'])>1 or len(s['participantes'])>2:conclusion+='\n\nContinuación: ver hojas de Anexos.'
    put(b,'A17',conclusion)
    for i,p in zip([19,21],s['participantes'][:2]):
        for col,key in [('A','rol_en_caso'),('E','nombre'),('K','profesion')]:put(b,f'{col}{i}',p.get(key))
    put(b,'G23',day(s.get('fecha_envio_autoridad')));b['G23'].number_format='dd/mm/yyyy'
    for title,rows in [('Anexos avances',s['avances'][2:]),('Anexos atenciones',s['nuevas_atenciones'][1:]),('Anexos participantes',s['participantes'][2:])]:
        if not rows:continue
        sh=wb.create_sheet(title);keys=[k for k in rows[0] if k not in ('id','seguimiento_id')];sh.append(keys)
        for row in rows:sh.append([row.get(k) for k in keys])
        for c in sh[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='25465A')
        for col in sh.columns:
            sh.column_dimensions[col[0].column_letter].width=40
            for c in col:c.alignment=Alignment(wrap_text=True,vertical='top')
        sh.freeze_panes='A2'
    output=io.BytesIO();wb.save(output);wb.close();return output.getvalue()
