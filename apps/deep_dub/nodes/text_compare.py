"""保守的字符差异代理；不把转录吻合等同事实、专名、否定或听感审核。"""
from difflib import SequenceMatcher
import unicodedata

from anp.errors import BusinessError

RULE='asr-nfkc-character-diff-v1'


def normalize(text):
    value=unicodedata.normalize('NFKC',text).casefold()
    result=[]
    # 保留数学/货币符号、正负号、代码名符号；数字之间的标点不能随意丢掉。
    for i,char in enumerate(value):
        category=unicodedata.category(char)
        numeric_separator=category[0]=='P' and i>0 and i+1<len(value) and value[i-1].isdigit() and value[i+1].isdigit()
        if category[0] in ('L','N','S') or category=='Pd' or char in '+#%‰‱٪' or numeric_separator:
            result.append(char)
    return ''.join(result)


def compare(expected,recognized):
    if not isinstance(expected,str) or not isinstance(recognized,str) or not expected.strip() or len(expected)>2000 or len(recognized)>64000:
        raise BusinessError('ASR比较文本为空或超限',422,'ASR_COMPARISON_INVALID')
    left,right=normalize(expected),normalize(recognized)
    if not left or max(len(left),len(right))>4000:
        raise BusinessError('ASR字符比较超过有效边界',422,'ASR_COMPARISON_INVALID')
    previous=list(range(len(right)+1))
    for i,a in enumerate(left,1):
        current=[i]
        for j,b in enumerate(right,1):
            current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
        previous=current
    diffs=[{'operation':kind,'expected_range':[a,b],'recognized_range':[c,d],
        'expected':left[a:b],'recognized':right[c:d]}
        for kind,a,b,c,d in SequenceMatcher(None,left,right,autojunk=False).get_opcodes() if kind!='equal']
    return {'rule_revision':RULE,'expected_normalized':left,'recognized_normalized':right,
        'expected_characters':len(left),'recognized_characters':len(right),'edit_distance':previous[-1],
        'character_error_rate':previous[-1]/len(left),'normalized_exact_match':left==right,
        'difference_spans':diffs[:256],'difference_spans_truncated':len(diffs)>256,
        'difference_alignment':'SequenceMatcher定位；edit_distance另按Levenshtein计算',
        'scope':'字符差异代理，不能自动通过内容/事实/数字/否定/专名、音色身份或自然度'}
