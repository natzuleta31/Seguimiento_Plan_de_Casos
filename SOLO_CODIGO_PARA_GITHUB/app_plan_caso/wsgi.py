import os
from .setup import inicializar
inicializar(os.environ.get('APP_DB','data/plan_caso.sqlite'))
from .web import application
