"""Adaptador de consulta local. La interfaz web móvil consumirá estos servicios."""
import argparse,json
from .application import consultar_beneficiario

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('codigo')
    parser.add_argument('--bd',default='outputs/maestro_app/plan_caso.sqlite')
    args=parser.parse_args()
    print(json.dumps(consultar_beneficiario(args.bd,args.codigo),ensure_ascii=True,indent=2))

if __name__=='__main__': main()
