"""私有媒体Range读取；短期HttpOnly播放会话仍逐请求核验原用户和工作空间。"""
import base64
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
import time

import jwt

from anp.auth.identity import actor_from_token
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.artifacts import object_url,headers
from apps.deep_dub.services.delegation import vault
from apps.deep_dub.services.versions import identifier

COOKIE_NAME='anp_deep_dub_media'
COOKIE_PATH='/api/apps/deep_dub/media'
MEDIA_TYPES={'audio/wav','audio/x-wav','audio/mpeg','audio/flac','audio/ogg','audio/mp4',
             'video/mp4','video/webm','audio/webm'}


class MediaSessions:
    def issue(self,actor,ttl=180):
        if not isinstance(ttl,int) or isinstance(ttl,bool) or not 1<=ttl<=180:
            raise BusinessError('播放会话有效期不合法',422,'INVALID_MEDIA_SESSION')
        key=vault.key()
        # 原会话已由入口验证；这里只用exp收紧期限。先完成密钥读取，再精确计时。
        expires=min(float(jwt.decode(actor.token,options={'verify_signature':False})['exp']),time.time()+ttl)
        if expires<=time.time():raise BusinessError('登录已到期',401,'UNAUTHENTICATED')
        body=base64.urlsafe_b64encode(json.dumps({'v':1,'token':actor.token,'workspace':actor.workspace_id,
            'user':actor.user['id'],'expires':expires},separators=(',',':')).encode()).decode().rstrip('=')
        signature=hmac.new(key,b'media-session-v1.'+body.encode(),sha256).hexdigest()
        return body+'.'+signature,expires

    async def actor(self,cookie):
        if not isinstance(cookie,str) or len(cookie)>12000 or not re.fullmatch(r'[A-Za-z0-9_-]+\.[0-9a-f]{64}',cookie):
            raise BusinessError('播放会话无效或已到期',401,'INVALID_MEDIA_SESSION')
        body,signature=cookie.split('.')
        expected=hmac.new(vault.key(),b'media-session-v1.'+body.encode(),sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            raise BusinessError('播放会话无效或已到期',401,'INVALID_MEDIA_SESSION')
        try:record=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        except (ValueError,UnicodeError):
            raise BusinessError('播放会话无效或已到期',401,'INVALID_MEDIA_SESSION') from None
        if record.get('v')!=1 or record.get('expires',0)<=time.time():
            raise BusinessError('播放会话无效或已到期',401,'MEDIA_SESSION_EXPIRED')
        actor=await actor_from_token(record['token'],record['workspace'])
        if actor.user['id']!=record['user']:
            raise BusinessError('播放会话身份不匹配',401,'INVALID_MEDIA_SESSION')
        return actor


sessions=MediaSessions()


def byte_range(value,size):
    """只接受单Range；无效、多段或空对象范围由调用者返回416。"""
    if not value:return None
    if len(value)>100:return False
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not match or size<=0:return False
    left,right=match.groups()
    if not left and not right:return False
    if not left:
        suffix=int(right)
        if suffix<=0:return False
        return max(0,size-suffix),size-1
    start=int(left);end=min(int(right),size-1) if right else size-1
    return (start,end) if start<=end and start<size else False


@dataclass
class MediaStream:
    status:int
    headers:dict
    response:object=None
    client:object=None

    async def close(self):
        try:
            if self.response is not None:await self.response.aclose()
        finally:
            if self.client is not None:await self.client.aclose()

    async def chunks(self):
        received=0;expected=int(self.headers['Content-Length'])
        try:
            async for block in self.response.aiter_raw(chunk_size=256*1024):
                received+=len(block)
                if received>expected:raise BusinessError('媒体响应长度异常',502,'MEDIA_UPSTREAM_INVALID')
                yield block
            if received!=expected:raise BusinessError('媒体响应提前结束',502,'MEDIA_UPSTREAM_INVALID')
        finally:await self.close()


class MediaService:
    async def describe(self,actor,artifact_id):
        await actor.require('deep_dub.project.read')
        rows=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,
            id='eq.'+identifier(artifact_id))
        if not rows:raise BusinessError('媒体不存在或无权访问',404,'NOT_FOUND')
        item=rows[0]
        if item['media_type'] not in MEDIA_TYPES:
            raise BusinessError('该产物不是可内联播放的音视频',415,'UNSUPPORTED_MEDIA_TYPE')
        return item

    async def info(self,actor,artifact_id):
        item=await self.describe(actor,artifact_id)
        return {key:item.get(key) for key in ('id','project_id','media_type','bytes','sha256','duration_ms','sample_rate')}

    async def open(self,actor,artifact_id,range_header=None,if_range=None):
        item=await self.describe(actor,artifact_id)
        size=item['bytes'];etag='"'+item['sha256']+'"'
        selected=byte_range(range_header,size) if if_range in (None,etag) else None
        result_headers={'Content-Type':item['media_type'],'Accept-Ranges':'bytes','ETag':etag,
            'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'inline'}
        if selected is False:
            return MediaStream(416,{**result_headers,'Content-Range':f'bytes */{size}','Content-Length':'0'})
        status=206 if selected is not None else 200
        start,end=selected if selected is not None else (0,size-1)
        expected=end-start+1
        result_headers['Content-Length']=str(expected)
        request_headers={**headers(actor),'Accept-Encoding':'identity'}
        if selected is not None:
            request_headers['Range']=f'bytes={start}-{end}'
            result_headers['Content-Range']=f'bytes {start}-{end}/{size}'
        client=async_client(timeout=300,trust_env=False)
        stream=MediaStream(status,result_headers,client=client)
        try:
            url=object_url(item['object_path']).replace('/storage/v1/object/','/storage/v1/object/authenticated/',1)
            response=await client.send(client.build_request('GET',url,headers=request_headers),stream=True)
            stream.response=response
            try:actual_size=int(response.headers.get('Content-Length','-1'))
            except ValueError:actual_size=-1
            if response.status_code!=status or actual_size!=expected or response.headers.get('Content-Encoding','identity')!='identity':
                raise BusinessError('媒体存储响应不符合请求范围',502,'MEDIA_UPSTREAM_INVALID')
            if selected is not None and response.headers.get('Content-Range')!=result_headers['Content-Range']:
                raise BusinessError('媒体存储响应范围不正确',502,'MEDIA_UPSTREAM_INVALID')
            return stream
        except BaseException:
            await stream.close();raise


service=MediaService()
