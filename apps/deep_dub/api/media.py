"""浏览器原生媒体元素通过短期HttpOnly会话播放，URL不携带凭据。"""
import time
from math import ceil

from fastapi import APIRouter,Depends,Header,Request,Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from anp.auth.dependencies import get_actor
from anp.auth.identity import actor_from_token
from apps.deep_dub.services.media import COOKIE_NAME,COOKIE_PATH,sessions,service

router=APIRouter(prefix='/media',tags=['deep_dub_media'])


@router.post('/session')
async def create_session(request:Request,response:Response,actor=Depends(get_actor)):
    await actor.require('deep_dub.project.read')
    cookie,expires=sessions.issue(actor)
    response.set_cookie(COOKIE_NAME,cookie,httponly=True,secure=request.url.scheme=='https',
        samesite='strict',path=COOKIE_PATH,max_age=max(1,ceil(expires-time.time())))
    response.headers['Cache-Control']='no-store'
    return {'expires_at':expires}


@router.delete('/session',status_code=204)
async def clear_session(response:Response):
    response.delete_cookie(COOKIE_NAME,path=COOKIE_PATH,samesite='strict',httponly=True)
    response.headers['Cache-Control']='no-store'


async def media_actor(request:Request,authorization:str|None=Header(None),x_workspace_id:str|None=Header(None)):
    # 显式Bearer优先，错误Bearer不能回退到另一个用户的Cookie。
    if authorization is not None:
        return await actor_from_token(authorization[7:] if authorization.startswith('Bearer ') else '',x_workspace_id)
    return await sessions.actor(request.cookies.get(COOKIE_NAME))


@router.get('/artifacts/{artifact_id}/info')
async def media_info(artifact_id:str,response:Response,actor=Depends(media_actor)):
    response.headers['Cache-Control']='private, no-store'
    return await service.info(actor,artifact_id)


@router.get('/artifacts/{artifact_id}')
async def get_media(artifact_id:str,range_header:str|None=Header(None,alias='Range'),
                    if_range:str|None=Header(None),actor=Depends(media_actor)):
    stream=await service.open(actor,artifact_id,range_header,if_range)
    if stream.status==416:return Response(status_code=416,headers=stream.headers)
    return StreamingResponse(stream.chunks(),status_code=stream.status,headers=stream.headers,
                             background=BackgroundTask(stream.close))
