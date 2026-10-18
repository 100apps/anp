"""官方来源参考解析及逐词差异；不将参考文字、章节或相似度当作内容/时序审核。"""
from difflib import SequenceMatcher
from hashlib import sha256
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit

from anp.errors import BusinessError

RULE = 'yale-reference-and-asr-diff-v1'
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}\Z')
WORD = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)
CHAPTER = re.compile(r'(.+?)\s*\[(\d{2}):(\d{2}):(\d{2})\]\s*\Z')


def invalid(message):
    raise BusinessError(message, 422, 'SOURCE_REFERENCE_INVALID')


class YaleTranscript(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.inert = 0
        self.heading = False
        self.heading_parts = []
        self.parts = []
        self.chapter = None
        self.chapters = []
        self.video_ids = set()
        self.containers = 0

    def flush(self):
        if self.chapter is not None:
            paragraphs = [' '.join(p.split()) for p in ''.join(self.parts).split('\n') if p.strip()]
            if not paragraphs:
                invalid('官方章节缺少正文')
            self.chapters.append({**self.chapter, 'paragraphs': paragraphs})
        self.parts = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'iframe':
            url = urlsplit(attrs.get('src', ''))
            if url.scheme == 'https' and url.hostname in ('www.youtube.com', 'www.youtube-nocookie.com'):
                match = re.fullmatch(r'/embed/([A-Za-z0-9_-]{11})', url.path)
                if match:
                    self.video_ids.add(match[1])
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif attrs.get('id') == 'inline_content':
                self.depth = 1
                self.containers += 1
        if not self.depth:
            return
        if tag in ('script', 'style'):
            self.inert += 1
        if self.inert:
            return
        if tag == 'h3':
            self.flush()
            self.chapter = None
            self.heading = True
            self.heading_parts = []
        elif tag in ('p', 'br'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in ('script', 'style') and self.inert:
            self.inert -= 1
        elif not self.inert and tag == 'h3' and self.heading:
            self.heading = False
            heading = ' '.join(''.join(self.heading_parts).split())
            match = CHAPTER.fullmatch(heading)
            if not match or int(match[3]) >= 60 or int(match[4]) >= 60:
                invalid('官方章节标题缺少合法时间')
            seconds = int(match[2]) * 3600 + int(match[3]) * 60 + int(match[4])
            self.chapter = {'title': match[1], 'reported_start_ms': seconds * 1000}
        elif not self.inert and tag == 'p':
            self.parts.append('\n')
        if tag == 'div':
            self.depth -= 1
            if not self.depth:
                self.flush()
                self.chapter = None

    def handle_data(self, data):
        if self.depth and not self.inert:
            if self.heading:
                self.heading_parts.append(data)
            elif self.chapter is not None:
                self.parts.append(data)


def parse_yale_reference(raw, expected_video_id):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 2 * 1024**2:
        invalid('参考页必须是两MiB以内的HTML字节')
    if not isinstance(expected_video_id, str) or not VIDEO_ID.fullmatch(expected_video_id):
        invalid('来源视频ID格式无效')
    parser = YaleTranscript()
    try:
        parser.feed(raw.decode('utf-8-sig'))
        parser.close()
    except (UnicodeError, ValueError):
        invalid('参考页编码或结构无效')
    if parser.depth or parser.inert or parser.heading or parser.containers != 1:
        invalid('参考正文不完整或包含多个正文容器')
    if parser.video_ids != {expected_video_id}:
        invalid('官方嵌入视频与请求来源不一致')
    starts = [c['reported_start_ms'] for c in parser.chapters]
    if not starts or len(starts) > 100 or starts[0] != 0 or any(a >= b for a, b in zip(starts, starts[1:])):
        invalid('官方章节时间缺失或不递增')
    text = '\n\n'.join(p for c in parser.chapters for p in c['paragraphs'])
    if not text or len(text) > 250000:
        invalid('官方参考正文缺失或超限')
    return {'rule_revision': RULE, 'html_sha256': sha256(raw).hexdigest(), 'video_id': expected_video_id,
            'chapters': parser.chapters, 'text': text, 'text_sha256': sha256(text.encode()).hexdigest(),
            'source_identity_scope': '官方页面嵌入同一视频ID，未证明本机媒体字节与该视频一致',
            'semantic_review': 'not_measured', 'subtitle_timing_review': 'not_measured'}


def tokens(text):
    return [{'text': m.group(), 'key': unicodedata.normalize('NFKC', m.group()).casefold().replace('’', "'"),
             'start_char': m.start(), 'end_char': m.end()} for m in WORD.finditer(text)]


def compare_reference(reference_text, asr_chunks):
    if (not isinstance(reference_text, str) or not 0 < len(reference_text) <= 250000
        or not isinstance(asr_chunks, list) or not 0 < len(asr_chunks) <= 500):
        invalid('参考文本或识别分块超限')
    observed = []
    for index, chunk in enumerate(asr_chunks):
        text = chunk.get('text') if isinstance(chunk, dict) else None
        if not isinstance(text, str) or len(text) > 64000:
            invalid('识别分块不是有界文本')
        observed.extend({**word, 'chunk_index': index} for word in tokens(text))
    expected = tokens(reference_text)
    if not expected or not observed or max(len(expected), len(observed)) > 40000:
        invalid('参考或识别文本没有合法词，或超过四万词')
    diffs = []
    matched = 0
    for action, a, b, c, d in SequenceMatcher(None, [t['key'] for t in expected], [t['key'] for t in observed], autojunk=False).get_opcodes():
        if action == 'equal':
            matched += b - a
            continue
        left, right = expected[a:b], observed[c:d]
        nearby = observed[c:d] or observed[max(0, c - 1):min(len(observed), c + 1)]
        diffs.append({'kind': action, 'reference_tokens': [a, b], 'asr_tokens': [c, d],
                      'reference_text': reference_text[left[0]['start_char']:left[-1]['end_char']] if left else '',
                      'asr_text': ' '.join(t['text'] for t in right),
                      'nearby_asr_chunks': sorted({t['chunk_index'] for t in nearby}),
                      'contains_number': any(any(char.isdigit() for char in t['text']) for t in left + right)})
    return {'rule_revision': RULE, 'reference_sha256': sha256(reference_text.encode()).hexdigest(),
            'asr_texts_sha256': sha256('\n'.join(c['text'] for c in asr_chunks).encode()).hexdigest(),
            'reference_token_count': len(expected), 'asr_token_count': len(observed), 'matched_token_count': matched,
            'difference_count': len(diffs), 'differences': diffs,
            'semantic_review': 'not_measured', 'subtitle_timing_review': 'not_measured',
            'scope': '标准化逐词文本差异及附近ASR块索引，不判定哪方正确，不修改原文，不生成字幕时间'}
