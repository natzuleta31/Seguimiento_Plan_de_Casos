"""Paquete estático para Netlify con proxy a la API Python externa."""
import os,shutil
from pathlib import Path
from urllib.parse import urlsplit

root=Path(__file__).resolve().parent.parent
origin=os.environ.get('API_ORIGIN','').strip().rstrip('/')
url=urlsplit(origin)
if url.scheme!='https' or not url.hostname or url.username or url.password or url.path not in ('','/') or url.query or url.fragment or any(c.isspace() for c in origin):
    raise SystemExit('Configura API_ORIGIN con el origen HTTPS del servidor Python, por ejemplo https://tu-cuenta.pythonanywhere.com')
dest=root/'dist';dest.mkdir(exist_ok=True)
for name in ['index.html','app.js','styles.css','icon.svg','manifest.webmanifest','offline.js','sw.js']:shutil.copy2(root/'frontend'/name,dest/name)
shutil.copytree(root/'mapas',dest/'mapas',dirs_exist_ok=True)
(dest/'_redirects').write_text('/api/* '+origin+'/api/:splat 200!\n',encoding='utf8')
(dest/'_headers').write_text("/*\n  X-Content-Type-Options: nosniff\n  X-Frame-Options: DENY\n  Referrer-Policy: no-referrer\n  Cache-Control: no-store\n  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'\n",encoding='utf8')
print('Interfaz preparada en dist; API proxy configurado.')
