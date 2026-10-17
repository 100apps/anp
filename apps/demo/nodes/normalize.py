import re
import hashlib


async def normalize(state, context):
    text=re.sub(r'\s+',' ',state['input_data']['text']).strip()
    return {'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}
