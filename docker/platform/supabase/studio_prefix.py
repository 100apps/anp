"""仅在镜像构建目录修复锁定 Studio 源码中的根路径假设。"""
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
