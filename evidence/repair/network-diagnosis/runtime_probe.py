import json,socket,ssl,sys,time
from pathlib import Path
host='api.deepseek.com'
results=[]
for name in ['default','small-tls12']:
    ctx=ssl.create_default_context()
    if name=='small-tls12':
        ctx.maximum_version=ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers('ECDHE-RSA-AES128-GCM-SHA256')
    item={'kind':name,'python':sys.version.split()[0],'ssl':ssl.OPENSSL_VERSION,'addresses':socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
    try:
        with ctx.wrap_socket(socket.create_connection((host,443),timeout=10),server_hostname=host) as sock:
            sock.sendall(b'GET /models HTTP/1.1\r\nHost: api.deepseek.com\r\nConnection: close\r\n\r\n')
            item.update(status=sock.recv(1024).split(b'\r\n')[0].decode(),tls=sock.version(),cipher=sock.cipher())
    except Exception as exc: item['error']=repr(exc)
    results.append(item)
print(json.dumps(results,indent=2))
