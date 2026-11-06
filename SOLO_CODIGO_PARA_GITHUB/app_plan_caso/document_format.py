"""Formato de intercambio explícito: únicamente los datos que aparecen en el informe."""
import json
GENERAL=['nombre_completo','nombres','documento','tipo_documento','sim','regional','centro_zonal','operador','modalidad','fecha_nacimiento','escolaridad','autoridad_competente','fecha_apertura_pard','fecha_ingreso_modalidad','motivo_ingreso','concepto_evaluacion_integradora']
TOP=['codigo_beneficiario','fecha_elaboracion','nivel_riesgo','conclusiones','percepcion_beneficiario_red','fecha_envio_autoridad']
ROWS={'avances':['situacion_abordada','atencion_realizada','avance_cuantitativo','avance_cualitativo','requiere_atenciones_adicionales'],'nuevas_atenciones':['situacion','prioridad','nivel_atencion','resultado_esperado','atencion_necesaria','responsable','fecha_limite'],'participantes':['rol_en_caso','nombre','profesion']}

def registro(data):
    result={k:data.get(k) for k in TOP}
    result['generales']={k:data.get('generales',{}).get(k) for k in GENERAL}
    for table,keys in ROWS.items():result[table]=[{k:r.get(k) for k in keys} for r in data.get(table,[])]
    return result

def canon(data):return json.dumps(registro(data),ensure_ascii=False,sort_keys=True)
