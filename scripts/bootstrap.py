"""初始化由工具镜像管理；此入口只检查状态，不覆盖数据库角色凭据。"""
from manage import compose
compose('exec','-T','toolbox','/usr/bin/python3.13','/opt/anp3/runtime/supervisor.py','status')
