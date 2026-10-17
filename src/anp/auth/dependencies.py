from fastapi import Header
from anp.auth.identity import actor_from_token


async def get_actor(authorization: str | None=Header(None),x_workspace_id: str | None=Header(None)):
    token=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    return await actor_from_token(token,x_workspace_id)
