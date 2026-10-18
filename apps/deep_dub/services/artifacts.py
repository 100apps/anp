"""内容寻址的私有产物：仅新增对象，读取时始终验证 SHA256 和字节数。"""
from hashlib import sha256
from pathlib import Path
import os
import tempfile
from pydantic import Field

from anp.config import secret, value
from anp.errors import BusinessError
from anp.sdk.http import async_client
from apps.deep_dub.services.versions import identifier, Contract


class ArtifactMetadata(Contract):
    media_type: str = Field(min_length=1,max_length=200,pattern=r'^[!-~]+$')
    duration_ms: int | None = Field(default=None,ge=0,strict=True)
    sample_rate: int | None = Field(default=None,gt=0,strict=True)


def object_url(path):
    parts = path.split('/')
    if len(parts) != 5 or parts[0] != 'deep-dub':
        raise BusinessError('产物对象路径不合法',422,'INVALID_ARTIFACT_PATH')
    if [identifier(p) for p in parts[1:4]] != parts[1:4]:
        raise BusinessError('产物路径必须为规范 UUID',422,'INVALID_ARTIFACT_PATH')
    digest = parts[4]
    if len(digest) != 64 or any(x not in '0123456789abcdef' for x in digest):
        raise BusinessError('产物摘要不合法',422,'INVALID_ARTIFACT_PATH')
    return value('supabase','url')+'/storage/v1/object/'+path


def headers(actor):
    return {'apikey':secret('ANON_KEY'),'Authorization':'Bearer '+actor.token}


class ArtifactService:
    async def authorize_upload(self, actor, attempt_id):
        await actor.require('deep_dub.task.run')
        attempt_id = identifier(attempt_id)
        rows = await actor.objects.select('deep_dub_stage_attempts',workspace_id='eq.'+actor.workspace_id,id='eq.'+attempt_id)
        if not rows:
            raise BusinessError('执行尝试不存在或无权访问',404,'NOT_FOUND')
        attempt = rows[0]
        if attempt['created_by'] != actor.user['id']:
            raise BusinessError('必须由原执行者提交产物',403,'FORBIDDEN')
        return attempt

    async def upload(self, actor, attempt_id, file, media_type, duration_ms=None, sample_rate=None):
        meta=ArtifactMetadata(media_type=media_type,duration_ms=duration_ms,sample_rate=sample_rate)
        attempt_id=identifier(attempt_id)
        attempt=await self.authorize_upload(actor,attempt_id)
        file = Path(file)
        # 先散列，上传和回读均流式处理；不把整片视频加载到内存。
        digest, size = sha256(), 0
        with file.open('rb') as source:
            while block := source.read(1024*1024):
                digest.update(block)
                size += len(block)
        path = f"deep-dub/{actor.workspace_id}/{attempt['project_id']}/{attempt_id}/{digest.hexdigest()}"
        url = object_url(path)

        async def blocks():
            with file.open('rb') as source:
                while block := source.read(1024*1024):
                    yield block

        async with async_client(timeout=300,trust_env=False) as client:
            response = await client.post(url,headers={**headers(actor),'Content-Type':media_type,
                'Content-Length':str(size),'x-upsert':'false'},content=blocks())
        if not response.is_success:
            # Storage 的重复对象可能以 HTTP400 + Duplicate 报告；只容许此特例进入完整回读校验。
            try:
                duplicate = response.json().get('error') in ('Duplicate','AlreadyExists')
            except ValueError:
                duplicate = False
            if not duplicate:
                raise BusinessError('私有产物上传失败',response.status_code,'ARTIFACT_UPLOAD_FAILED')
        await self.verify(actor,path,digest.hexdigest(),size)
        return await actor.objects.rpc('deep_dub_register_artifact',p_workspace=actor.workspace_id,
            p_attempt=attempt_id,p_sha256=digest.hexdigest(),p_bytes=size,p_media_type=meta.media_type,
            p_duration_ms=meta.duration_ms,p_sample_rate=meta.sample_rate)

    async def verify(self, actor, path, expected_hash, expected_size):
        return await self._read_verified(actor,path,expected_hash,expected_size)

    async def _read_verified(self, actor, path, expected_hash, expected_size, target=None):
        await actor.require('deep_dub.project.read')
        url = object_url(path).replace('/storage/v1/object/','/storage/v1/object/authenticated/',1)
        digest, size = sha256(), 0
        async with async_client(timeout=300,trust_env=False) as client:
            async with client.stream('GET',url,headers=headers(actor)) as response:
                if not response.is_success:
                    raise BusinessError('私有产物读取失败',response.status_code,'ARTIFACT_READ_FAILED')
                async for block in response.aiter_bytes():
                    size += len(block)
                    if size > expected_size:
                        raise BusinessError('产物字节数不匹配',409,'ARTIFACT_INTEGRITY_FAILED')
                    digest.update(block)
                    if target is not None:target.write(block)
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise BusinessError('产物内容摘要不匹配',409,'ARTIFACT_INTEGRITY_FAILED')
        return {'verified':True,'sha256':digest.hexdigest(),'bytes':size}

    async def download(self, actor, artifact_id, destination):
        await actor.require('deep_dub.project.read')
        rows=await actor.objects.select('deep_dub_artifacts',workspace_id='eq.'+actor.workspace_id,id='eq.'+identifier(artifact_id))
        if not rows:raise BusinessError('产物不存在或无权访问',404,'NOT_FOUND')
        item=rows[0]
        destination=Path(destination).absolute()
        parent=destination.parent.resolve(strict=True)
        if destination.exists():raise BusinessError('下载目标已存在，拒绝覆盖',409,'ARTIFACT_TARGET_EXISTS')
        # 本地工作副本先写同目录临时文件，核验后以硬链接原子发布，避免覆盖竞态或半文件。
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(prefix='.anp-artifact-',suffix='.part',dir=parent,delete=False) as target:
                temporary=Path(target.name)
                verified=await self._read_verified(actor,item['object_path'],item['sha256'],item['bytes'],target)
            await actor.require('deep_dub.project.read')
            try:os.link(temporary,destination)
            except FileExistsError:
                raise BusinessError('下载目标已存在，拒绝覆盖',409,'ARTIFACT_TARGET_EXISTS') from None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return {'artifact_id':item['id'],**verified}


service = ArtifactService()
