"""平台托管 LangGraph 引擎；业务图来自独立挂载目录，不进入镜像。"""
import os
from pathlib import Path
from anp.applications.registry import applications


def graph_specs():
    return {app_id:manifest['flow'].get('deployment_entry',f'apps.{app_id}.flow.deployment:graph')
        for app_id,manifest in applications().items()}


def job_concurrency(value):
    count=int(value)
    if not 1<=count<=32:raise ValueError('Agent 并发任务数须为 1–32')
    return count


def main():
    from langgraph_api.cli import run_server
    state=Path(os.environ.get('ANP_AGENT_STATE','/state/agent'))
    state.mkdir(parents=True,exist_ok=True)
    os.chdir(state)
    run_server(host='127.0.0.1',port=18124,graphs=graph_specs(),open_browser=False,
        n_jobs_per_worker=job_concurrency(os.environ.get('ANP_AGENT_CONCURRENCY','4')),
        auth={'path':'anp.runtime.agent_auth:auth'},http={'mount_prefix':'/agent'},
        disable_persistence=False,server_level='INFO')


if __name__=='__main__':main()
