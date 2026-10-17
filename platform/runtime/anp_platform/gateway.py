"""生成支持 WebSocket/SSE 的单端口 Nginx 网关。"""
from pathlib import Path
import re


def render(settings):
    port=int(settings.get('port',8080))
    routes=settings['routes']
    upstreams=[]
    locations=[]
    for index,route in enumerate(routes):
        path=route['path']
        if not re.fullmatch(r'/[a-zA-Z0-9_./-]+/',path) or '..' in path:raise ValueError('无效服务路径：'+path)
        target=route['target']
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+:[0-9]+',target):raise ValueError('无效上游地址')
        upstreams.append(f'upstream service_{index} {{ server {target}; keepalive 32; }}')
        slash='/' if route.get('strip_prefix') else ''
        auth='auth_basic "ANP Supabase 管理"; auth_basic_user_file /run/anp/studio.htpasswd;' if route.get('basic_auth') else ''
        locations.append(f'location = {path.rstrip("/")} {{ return 308 {path}; }}\n'
            f'location ^~ {path} {{ {auth} proxy_pass http://service_{index}{slash}; }}')
    return '''worker_processes 2;
pid /tmp/anp-nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 4096; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  map $http_upgrade $connection_upgrade { default upgrade; '' ''; }
  access_log /dev/stdout;
  sendfile on;
  tcp_nopush on;
  keepalive_timeout 65;
  client_max_body_size 0;
  proxy_http_version 1.1;
  proxy_buffering off;
  proxy_request_buffering off;
  proxy_read_timeout 3600s;
  proxy_send_timeout 3600s;
  proxy_set_header Host $http_host;
  proxy_set_header X-Forwarded-Host $http_host;
  proxy_set_header X-Forwarded-Proto $scheme;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection $connection_upgrade;
  gzip on;
  gzip_types text/css application/javascript application/json;
'''+'\n'.join(upstreams)+f'''
  server {{
    listen {port};
    server_name _;
    location = /_platform/health {{ proxy_pass http://127.0.0.1:12000/health; }}
    location = /_platform/services {{ proxy_pass http://127.0.0.1:12000/services; }}
'''+'\n'.join(locations)+'''
    location = / { root /opt/anp/www; try_files /index.html =404; }
    location / { return 404; }
  }
}
'''


def write(settings,path='/run/anp/nginx.conf'):
    destination=Path(path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(render(settings),encoding='utf8')
    return str(destination)
