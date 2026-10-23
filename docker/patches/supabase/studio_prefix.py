"""仅在镜像构建目录适配锁定 Studio 的统一入口和构建资源。"""
from pathlib import Path


def replace(relative, old, new, count):
    path = Path("apps/studio") / relative
    text = path.read_text()
    assert text.count(old) == count, f"上游已变更，请重新审核目录补丁：{relative}"
    path.write_text(text.replace(old, new))


replace("lib/constants/index.ts", "return '/api'",
        "return (process.env.NEXT_PUBLIC_BASE_PATH ?? '') + '/api'", 2)
replace("lib/constants/api.ts", "${PUBLIC_URL.origin}/rest/v1/",
        "${PUBLIC_URL.origin}${PUBLIC_URL.pathname.replace(/\\/$/, '')}/rest/v1/", 1)
replace("lib/constants/api.ts", "PROJECT_ENDPOINT = PUBLIC_URL.host",
        "PROJECT_ENDPOINT = PUBLIC_URL.host + PUBLIC_URL.pathname.replace(/\\/$/, '')", 1)
for name, variable in (("public-url", "publicUrl"), ("sign", "signedUrl"), ("sign-multi", "signedUrl")):
    replace(f"pages/api/platform/storage/[ref]/buckets/[id]/objects/{name}.ts",
            f"{variable}.port = parsed.port",
            f"{variable}.port = parsed.port\n  {variable}.pathname = parsed.pathname.replace(/\\/$/, '') + {variable}.pathname", 1)

# Next 16.2 默认 os.cpus().length-1，忽略 Docker CPU 配额，32 核宿主会启动31个页面 worker。
# 显式限制页面生成并发；不关闭编译、页面生成或既有检查。
replace("next.config.ts", "clientRouterFilter: false,", "clientRouterFilter: false,\n    cpus: 2,\n    turbopackMemoryLimit: 6 * 1024 ** 3,", 1)
