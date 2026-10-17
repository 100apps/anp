from fastapi import APIRouter,Depends,Header
from anp.auth.dependencies import get_actor
from anp.tasks.service import TaskService,TaskInput
from anp.applications.registry import application,entrypoint


def task_router(app_id):
    router=APIRouter(prefix=f'/api/apps/{app_id}/tasks',tags=[app_id])
    service=entrypoint(application(app_id)['services']['entry'])
    input_model=getattr(service,'input_model',TaskInput)

    @router.post('',status_code=201)
    async def create(body: input_model,actor=Depends(get_actor),idempotency_key: str | None=Header(None)):
        return await service.create(actor,body.model_dump(),idempotency_key)

    @router.get('')
    async def list_tasks(actor=Depends(get_actor)):
        return await service.list(actor)

    @router.get('/{task_id}')
    async def get_task(task_id: str,actor=Depends(get_actor)):
        return await service.get(actor,task_id)

    @router.post('/{task_id}/run')
    async def run(task_id: str,actor=Depends(get_actor)):
        return await service.run(actor,task_id,'http')

    @router.post('/{task_id}/cancel')
    async def cancel(task_id: str,actor=Depends(get_actor)):
        return await service.cancel(actor,task_id)

    @router.get('/{task_id}/result')
    async def result(task_id: str,actor=Depends(get_actor)):
        return await service.result(actor,task_id)
    return router
