from copy import deepcopy
import pytest
from anp.errors import BusinessError
from apps.deep_dub.nodes.source_fetch import youtube_location,checked_metadata,DownloadConfig,command


@pytest.mark.parametrize('url',['http://www.youtube.com/watch?v=QXXp1bHd6gI','https://127.0.0.1/watch?v=QXXp1bHd6gI',
    'https://www.youtube.com.evil.invalid/watch?v=QXXp1bHd6gI','https://user@www.youtube.com/watch?v=QXXp1bHd6gI',
    'https://www.youtube.com:443/watch?v=QXXp1bHd6gI','https://www.youtube.com/watch?v=QXXp1bHd6gI&list=PL123',
    'https://www.youtube.com/watch?v=QXXp1bHd6gI&v=other123456','https://www.youtube.com/watch?v=QXXp1bHd6gI#download',
    'file:///C:/x','--exec=bad',None])
def test_only_explicit_youtube_single_video(url):
    with pytest.raises(BusinessError):youtube_location(url)


def test_selection_and_metadata_whitelist_no_signed_urls(tmp_path):
    url='https://www.youtube.com/watch?v=QXXp1bHd6gI'
    assert youtube_location(url)==(url,'QXXp1bHd6gI')
    config=DownloadConfig(executable='downloader',executable_sha256='a'*64,firefox_profile='profile',node='node',
        ffmpeg_directory='ffmpeg',user_agent='fixture agent',context_revision='test-v1')
    args=command(config,url,tmp_path)
    assert args[-2:]==['--',url] and args[args.index('-f')+1]=='bv*+ba/b'
    assert '--no-check-certificates' not in args and '--no-playlist' in args
    raw={'id':'QXXp1bHd6gI','extractor_key':'Youtube','duration':2586,'title':'fixture',
        'url':'https://media.invalid/signed?token=private','http_headers':{'Cookie':'private'},
        'requested_formats':[{'format_id':'vp9','ext':'webm','vcodec':'vp9','acodec':'none','url':'private'},
            {'format_id':'opus','ext':'webm','vcodec':'none','acodec':'opus','http_headers':{'secret':'private'}}]}
    result=checked_metadata(raw,'QXXp1bHd6gI')
    assert 'private' not in repr(result) and len(result['selected_formats'])==2
    for change in ('identity','no_audio','live','playlist','duration'):
        bad=deepcopy(raw)
        if change=='identity':bad['id']='other123456'
        elif change=='no_audio':bad['requested_formats']=bad['requested_formats'][:1]
        elif change=='live':bad['is_live']=True
        elif change=='playlist':bad['_type']='playlist'
        else:bad['duration']=float('nan')
        with pytest.raises(BusinessError):checked_metadata(bad,'QXXp1bHd6gI')
