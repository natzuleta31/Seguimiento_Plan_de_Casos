"""Casos de uso compartidos por la futura interfaz móvil y la administración."""
from .domain import validar_codigo, validar_activo
from .infrastructure import conexion, buscar_por_codigo

def consultar_beneficiario(ruta,codigo_o_qr):
    codigo=codigo_o_qr.removeprefix('PLAN-CASO:')
    validar_codigo(codigo)
    with conexion(ruta) as db:
        return buscar_por_codigo(db,codigo)

def cambiar_estado(ruta,codigo,activo,fecha,motivo):
    validar_codigo(codigo)
    estado=validar_activo(activo)
    if not fecha or not motivo.strip():
        raise ValueError('Indica fecha y motivo del cambio')
    with conexion(ruta) as db:
        anterior=buscar_por_codigo(db,codigo)
        if not anterior: raise ValueError('Beneficiario inexistente')
        if anterior['activo']==estado: return
        db.execute('UPDATE beneficiarios SET activo=?,estado_validacion=?,fecha_retiro=?,motivo_retiro=? WHERE codigo_beneficiario=?',(estado,'PENDIENTE_CONFIRMAR_ACTIVO' if estado is None else 'ESTADO_CONFIRMADO',fecha if estado==0 else None,motivo if estado==0 else None,codigo))
        db.execute('INSERT INTO historial_estados(codigo_beneficiario,activo_anterior,activo_nuevo,fecha,motivo) VALUES(?,?,?,?,?)',(codigo,anterior['activo'],estado,fecha,motivo))
