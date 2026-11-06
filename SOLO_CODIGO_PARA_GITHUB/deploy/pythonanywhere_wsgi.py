"""Copiar al archivo WSGI de PythonAnywhere; el repositorio puede ser público."""
import os,sys
from pathlib import Path

home=Path.home()
project=home/'plan-de-caso'
private=home/'.plan-caso-data'
private.mkdir(exist_ok=True)
sys.path.insert(0,str(project))
os.environ['APP_ENV']='production'
os.environ['APP_DB']=str(private/'plan_caso.sqlite')
os.environ['APP_DOCUMENTS_DIR']=str(private/'documentos')
os.environ['APP_TEMPLATE_PATH']=str(private/'plantilla_plan_caso.xlsx')
# Completar mediante variables privadas del servidor, no publicar datos de contacto sin autorización.
# DATA_CONTROLLER_NAME: entidad responsable del tratamiento.
# DATA_CONTROLLER_CONTACT: canal institucional de atención.
from app_plan_caso.wsgi import application
