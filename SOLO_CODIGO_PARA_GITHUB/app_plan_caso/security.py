"""Contraseñas derivadas, sesiones revocables y autorización por gestora."""
import hashlib,hmac,re,secrets,time
from .infrastructure import conexion

class AccesoDenegado(Exception): pass

def hash_password(password):
    if len(password)<12: raise ValueError('La contraseña debe tener al menos 12 caracteres')
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),600000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'

def verificar_password(password,stored):
    _,rounds,salt,digest=stored.split('$')
    value=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),int(rounds)).hex()
    return hmac.compare_digest(value,digest)

def crear_usuario(ruta,usuario,password,rol='admin',gestora=None):
    if rol not in ('admin','gestora'): raise ValueError('Rol inválido')
    if rol=='gestora' and not gestora: raise ValueError('Asigna una gestora al usuario')
    with conexion(ruta) as db:
        db.execute('INSERT INTO usuarios(usuario,password_hash,rol,codigo_gestora) VALUES(?,?,?,?)',(usuario.strip(),hash_password(password),rol,gestora))

def login(ruta,usuario,password,ip):
    now=int(time.time());key=hashlib.sha256((ip+'|'+usuario.casefold()).encode()).hexdigest()
    with conexion(ruta) as db:
        attempt=db.execute('SELECT * FROM intentos_login WHERE clave=?',(key,)).fetchone()
        if attempt and attempt['inicio']>now-900 and attempt['intentos']>=8:
            raise AccesoDenegado('Demasiados intentos. Espera 15 minutos.')
        user=db.execute('SELECT * FROM usuarios WHERE usuario=? AND activo=1',(usuario,)).fetchone()
        # Trabajo comparable también cuando la cuenta no existe.
        valid=verificar_password(password,user['password_hash']) if user else bool(hashlib.pbkdf2_hmac('sha256',password.encode(),b'no-user-salt-000',600000) and False)
        if not valid:
            if not attempt or attempt['inicio']<=now-900:
                db.execute('INSERT OR REPLACE INTO intentos_login VALUES(?,1,?)',(key,now))
            else: db.execute('UPDATE intentos_login SET intentos=intentos+1 WHERE clave=?',(key,))
        else:
            token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(24)
            db.execute('DELETE FROM sesiones WHERE vence<?',(now,))
            db.execute('INSERT INTO sesiones(token_hash,usuario_id,csrf,vence,actividad,bloqueada) VALUES(?,?,?,?,?,1)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,now+28800,now))
            db.execute('DELETE FROM intentos_login WHERE clave=?',(key,))
            return token,csrf
    raise AccesoDenegado('Usuario o contraseña incorrectos')

def sesion(ruta,token):
    if not token: raise AccesoDenegado('Inicia sesión para continuar')
    with conexion(ruta) as db:
        row=db.execute('SELECT u.id,u.usuario,u.rol,u.codigo_gestora,u.pin_hash IS NOT NULL AS tiene_pin,u.politica_version,s.csrf,s.actividad,s.bloqueada FROM sesiones s JOIN usuarios u ON u.id=s.usuario_id WHERE s.token_hash=? AND s.vence>? AND u.activo=1',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        if not row: raise AccesoDenegado('Tu sesión terminó. Vuelve a ingresar.')
        result=dict(row)
        result['bloqueada']=bool(result['bloqueada'] or int(time.time())-result['actividad']>=900 or not result['tiene_pin'])
        result['inactividad_segundos']=900
        return result

def validar_pin(pin):
    if not isinstance(pin,str) or not re.fullmatch(r'[0-9]{4}',pin):raise ValueError('El PIN debe tener exactamente 4 dígitos')
    return 'pin:'+pin+':credential'

def tocar_sesion(ruta,token,bloquear=False):
    with conexion(ruta) as db:
        if bloquear:db.execute('UPDATE sesiones SET bloqueada=1 WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
        else:db.execute('UPDATE sesiones SET actividad=? WHERE token_hash=?',(int(time.time()),hashlib.sha256(token.encode()).hexdigest()))

def gestionar_pin(ruta,user,token,payload,accion):
    now=int(time.time());key=('pin-recuperar:' if accion=='recuperar' else 'pin:')+str(user['id'])
    if accion in ('configurar','recuperar'):new_hash=hash_password(validar_pin(payload.get('pin')))
    with conexion(ruta) as db:
        db.execute('BEGIN IMMEDIATE')
        attempt=db.execute('SELECT * FROM intentos_login WHERE clave=?',(key,)).fetchone()
        if attempt and attempt['inicio']>now-900 and attempt['intentos']>=5:raise AccesoDenegado('Demasiados intentos. Espera 15 minutos para volver a intentar.')
        account=db.execute('SELECT pin_hash,password_hash FROM usuarios WHERE id=?',(user['id'],)).fetchone()
        if accion=='configurar':
            if account['pin_hash']:raise ValueError('Ya existe un PIN. Usa la recuperación con contraseña para cambiarlo.')
            valid=True
        elif accion=='recuperar':
            password=payload.get('password','')
            if not isinstance(password,str) or len(password)>1024:raise ValueError('Contraseña inválida')
            valid=verificar_password(password,account['password_hash'])
        elif accion=='desbloquear':
            pin=validar_pin(payload.get('pin'))
            valid=bool(account['pin_hash']) and verificar_password(pin,account['pin_hash'])
        else:raise ValueError('Operación de PIN inválida')
        if not valid:
            if not attempt or attempt['inicio']<=now-900:db.execute('INSERT OR REPLACE INTO intentos_login VALUES(?,1,?)',(key,now))
            else:db.execute('UPDATE intentos_login SET intentos=intentos+1 WHERE clave=?',(key,))
        else:
            if accion in ('configurar','recuperar'):
                db.execute('UPDATE usuarios SET pin_hash=? WHERE id=?',(new_hash,user['id']))
                db.execute('INSERT INTO auditoria(usuario_id,accion,referencia) VALUES(?,?,?)',(user['id'],'pin_'+accion,str(user['id'])))
            token_hash=hashlib.sha256(token.encode()).hexdigest()
            if accion=='recuperar':
                db.execute('DELETE FROM sesiones WHERE usuario_id=? AND token_hash<>?',(user['id'],token_hash))
                db.execute('DELETE FROM intentos_login WHERE clave=?',('pin:'+str(user['id']),))
            db.execute('UPDATE sesiones SET actividad=?,bloqueada=0 WHERE token_hash=?',(now,token_hash))
            db.execute('DELETE FROM intentos_login WHERE clave=?',(key,))
            return
    raise AccesoDenegado('PIN incorrecto' if accion=='desbloquear' else 'Contraseña incorrecta. Los datos se conservaron.')

def autorizar(user,benef):
    if not benef: raise ValueError('Beneficiario no encontrado')
    if user['rol']!='admin' and user['codigo_gestora']!=benef['codigo_gestora']:
        raise AccesoDenegado('No tienes acceso a este caso')

def admin(user):
    if user['rol']!='admin': raise AccesoDenegado('Acción reservada a administración')

def autorizar_dispositivo(ruta,user,payload):
    password=payload.get('password','');pin=validar_pin(payload.get('pin'));now=int(time.time());key='device:'+str(user['id'])
    if not isinstance(password,str) or len(password)>1024:raise ValueError('Contraseña inválida')
    with conexion(ruta) as db:
        attempt=db.execute('SELECT * FROM intentos_login WHERE clave=?',(key,)).fetchone()
        if attempt and attempt['inicio']>now-900 and attempt['intentos']>=5:raise AccesoDenegado('Demasiados intentos. Espera 15 minutos.')
        account=db.execute('SELECT password_hash,pin_hash FROM usuarios WHERE id=?',(user['id'],)).fetchone()
        valid=verificar_password(password,account['password_hash']) and bool(account['pin_hash']) and verificar_password(pin,account['pin_hash'])
        if valid:db.execute('DELETE FROM intentos_login WHERE clave=?',(key,));return
        if not attempt or attempt['inicio']<=now-900:db.execute('INSERT OR REPLACE INTO intentos_login VALUES(?,1,?)',(key,now))
        else:db.execute('UPDATE intentos_login SET intentos=intentos+1 WHERE clave=?',(key,))
    raise AccesoDenegado('Contraseña o PIN incorrectos')
