"""只核对原始模型的引用是否支撑差异；不猜测原音或自动改写模型意见。"""
import unicodedata

RULE='auditory-excerpt-grounding-v1'


def summarize(measured):
    unsupported=[];issues=0
    def normalized(text):return ' '.join(unicodedata.normalize('NFC',text).split())
    for window in measured['windows']:
        verdict=window['result']['verdict']
        if verdict['status']!='mismatch':continue
        for index,issue in enumerate(verdict['issues']):
            issues+=1
            if normalized(issue['candidate_excerpt'])==normalized(issue['heard_excerpt']):
                unsupported.append({'window_index':window['index'],'issue_index':index})
    windows=sorted({item['window_index'] for item in unsupported})
    return {'rule_revision':RULE,'mismatch_issues':issues,'identical_excerpt_issues':len(unsupported),
        'unsubstantiated_window_indexes':windows,'unsubstantiated_window_count':len(windows),'issue_locations':unsupported,
        'adjudication_status':'not_measured','scope':'一致引文无法单独支撑所报差异，需另行查听；不证明原稿正确，也不修改模型原意见或自动通过正文'}
