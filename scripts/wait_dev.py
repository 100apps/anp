"""等待本项目开发入口就绪，供CI使用；失败时不把进程启动等同验收成功。"""
import json
import time
import urllib.request

deadline=time.monotonic()+120
while time.monotonic()<deadline:
    try:
        with urllib.request.urlopen('http://127.0.0.1:20260/api/health',timeout=2) as response:
            assert json.load(response)['service']=='anp'
        with urllib.request.urlopen('http://127.0.0.1:51760/workbench/',timeout=2) as response:
            assert response.status==200
        print('ANP前后端就绪');break
    except (OSError,ValueError,AssertionError):time.sleep(1)
else:raise SystemExit('ANP前后端启动超时，请检查var/logs')
