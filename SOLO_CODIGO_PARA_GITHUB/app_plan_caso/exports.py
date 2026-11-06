"""Adaptadores de documentos. La exportación no modifica el seguimiento."""
import csv,datetime,io,json
from xml.sax.saxutils import escape

def seguro(v):
    # Evita que textos cargados se ejecuten como fórmulas al abrir un CSV/Excel.
    return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v

def excel(sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.worksheet.table import Table,TableStyleInfo
    wb=Workbook();wb.remove(wb.active)
    for index,(name,rows) in enumerate(sheets.items(),1):
        sh=wb.create_sheet(name[:31]);sh.sheet_view.showGridLines=False
        if not rows:rows=[['Sin registros']]
        for row in rows:sh.append([seguro(v) if not isinstance(v,(dict,list)) else json.dumps(v,ensure_ascii=False) for v in row])
        for cell in sh[1]:cell.font=Font(name='Arial',bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='25465A');cell.alignment=Alignment(wrap_text=True,vertical='center')
        sh.row_dimensions[1].height=40
        for col in sh.columns:sh.column_dimensions[col[0].column_letter].width=min(55,max(20,len(str(col[0].value))+2))
        for row in sh.iter_rows(min_row=2):
            for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
        sh.freeze_panes='A2'
        if sh.max_row>1:
            table=Table(displayName=f'Tabla{index}',ref=sh.dimensions);table.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=True);sh.add_table(table)
    target=io.BytesIO();wb.save(target);return target.getvalue()

def tabla(rows):
    if not rows:return [['Sin registros']]
    keys=list(rows[0]);return [keys]+[[r.get(k) for k in keys] for r in rows]

def maestro_excel(records,managers,failures=None,residences=None):
    values=[{**r,'activo':None if r['activo'] is None else bool(r['activo'])} for r in records]
    empty=[['id','fecha','codigo_beneficiario','fila_origen','campo','fuente','tipo','descripcion','estado','nota_resolucion','fecha_resolucion']]
    return excel({'Beneficiarios':tabla(values),'Gestoras':tabla(managers),'Fallos documentales':tabla(failures) if failures else empty,'Residencias':tabla(residences) if residences else [['codigo_beneficiario','direccion','municipio','latitud','longitud','precision_m','fecha_captura']]})

