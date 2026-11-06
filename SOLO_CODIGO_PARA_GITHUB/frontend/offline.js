'use strict';
// Almacén local cifrado. Los datos personales nunca se guardan en Cache Storage.
window.OfflineVault=(()=>{
  let key=null,uid=null,dbPromise;
  const enc=new TextEncoder(),dec=new TextDecoder();
  const rnd=n=>crypto.getRandomValues(new Uint8Array(n));
  function db(){return dbPromise??=(new Promise((resolve,reject)=>{const r=indexedDB.open('plan-caso-privado',1);r.onupgradeneeded=()=>r.result.createObjectStore('records',{keyPath:'id'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)}))}
  async function read(id){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction('records').objectStore('records').get(id);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
  async function write(item){const d=await db();return new Promise((resolve,reject)=>{const t=d.transaction('records','readwrite');t.objectStore('records').put(item);t.oncomplete=resolve;t.onerror=()=>reject(t.error)})}
  async function remove(id){const d=await db();return new Promise((resolve,reject)=>{const t=d.transaction('records','readwrite');t.objectStore('records').delete(id);t.oncomplete=resolve;t.onerror=()=>reject(t.error)})}
  async function all(){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction('records').objectStore('records').getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error)})}
  async function encrypt(k,bytes){const iv=rnd(12);return {iv,cipher:await crypto.subtle.encrypt({name:'AES-GCM',iv},k,bytes)}}
  async function decrypt(k,b){return crypto.subtle.decrypt({name:'AES-GCM',iv:b.iv},k,b.cipher)}
  async function derived(value,salt,deviceKey){let bytes=enc.encode(value);if(deviceKey)bytes=new Uint8Array(await crypto.subtle.sign('HMAC',deviceKey,bytes));const base=await crypto.subtle.importKey('raw',bytes,'PBKDF2',false,['deriveKey']);return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:250000,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['encrypt','decrypt'])}
  async function meta(){const last=await read('last-user');return last?read('meta:'+last.uid):null}
  function userData(user){return {id:user.id,usuario:user.usuario,rol:user.rol,codigo_gestora:user.codigo_gestora,tiene_pin:1,politica_version:user.politica_version,csrf:'offline',bloqueada:false,inactividad_segundos:900}}
  async function activate(user,pin,password){
    if(!crypto.subtle||!indexedDB)throw Error('Este navegador no admite almacenamiento local protegido.');
    let m=await read('meta:'+user.id),raw;
    if(m){const passKey=await derived(password,m.salt);try{raw=await decrypt(passKey,m.passwordBlob)}catch{throw Error('La contraseña no abre los datos locales existentes. No se borraron.')};m.pinBlob=await encrypt(await derived(pin,m.salt,m.deviceKey),raw);m.user=userData(user)}
    else{raw=rnd(32);const salt=rnd(16),deviceKey=await crypto.subtle.generateKey({name:'HMAC',hash:'SHA-256'},false,['sign']);m={id:'meta:'+user.id,user:userData(user),salt,deviceKey,pinBlob:await encrypt(await derived(pin,salt,deviceKey),raw),passwordBlob:await encrypt(await derived(password,salt),raw),pinFailures:0,passwordFailures:0}}
    key=await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['encrypt','decrypt']);uid=user.id;
    await write(m);await write({id:'last-user',uid});return m.user;
  }
  async function unlock(pin,password,newPin){
    const m=await meta();if(!m)throw Error('Prepara el trabajo sin conexión mientras tienes señal.');
    const kind=password!==undefined?'password':'pin',count=kind+'Failures',until=kind+'Until';
    if(m[until]>Date.now())throw Error('Demasiados intentos. Espera 15 minutos.');
    let raw;
    try{raw=await decrypt(await derived(password!==undefined?password:pin,m.salt,password!==undefined?null:m.deviceKey),password!==undefined?m.passwordBlob:m.pinBlob)}
    catch{m[count]=(m[count]||0)+1;if(m[count]>=5){m[until]=Date.now()+900000;m[count]=0}await write(m);throw Error(password!==undefined?'Contraseña incorrecta. Los datos locales se conservaron.':'PIN incorrecto')}
    if(newPin){if(!/^[0-9]{4}$/.test(newPin))throw Error('El PIN debe tener cuatro dígitos');m.pinBlob=await encrypt(await derived(newPin,m.salt,m.deviceKey),raw);m.pinFailures=0;m.pinUntil=0}
    m[count]=0;m[until]=0;await write(m);uid=m.user.id;key=await crypto.subtle.importKey('raw',raw,'AES-GCM',false,['encrypt','decrypt']);return {...m.user,bloqueada:false,pin_local:!!newPin};
  }
  function requireKey(){if(!key||uid===null)throw Error('Desbloquea el almacenamiento local con tu PIN.')}
  async function put(name,value){requireKey();const blob=await encrypt(key,enc.encode(JSON.stringify(value)));await write({id:'data:'+uid+':'+name,...blob})}
  async function get(name,fallback=null){requireKey();const r=await read('data:'+uid+':'+name);return r?JSON.parse(dec.decode(await decrypt(key,r))):fallback}
  async function entries(prefix){requireKey();const start='data:'+uid+':'+prefix,result=[];for(const r of await all())if(r.id.startsWith(start))result.push({key:r.id.slice(('data:'+uid+':').length),value:JSON.parse(dec.decode(await decrypt(key,r)))});return result}
  async function queue(kind,path,method,body){const id=body.solicitud_id||crypto.randomUUID();body.solicitud_id=id;await put('queue:'+id,{id,kind,path,method,body,updated:Date.now()});return 'local-'+id}
  async function listCases(){const rows=await get('cases',[]);return rows}
  async function detail(code){const b=await get('case:'+code);if(!b)throw Error('Este caso no está preparado para trabajar sin conexión.');b.seguimientos??=[];for(const q of await entries('queue:'))if(q.value.kind==='seguimiento'&&q.value.body.codigo_beneficiario===code)b.seguimientos.unshift({id:'local-'+q.value.id,fecha_elaboracion:q.value.body.fecha_elaboracion,nivel_riesgo:q.value.body.nivel_riesgo,estado:q.value.body.estado});return b}
  async function report(id){const q=await get('queue:'+id.replace(/^local-/,''));if(!q)throw Error('Este registro ya se sincronizó. Actualiza la ficha.');const b=await get('case:'+q.body.codigo_beneficiario);return {...q.body,id,generales:{...b,...q.body.generales},sin_conexion:true}}
  function jsonResponse(value){return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json; charset=utf-8','X-Offline':'1'}})}
  function validateRecord(p,b){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(p.fecha_elaboracion||''))throw Error('Completa una fecha de elaboración válida.');
    const today=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Bogota',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());if(p.fecha_elaboracion>today)throw Error('La fecha del seguimiento no puede ser futura.');
    for(const table of ['avances','nuevas_atenciones','participantes'])if(!Array.isArray(p[table])||p[table].length>100)throw Error('Revisa las filas del formulario.');
    for(const a of p.nuevas_atenciones)if(![1,2,3].includes(a.prioridad)||!['I','F','S'].includes(a.nivel_atencion))throw Error('Revisa GP y NAT.');
    if(p.estado!=='finalizado')return;
    const g={...b,...p.generales};for(const k of ['regional','centro_zonal','operador','modalidad','escolaridad','autoridad_competente','fecha_apertura_pard','fecha_ingreso_modalidad'])if(!g[k])throw Error('Completa la información general antes de finalizar.');
    if(!['Alto/Emergencia','Media','Bajo'].includes(p.nivel_riesgo)||!p.conclusiones?.trim()||!p.avances.length||!p.participantes.length)throw Error('Para finalizar completa riesgo, avances, conclusiones y participantes.');
    for(const a of p.avances)if(!['situacion_abordada','atencion_realizada','avance_cuantitativo','avance_cualitativo'].every(k=>String(a[k]||'').trim())||typeof a.requiere_atenciones_adicionales!=='boolean')throw Error('Completa cada avance y la necesidad de atención adicional.');
    for(const a of p.nuevas_atenciones)if(!['situacion','resultado_esperado','atencion_necesaria','responsable','fecha_limite'].every(k=>String(a[k]||'').trim()))throw Error('Completa cada nueva atención.');
    for(const a of p.participantes)if(!['rol_en_caso','nombre','profesion'].every(k=>String(a[k]||'').trim()))throw Error('Completa los participantes.');
  }
  async function route(path,options={}){
    const method=options.method||'GET';let p=typeof options.body==='string'?JSON.parse(options.body||'{}'):options.body||{};
    if(path==='/me'){const m=await meta();if(!m)throw Error('Sin conexión. El dispositivo todavía no fue preparado.');return jsonResponse({...m.user,bloqueada:true,csrf:'offline'})}
    if(path==='/pin/desbloquear')return jsonResponse(await unlock(p.pin));
    if(path==='/pin/recuperar')return jsonResponse(await unlock(undefined,p.password,p.pin));
    if(path==='/bloquear'){key=null;return jsonResponse({bloqueada:true})}
    if(path==='/logout'){key=null;return jsonResponse({ok:true})}
    if(path==='/actividad')return jsonResponse({ok:true});
    requireKey();
    if(path==='/politica')return jsonResponse(await get('policy'));
    if(path==='/politica/aceptar')throw Error('La aceptación de una nueva política requiere conexión.');
    if(path==='/beneficiarios')return jsonResponse(await listCases());
    if(path==='/gestoras')return jsonResponse(await get('managers',[]));
    let m=path.match(/^\/beneficiarios\/(BEN-\d{6})(?:\/(seguimientos|residencia))?$/);
    if(m&&method==='GET')return jsonResponse(await detail(m[1]));
    if(m&&method==='POST'){
      const b=await detail(m[1]);if(b.activo===0)throw Error('El caso está retirado en el maestro descargado.');
      if(m[2]==='seguimientos'){validateRecord(p,b);p.codigo_beneficiario=m[1];const id=await queue('seguimiento',path,method,p);return jsonResponse({id,sin_conexion:true})}
      if(m[2]==='residencia'){if(!p.direccion?.trim()||!p.municipio?.trim()||p.confirmado!==true)throw Error('Completa dirección, municipio y confirmación de residencia.');const id=await queue('residencia',path,method,p);b.direccion_residencia=p.direccion;b.municipio_residencia=p.municipio;b.residencia={...p,procedencia:'PENDIENTE_SINCRONIZAR',fecha_captura:new Date().toISOString()};await put('case:'+m[1],b);return jsonResponse({id,sin_conexion:true})}
    }
    m=path.match(/^\/seguimientos\/(local-[a-f0-9-]+)$/);
    if(m&&method==='GET')return jsonResponse(await report(m[1]));
    if(m&&method==='PUT'){const id=m[1].replace('local-',''),q=await get('queue:'+id);if(!q)throw Error('Registro local no encontrado');p.codigo_beneficiario=q.body.codigo_beneficiario;p.solicitud_id=id;validateRecord(p,await detail(p.codigo_beneficiario));await queue('seguimiento',q.path,'POST',p);return jsonResponse({id:m[1],sin_conexion:true})}
    if(path==='/documentos'&&method==='GET'){const rows=await get('documents',[]);for(const q of await entries('queue:'))if(q.value.kind==='pdf')rows.unshift({id:'local-'+q.value.id,nombre:q.value.body.nombre,estado:'pendiente_conexion',mensaje:'PDF protegido en el dispositivo. El agente Python lo leerá cuando vuelva la conexión.',creado:new Date(q.value.updated).toISOString()});return jsonResponse(rows)}
    if(path==='/documentos/cargar'&&options.body instanceof Blob){const bytes=new Uint8Array(await options.body.arrayBuffer());if(bytes.length>8000000)throw Error('Máximo 8 MB por PDF');let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const digest=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('');for(const q of await entries('queue:'))if(q.value.kind==='pdf'&&q.value.body.hash===digest)return jsonResponse({id:'local-'+q.value.id,duplicado:true,estado:'pendiente_conexion'});const id=await queue('pdf',path,'POST',{nombre:decodeURIComponent(options.headers?.['X-File-Name']||'documento.pdf'),base64:btoa(binary),hash:digest});return jsonResponse({id,duplicado:false,estado:'pendiente_conexion'})}
    throw Error('Esta operación necesita conexión. Los registros pendientes se conservan en el dispositivo.');
  }
  async function synchronize(csrf){
    requireKey();let count=0;
    for(const entry of await entries('queue:')){const q=entry.value,headers={'X-CSRF-Token':csrf};let body,path=q.path;if(q.kind==='pdf'){body=Uint8Array.from(atob(q.body.base64),c=>c.charCodeAt(0));headers['X-File-Name']=encodeURIComponent(q.body.nombre)}else{headers['Content-Type']='application/json';body=JSON.stringify(q.body)}const response=await fetch('/api'+path,{method:q.method,headers,body,credentials:'same-origin',cache:'no-store'});const result=await response.json();if(!response.ok)throw Error(result.error||'No se pudo sincronizar');const current=await get(entry.key);if(current?.updated===q.updated)await remove('data:'+uid+':'+entry.key);count++}
    return count;
  }
  return {activate,unlock,put,get,entries,route,synchronize,meta,ready:()=>!!key,lock:()=>{key=null},count:async()=>key?(await entries('queue:')).length:0};
})();
