"""Servidor local y administración de cuentas. Producción: Gunicorn."""
import argparse,getpass,os,shutil
from pathlib import Path
from app_plan_caso.setup import inicializar
from app_plan_caso.security import crear_usuario

def main():
    p=argparse.ArgumentParser(description='Plan de Caso')
    p.add_argument('--db',default=os.environ.get('APP_DB','data/plan_caso.sqlite'))
    p.add_argument('--seed-db',help='Copiar una BD inicial privada solo si no existe la de destino')
    p.add_argument('--crear-usuario')
    p.add_argument('--rol',default='admin',choices=['admin','gestora'])
    p.add_argument('--gestora')
    p.add_argument('--port',type=int,default=int(os.environ.get('PORT','8000')))
    p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--init-only',action='store_true')
    a=p.parse_args();os.environ['APP_DB']=a.db
    if a.seed_db:
        if Path(a.db).exists(): p.error('La BD de destino ya existe. No se sobrescribirá.')
        Path(a.db).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(a.seed_db,a.db)
    inicializar(a.db)
    if a.crear_usuario:
        password=getpass.getpass('Contraseña (mínimo 12 caracteres): ')
        if password!=getpass.getpass('Repítela: '): p.error('Las contraseñas no coinciden')
        crear_usuario(a.db,a.crear_usuario,password,a.rol,a.gestora);print('Usuario creado.');return
    if a.init_only: print('Base de datos inicializada.');return
    from wsgiref.simple_server import make_server
    from app_plan_caso.web import application
    print(f'Plan de Caso: http://{a.host}:{a.port}',flush=True)
    with make_server(a.host,a.port,application) as server: server.serve_forever()

if __name__=='__main__': main()