def generar_informe(s,formato):
    general=s['generales'];base={k:v for k,v in s.items() if k not in ['generales','avances','nuevas_atenciones','participantes']}
    if formato=='xlsx':
        return excel({'Información general':[['campo','valor']]+[[k,v] for k,v in general.items()],'Seguimiento':tabla([base]),'Avances':tabla(s['avances']),'Nuevas atenciones':tabla(s['nuevas_atenciones']),'Participantes':tabla(s['participantes'])})
    if formato=='csv':
        out=io.StringIO(newline='');writer=csv.writer(out);writer.writerow(['seguimiento_id','codigo_beneficiario','seccion','fila','campo','valor'])
        groups={'informacion_general':[general],'seguimiento':[base],'avances':s['avances'],'nuevas_atenciones':s['nuevas_atenciones'],'participantes':s['participantes']}
        for name,records in groups.items():
            for number,row in enumerate(records,1):
                for key,value in row.items():writer.writerow([s['id'],s['codigo_beneficiario'],name,number,key,seguro(value)])
        return out.getvalue().encode('utf-8-sig')
    if formato!='pdf':raise ValueError('Formato no permitido')
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    out=io.BytesIO();styles=getSampleStyleSheet();normal=styles['BodyText'];normal.fontSize=9;normal.leading=13
    def para(value,style=normal):return Paragraph(escape(str(value if value is not None else 'Pendiente')).replace('\n','<br/>'),style)
    story=[para('SEGUIMIENTO AL PLAN DE CASO',styles['Title']),para('Información reservada · '+s['codigo_beneficiario']),para('Estado: '+s['estado'].upper()),para('Incluye un adjunto estructurado de este informe para su revisión e importación en la app.'),Spacer(1,12)]
    def heading(label):story.extend([Spacer(1,10),para(label,styles['Heading2'])])
    heading('1. Información general')
    labels={'nombre_completo':'Nombre completo','documento':'Documento','tipo_documento':'Tipo de documento','sim':'Número SIM','regional':'Regional','centro_zonal':'Centro zonal','operador':'Operador','modalidad':'Modalidad','fecha_nacimiento':'Fecha de nacimiento','escolaridad':'Escolaridad','autoridad_competente':'Autoridad administrativa','fecha_apertura_pard':'Apertura PARD','fecha_ingreso_modalidad':'Ingreso a modalidad'}
    info=[[para(label),para(general.get(key))] for key,label in labels.items()]
    info.extend([[para('Fecha de elaboración'),para(s['fecha_elaboracion'])],[para('Nivel de riesgo'),para(s['nivel_riesgo'])]])
    t=Table(info,colWidths=[145,360],hAlign='LEFT');t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#DCE5E2'))]));story.append(t)
    for k,label in [('motivo_ingreso','Motivo de ingreso'),('concepto_evaluacion_integradora','Concepto de evaluación integradora')]:heading(label);story.append(para(general.get(k)))
    heading('2. Descripción de avances o resultados')
    for i,a in enumerate(s['avances'],1):
        story.append(para(f'Avance {i}',styles['Heading3']))
        for k,label in [('situacion_abordada','Situación abordada'),('atencion_realizada','Atención realizada'),('avance_cuantitativo','Descripción cuantitativa'),('avance_cualitativo','Descripción cualitativa')]:story.extend([para(label,styles['Heading4']),para(a.get(k))])
        story.append(para('Requiere atenciones adicionales: '+('Sí' if a['requiere_atenciones_adicionales']==1 else 'No' if a['requiere_atenciones_adicionales']==0 else 'Pendiente')))
    heading('3. Nuevas situaciones o atenciones')
    if not s['nuevas_atenciones']:story.append(para('Sin nuevas atenciones registradas.'))
    for i,a in enumerate(s['nuevas_atenciones'],1):
        story.append(para(f'Atención {i} · GP {a["prioridad"]} · NAT {a["nivel_atencion"]}',styles['Heading3']))
        for k,label in [('situacion','Situación por abordar'),('resultado_esperado','Resultado esperado'),('atencion_necesaria','Atención necesaria'),('responsable','Responsable'),('fecha_limite','Fecha límite')]:story.extend([para(label,styles['Heading4']),para(a.get(k))])
    heading('4. Conclusiones y recomendaciones');story.append(para(s.get('conclusiones')))
    heading('Percepción del beneficiario, familia o red');story.append(para(s.get('percepcion_beneficiario_red')))
    heading('Participantes')
    for p in s['participantes']:story.extend([para(f'{p["nombre"]} · {p["profesion"]}'),para(p['rol_en_caso']),para('Firma: ______________________________'),Spacer(1,8)])
    heading('Fecha de envío a la autoridad administrativa');story.append(para(s.get('fecha_envio_autoridad')))
    def footer(canvas,doc):
        canvas.saveState();canvas.setFont('Helvetica',8);canvas.drawString(42,28,'Plan de Caso · Información reservada');canvas.drawRightString(A4[0]-42,28,f'Página {doc.page}');canvas.restoreState()
    SimpleDocTemplate(out,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=42,bottomMargin=45).build(story,onFirstPage=footer,onLaterPages=footer)
    from pypdf import PdfReader,PdfWriter
    from .document_format import registro
    writer=PdfWriter();writer.append(PdfReader(io.BytesIO(out.getvalue())))
    writer.add_attachment('plan-caso.json',json.dumps({'formato':'plan-caso/1','registro':registro(s)},ensure_ascii=False).encode('utf8'))
    final=io.BytesIO();writer.write(final);return final.getvalue()

def qr_svg(payload):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing,String
    from reportlab.graphics import renderSVG
    qr=QrCodeWidget(payload,x=0,y=25,barWidth=240,barHeight=240)
    d=Drawing(240,270);d.add(qr)
    d.add(String(120,10,payload.removeprefix('PLAN-CASO:'),fontName='Helvetica',fontSize=12,textAnchor='middle'))
    return renderSVG.drawToString(d).encode('utf8')
