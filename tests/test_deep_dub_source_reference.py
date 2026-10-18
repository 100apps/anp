import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.source_reference import parse_yale_reference, compare_reference
from apps.deep_dub.services.source_reference import reference_location

VIDEO = 'QXXp1bHd6gI'


def page(body, video=VIDEO):
    return (f'<html><iframe src="https://www.youtube.com/embed/{video}"></iframe>'
            '<p>网页导航不是讲稿</p><div id="inline_content"><h1>课程标题</h1>'
            + body + '</div><p>版权页脚</p></html>').encode()


def test_inline_markup_preserves_paragraphs_and_chapters_without_running_content():
    raw = page('<h3>Chapter 1. Start [00:00:00]</h3><p><strong>Professor:</strong> Hello <em>world</em>.</p>'
               '<script>transmit_credentials()</script><style>.hidden {}</style>'
               '<p>We have 4.2 million.</p><h3>Chapter 2. Later [00:03:09]</h3><p>Not a conclusion.</p>')
    result = parse_yale_reference(raw, VIDEO)
    assert result['text'] == 'Professor: Hello world.\n\nWe have 4.2 million.\n\nNot a conclusion.'
    assert [c['reported_start_ms'] for c in result['chapters']] == [0, 189000]
    assert result['semantic_review'] == result['subtitle_timing_review'] == 'not_measured'


@pytest.mark.parametrize('raw', [
    page('<h3>A [00:00:00]</h3><p>Wrong video</p>', video='abcdefghijk'),
    page('<h3>A [00:00:00]</h3><p>One</p><h3>B [00:00:00]</h3><p>Two</p>'),
    page('<h3>A [00:99:00]</h3><p>Impossible time</p>'),
    page('<h3>A [00:00:00]</h3>'),
    page('<h3>A [00:00:00]</h3><p>Text</p>')[:-35],
    page('<h3>A [00:00:00]</h3><p>Text</p></div><div id="inline_content"><p>Duplicate</p>'),
    b'\xff', b'x' * (2 * 1024**2 + 1),
], ids=['wrong-video', 'repeated-chapter', 'invalid-clock', 'empty-chapter', 'truncated', 'duplicate-body', 'invalid-utf8', 'oversized'])
def test_wrong_identity_invalid_or_incomplete_reference_is_rejected(raw):
    with pytest.raises(BusinessError):
        parse_yale_reference(raw, VIDEO)


def test_difference_localizes_deletion_and_number_without_fabricating_timing_or_correcting_asr():
    reference = "Doctorow does not claim 4.2 million returned."
    chunks = [{'text': 'Doctor does claim'}, {'text': '4.3 million returned.'}]
    result = compare_reference(reference, chunks)
    assert any(d['reference_text'] == 'not' and d['kind'] == 'delete' for d in result['differences'])
    numeric = next(d for d in result['differences'] if d['contains_number'])
    assert numeric['reference_text'] == '4.2' and numeric['asr_text'] == '4.3'
    assert numeric['nearby_asr_chunks'] == [1]
    assert result['semantic_review'] == result['subtitle_timing_review'] == 'not_measured'
    assert chunks == [{'text': 'Doctor does claim'}, {'text': '4.3 million returned.'}]


def test_case_and_typographic_quotes_are_not_reported_as_content_differences():
    result = compare_reference("It’s Doctorow's work.", [{'text': "it's doctorow’s work"}])
    assert result['difference_count'] == 0
    assert result['matched_token_count'] == 3


def test_unbounded_or_empty_recognition_is_rejected():
    with pytest.raises(BusinessError):
        compare_reference('Word.', [{'text': ''}])
    with pytest.raises(BusinessError):
        compare_reference('word ' * 40001, [{'text': 'word'}])


@pytest.mark.parametrize('url', ['http://oyc.yale.edu/history/hist-119/lecture-1',
    'https://oyc.yale.edu:443/history/hist-119/lecture-1',
    'https://oyc.yale.edu.evil.test/history/hist-119/lecture-1',
    'https://oyc.yale.edu/history/hist-119/lecture-1?redirect=localhost',
    'https://oyc.yale.edu/history/hist-119/lecture-1#fragment',
    'https://user:password@oyc.yale.edu/history/hist-119/lecture-1',
    'https://127.0.0.1/reference', 'https://oyc.yale.edu/../private'])
def test_reference_cannot_fetch_arbitrary_hosts_paths_credentials_or_redirect_parameters(url):
    with pytest.raises(BusinessError):
        reference_location(url, 'https://www.youtube.com/watch?v='+VIDEO)


def test_reference_source_requires_unambiguous_video_identity():
    url='https://oyc.yale.edu/history/hist-119/lecture-1'
    assert reference_location(url,'https://www.youtube.com/watch?v='+VIDEO)==VIDEO
    with pytest.raises(BusinessError):
        reference_location(url,'https://www.youtube.com/watch?v='+VIDEO+'&v=abcdefghijk')
