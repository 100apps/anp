"""一次运行一个轻量进程；不引入独立队列服务。"""
import sys
from uuid import UUID

from anp_business.dubbing import service


def main():
    run_id = str(UUID(sys.argv[1]))
    try:
        result = service.execute(run_id)
        print(f"运行 {run_id}：{result['run']['status']}", flush=True)
    except Exception as error:
        # 阶段错误已脱敏保存；不在进程日志打印模型鉴权请求。
        print(f"运行 {run_id} 未完成：{type(error).__name__}；请读取运行对象中的错误与阶段回执", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
