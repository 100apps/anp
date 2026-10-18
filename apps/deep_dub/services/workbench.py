"""工作台只读查询；沿用用户RLS，分页明确，不承担生产成功状态写入。"""
from typing import Literal

from anp.errors import BusinessError
from apps.deep_dub.services.projects import service as projects
from apps.deep_dub.services.versions import identifier

RecordKind=Literal['sources','speakers','segment_sets','segments','translations','voice_references',
    'emotion_references','audio_attempts','selections','timeline_revisions','backgrounds','renders',
    'audits','scores','artifacts','stage_attempts']
RECORD_KINDS=set(RecordKind.__args__)


class WorkbenchService:
    async def tasks(self,actor):
        await actor.require('deep_dub.task.read')
        return await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,
            app_id='eq.deep_dub',order='created_at.desc,id.desc',limit=100)

    async def summary(self,actor):
        await actor.require('deep_dub.task.read')
        return await actor.objects.rpc('task_summary',p_workspace=actor.workspace_id,p_app='deep_dub')

    async def task(self,actor,task_id):
        await actor.require('deep_dub.task.read')
        task_id=identifier(task_id)
        rows=await actor.objects.select('tasks',workspace_id='eq.'+actor.workspace_id,
            app_id='eq.deep_dub',id='eq.'+task_id)
        if not rows:raise BusinessError('任务不存在或无权访问',404,'NOT_FOUND')
        runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,task_id='eq.'+task_id)
        return {**rows[0],'deep_dub_run':runs[0] if runs else None}

    async def run(self,actor,run_id):
        await actor.require('deep_dub.task.read')
        rows=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,id='eq.'+identifier(run_id))
        if not rows:raise BusinessError('运行不存在或无权访问',404,'NOT_FOUND')
        run=rows[0]
        await projects.get(actor,run['project_id'])
        stages=await actor.objects.select('deep_dub_stages',workspace_id='eq.'+actor.workspace_id,
            run_id='eq.'+run['id'],order='created_at.asc,id.asc',limit=501)
        profiles=await actor.objects.select('deep_dub_quality_profiles',workspace_id='eq.'+actor.workspace_id,
            id='eq.'+run['quality_profile_id'])
        return {'run':run,'stages':stages[:500],'stages_has_more':len(stages)>500,
                'quality_profile':profiles[0] if profiles else None}

    async def records(self,actor,project_id,kind,*,offset=0,limit=100):
        await projects.get(actor,project_id)
        if kind not in RECORD_KINDS or not isinstance(offset,int) or offset<0 or not isinstance(limit,int) or not 1<=limit<=200:
            raise BusinessError('记录类型或分页参数不正确',422,'VALIDATION_ERROR')
        rows=await actor.objects.select('deep_dub_'+kind,workspace_id='eq.'+actor.workspace_id,
            project_id='eq.'+identifier(project_id),order='created_at.asc,id.asc',offset=offset,limit=limit+1)
        return {'items':rows[:limit],'offset':offset,'limit':limit,'has_more':len(rows)>limit}


service=WorkbenchService()
