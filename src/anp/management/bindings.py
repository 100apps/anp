"""将远端资源绑定到组件，不修改上游源码或生成文件。"""
import re


def bind(document,settings,secrets):
    config=settings.get('deployment',{})
    def expand(value):
        if isinstance(value,str):
            return re.sub(r'\$\{([A-Z0-9_]+)\}',lambda m:secrets[m[1]],value)
        return value
    services=document['services']
    for service,environment in config.get('environment',{}).items():
        if service not in services: raise ValueError('未知组件绑定：'+service)
        services[service].setdefault('environment',{}).update({k:expand(v) for k,v in environment.items()})
    external=set(config.get('external_services',[]))
    unknown=external-set(services)
    if unknown: raise ValueError('未知外部组件：'+','.join(sorted(unknown)))
    for service in external: services.pop(service)
    for service in services.values():
        for dependency in external:
            service.get('depends_on',{}).pop(dependency,None)
    # DBX 使用相同云地址，密码继续在私有配置中展开。
    return [{k:expand(v) for k,v in item.items()} for item in config.get('dbx_connections',[])]
