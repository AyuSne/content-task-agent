import concurrent.futures,json,os,socket,ssl,httpx,time
from pathlib import Path
for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
    os.environ.pop(name,None)

def probe(kind):
    t=time.monotonic()
    result={'kind':kind}
    try:
        ctx=ssl.create_default_context()
        if kind.endswith('tls12'): ctx.maximum_version=ssl.TLSVersion.TLSv1_2
        if 'cipher' in kind: ctx.set_ciphers('DEFAULT')
        if 'alpn' in kind: ctx.set_alpn_protocols(['http/1.1'])
        if kind.startswith('raw'):
            if 'ipv4' in kind:
                ip=socket.getaddrinfo('api.deepseek.com',443,socket.AF_INET)[0][4]
                sock=socket.create_connection(ip,timeout=12)
            else: sock=socket.create_connection(('api.deepseek.com',443),timeout=12)
            with ctx.wrap_socket(sock,server_hostname='api.deepseek.com') as sock:
                sock.sendall(b'GET /models HTTP/1.1\r\nHost: api.deepseek.com\r\nConnection: close\r\n\r\n')
                result.update(status=sock.recv(1024).split(b'\r\n')[0].decode(),tls=sock.version(),cipher=sock.cipher())
        else:
            url='https://pypi.tuna.tsinghua.edu.cn/simple' if kind=='other-host' else 'https://api.deepseek.com/models'
            with httpx.Client(trust_env=False,verify=ctx,timeout=12) as c:
                r=c.get(url)
                result.update(status=r.status_code)
    except Exception as e:
        result.update(error=repr(e),cause=repr(e.__cause__))
    result['seconds']=round(time.monotonic()-t,3)
    return result
kinds=['httpx','raw','raw-alpn','raw-ipv4','httpx-cipher-tls12','raw-cipher-tls12','other-host']
with concurrent.futures.ThreadPoolExecutor(len(kinds)) as pool:
    results=list(pool.map(probe,kinds))
Path('.scratch/network-diagnosis/probes.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results,indent=2))
