from collections import Counter
import re


async def analyze(state, context):
    text=state['normalized']['text']
    words=re.findall(r'[a-zA-Z0-9_]+|[\u4e00-\u9fff]',text.lower())
    return {'character_count':len(text),'token_count':len(words),
        'sentence_count':len([s for s in re.split(r'[。！？.!?]+',text) if s.strip()]),
        'top_terms':[{'term':w,'count':n} for w,n in Counter(words).most_common(8)],
        'preview':text[:160]}
