"""Reglas del dominio, independientes de interfaz y almacenamiento."""
import re

def validar_codigo(codigo):
    if not re.fullmatch(r'BEN-\d{6}', codigo):
        raise ValueError('Código de beneficiario inválido')
    return codigo

def validar_activo(valor):
    if valor is not None and type(valor) is not bool:
        raise ValueError('activo debe ser TRUE, FALSE o vacío (pendiente)')
    return None if valor is None else int(valor)
