"""按固定最高质量策略下载单个YouTube原片；仅保存白名单元数据。"""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit,parse_qs

from pydantic import BaseModel,ConfigDict,Field

from anp.errors import BusinessError
from apps.deep_dub.nodes.media import probe_media
from apps.deep_dub.services.blocking import await_blocking,file_identity
from apps.deep_dub.services.owned_tree import run_tree

RULE='youtube-best-source-v1'


class DownloadConfig(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    executable:str
    executable_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    firefox_profile:str
    user_agent:str=Field(min_length=10,max_length=500)
    node:str
    ffmpeg_directory:str
    context_revision:str=Field(min_length=1,max_length=100)

    def verify(self):
        if file_identity(self.executable)['sha256']!=self.executable_sha256:
            raise BusinessError('下载器摘要变化，须先核验',409,'DOWNLOAD_IDENTITY_MISMATCH')
        if not all(Path(p).is_file() for p in (self.node,Path(self.ffmpeg_directory)/'ffmpeg.exe',Path(self.firefox_profile)/'cookies.sqlite')):
            raise BusinessError('下载配置缺少Node、FFmpeg或所选Firefox配置',503,'DOWNLOAD_UNCONFIGURED')


def youtube_location(url):
    try:
        parsed=urlsplit(url);query=parse_qs(parsed.query,strict_parsing=True)
        if (parsed.scheme!='https' or parsed.hostname not in ('www.youtube.com','youtube.com')
            or parsed.port is not None or parsed.username is not None or parsed.password is not None
            or parsed.path!='/watch' or parsed.fragment or set(query)!={'v'} or len(query['v'])!=1):raise ValueError()
        video_id=query['v'][0]
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise BusinessError('当前来源获取只支持无播放列表参数的HTTPS YouTube单视频',422,'SOURCE_URL_UNSUPPORTED') from None
    return 'https://www.youtube.com/watch?v='+video_id,video_id


def command(config,url,folder):
    url,_=youtube_location(url)
    return [config.executable,'--ignore-config','--cookies-from-browser','firefox:'+config.firefox_profile,
        '--js-runtimes','node:'+config.node,'--ffmpeg-location',config.ffmpeg_directory,'--user-agent',config.user_agent,
        '--concurrent-fragments','1','--sleep-requests','1','--sleep-interval','10','--max-sleep-interval','20',
        '--sleep-subtitles','5','--no-playlist','-f','bv*+ba/b','-S','res,fps','--merge-output-format','mkv',
        '--windows-filenames','--no-overwrites','--socket-timeout','30','--retries','0','--fragment-retries','0',
        '--extractor-retries','0','--no-progress','--no-cache-dir','--write-info-json',
        '--paths',str(Path(folder).resolve()),'--output','source.%(ext)s','--',url]


def checked_metadata(raw,video_id):
    try:
        assert isinstance(raw,dict)
        assert raw['id']==video_id and raw['extractor_key']=='Youtube' and raw.get('_type','video')=='video'
        assert raw.get('is_live') is not True and raw.get('live_status') not in ('is_live','is_upcoming')
        assert type(raw['duration']) in (int,float) and 0<raw['duration']<=14400
        assert isinstance(raw['title'],str) and 0<len(raw['title'])<=2000
        formats=raw.get('requested_formats') or [raw]
        assert isinstance(formats,list) and len(formats) in (1,2)
        selected=[]
        for item in formats:
            assert isinstance(item,dict)
            assert item.get('format_id') and item.get('ext')
            selected.append({key:item.get(key) for key in ('format_id','ext','vcodec','acodec','width','height','fps','asr','audio_channels')})
        assert any(x.get('vcodec') not in (None,'none') for x in selected)
        assert any(x.get('acodec') not in (None,'none') for x in selected)
        return {'video_id':video_id,'title':raw['title'],'channel_id':raw.get('channel_id'),
            'channel':raw.get('channel'),'upload_date':raw.get('upload_date'),'duration_seconds':raw['duration'],
            'selected_formats':selected,'policy':'bv*+ba/b; res,fps; MKV无损合并；未请求降级、裁剪或重用旧文件'}
    except (AssertionError,KeyError,ValueError,TypeError):
        raise BusinessError('下载来源身份或音视频格式不符合约定',422,'SOURCE_DOWNLOAD_METADATA_INVALID') from None


async def fetch(config,url,folder,timeout):
    url,video_id=youtube_location(url);folder=Path(folder)
    await await_blocking(config.verify)
    try:process=await run_tree(command(config,url,folder),folder,timeout)
    except BusinessError as error:
        if error.code!='SOURCE_DOWNLOAD_FAILED':raise
        # 不返回包含签名媒体URL、Cookie路径或账号上下文的原始stderr。
        stderr=folder/'tool.stderr'
        message=stderr.read_bytes()[:1024**2].decode('utf-8',errors='replace').lower() if stderr.is_file() else ''
        if any(x in message for x in ('429','too many requests','captcha','confirm you’re not a bot','confirm you\'re not a bot')):
            raise BusinessError('来源站点限流或要求本人验证；已停止，不切换账号或网络重试',429,'SOURCE_SITE_VERIFICATION_REQUIRED') from None
        if 'sign in' in message or 'login' in message:
            raise BusinessError('所选Firefox来源会话需恢复；未切换其他账号',409,'SOURCE_LOGIN_REQUIRED') from None
        raise
    info=folder/'source.info.json'
    if not info.is_file() or info.stat().st_size>16*1024**2:
        raise BusinessError('来源元数据缺失或超限',422,'SOURCE_DOWNLOAD_METADATA_INVALID')
    try:metadata=checked_metadata(json.loads(info.read_bytes()),video_id)
    except (ValueError,UnicodeError):raise BusinessError('来源元数据不是有效JSON',422,'SOURCE_DOWNLOAD_METADATA_INVALID') from None
    files=[p for p in folder.glob('source.*') if p.suffix.lower() in ('.mkv','.mp4','.webm')]
    if len(files)!=1 or files[0].is_symlink() or files[0].resolve().parent!=folder.resolve():
        raise BusinessError('来源缺少唯一原片',422,'SOURCE_DOWNLOAD_OUTPUT_INVALID')
    file=files[0];identity=await await_blocking(file_identity,file);probe=await probe_media(file)
    if not 0<identity['bytes']<=4*1024**3 or not all(any(s['codec_type']==kind for s in probe['streams']) for kind in ('video','audio')):
        raise BusinessError('原片超限或缺少音视频流',422,'SOURCE_DOWNLOAD_OUTPUT_INVALID')
    if abs(probe['duration_ms']-metadata['duration_seconds']*1000)>2000:
        raise BusinessError('媒体时长与来源元数据差异超限',422,'SOURCE_DOWNLOAD_OUTPUT_INVALID')
    proof={'rule_revision':RULE,'source_url':url,'online':metadata,'media':probe,'file':identity,'process':process,
        'downloader_sha256':config.executable_sha256,'download_context_revision':config.context_revision,
        'new_download':True,'content_accuracy_review':'not_measured','subtitle_timing_review':'not_measured',
        'identity_scope':'单视频解析ID与请求一致且音视频可探测；非独立内容/声学/完整解码或发布许可审核'}
    return proof,file
