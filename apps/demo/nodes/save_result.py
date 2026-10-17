from anp.sdk.storage import save_json


async def save_result(state, context):
    task=context.task
    report={'title':task['title'],'normalized':state['normalized'],'analysis':state['analysis'],
            'flow_version':'1.0.0'}
    path=f"{task['workspace_id']}/{task['app_id']}/{task['id']}/report.json"
    object_path=await save_json(context.objects.token,path,report)
    await context.objects.in_schema('app_'+task['app_id']).upsert('results',
        {'task_id':task['id'],'report':report,'object_path':object_path},'task_id')
    return {'saved':True,'object_path':object_path}
