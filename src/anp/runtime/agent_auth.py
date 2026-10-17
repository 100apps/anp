import secrets
from langgraph_sdk import Auth
from anp.config import secret

auth=Auth()


@auth.authenticate
async def authenticate(authorization: str | None):
    if not secrets.compare_digest(authorization or '', 'Bearer '+secret('ENGINE_TOKEN')):
        raise Auth.exceptions.HTTPException(status_code=401,detail='引擎访问凭据不正确')
    return {'identity':'anp-api'}
