from dataclasses import dataclass
import httpx
from anp.config import value, secret
from anp.errors import BusinessError
from anp.sdk.objects import Objects


@dataclass
class Actor:
    token: str
    user: dict
    workspace_id: str

    @property
    def objects(self):
        return Objects(self.token)

    async def require(self, permission):
        allowed = await self.objects.rpc('has_permission', p_workspace=self.workspace_id,
                                         p_permission=permission)
        if not allowed:
            raise BusinessError('没有执行此操作的权限', 403, 'FORBIDDEN')


async def actor_from_token(token, workspace_id=None):
    if not token:
        raise BusinessError('请先登录', 401, 'UNAUTHENTICATED')
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.get(value('supabase', 'url') + '/auth/v1/user',
            headers={'apikey': secret('ANON_KEY'), 'Authorization': 'Bearer ' + token})
    if not response.is_success:
        raise BusinessError('登录已失效，请重新登录', 401, 'UNAUTHENTICATED')
    user = response.json()
    memberships = await Objects(token).select('members', user_id='eq.' + user['id'])
    if not memberships:
        raise BusinessError('账号尚未加入工作空间', 403, 'NO_WORKSPACE')
    selected = next((m for m in memberships if m['workspace_id'] == workspace_id), None) if workspace_id else memberships[0]
    if selected is None:
        raise BusinessError('无权访问该工作空间', 403, 'FORBIDDEN')
    return Actor(token, user, selected['workspace_id'])
