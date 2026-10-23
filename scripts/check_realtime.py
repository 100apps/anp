"""本机 Supabase 真实验收：用户/RLS、Data API、CDC、广播与在线状态。"""
import asyncio
from contextlib import AsyncExitStack
import json
import time
import subprocess
import uuid

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

import check
from manage import ROOT, PROJECT, ensure_owned_project, read_env


async def receive(socket, predicate, timeout=20):
    async with asyncio.timeout(timeout):
        while True:
            message = json.loads(await socket.recv())
            if predicate(message):
                return message


async def send(socket, topic, event, payload, reference):
    await socket.send(json.dumps(dict(topic=topic, event=event, payload=payload, ref=reference)))


def verify_auth_migration_permissions():
    # 与 Auth 迁移相同的角色执行真实 DDL；事务回滚，不改变现有函数。
    with check.postgres() as connection:
        rows=connection.execute("SELECT p.proname,pg_get_userbyid(p.proowner),pg_get_functiondef(p.oid) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='auth' AND p.proname IN ('uid','role','jwt') AND p.pronargs=0").fetchall()
        assert {name for name,_,_ in rows}=={'uid','role','jwt'}, 'Auth 辅助函数不完整'
        assert all(owner=='supabase_auth_admin' for _,owner,_ in rows), 'Auth 辅助函数必须由迁移角色拥有'
        connection.execute('SET LOCAL ROLE supabase_auth_admin')
        for _,_,definition in rows:
            connection.execute(definition)
        connection.rollback()
    return {'helpers':sorted(name for name,_,_ in rows),'migration_role_can_replace':True,'rolled_back':True}


async def verify(base='http://127.0.0.1:51760'):
    # 管理凭据仅保留内存；业务对象读写全部通过 Supabase HTTP API。
    ensure_owned_project(read_env(ROOT/'.env')['ANP_WORKSPACE_ID'])
    secret = json.loads(subprocess.check_output(['docker','exec',PROJECT+'-toolbox-1','cat','/data/config/credentials.json']))
    suffix = uuid.uuid4().hex[:12]
    table = 'anp_realtime_probe_' + suffix
    topic = 'realtime:anp-probe-' + suffix
    headers = {'apikey': secret['SERVICE_KEY'], 'Authorization': 'Bearer ' + secret['SERVICE_KEY']}
    users = []
    created_table = False
    result = {'scope': '真实本机数据库、Supabase HTTP/WebSocket；合成隔离数据；无供应商调用',
              'base_url': base, 'passed': False}
    result['auth_helper_migration_permissions']=verify_auth_migration_permissions()
    async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False) as client:
        try:
            tokens = []
            for index in range(2):
                email = f'anp-probe-{suffix}-{index}@example.invalid'
                password = uuid.uuid4().hex + 'aA1!'
                response = await client.post('/supabase/auth/v1/admin/users', headers=headers,
                                             json={'email': email, 'password': password, 'email_confirm': True})
                assert response.status_code == 200, 'Auth 创建隔离验收用户失败'
                users.append(response.json()['id'])
                response = await client.post('/supabase/auth/v1/token?grant_type=password',
                                             headers={'apikey': secret['ANON_KEY']},
                                             json={'email': email, 'password': password})
                assert response.status_code == 200, 'Auth 密码登录失败'
                tokens.append(response.json()['access_token'])
            result['auth_two_users'] = True
            with check.postgres() as connection:
                # 仅初始化及清理测试 schema，业务操作走 API。
                connection.execute(f'CREATE TABLE public.{table} (id uuid PRIMARY KEY, owner_id uuid NOT NULL, value text)')
                connection.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
                connection.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON public.{table} TO authenticated')
                connection.execute(f'CREATE POLICY owner_only ON public.{table} TO authenticated '
                                   'USING (auth.uid()=owner_id) WITH CHECK (auth.uid()=owner_id)')
                connection.execute(f'ALTER PUBLICATION supabase_realtime ADD TABLE public.{table}')
                connection.execute("NOTIFY pgrst, 'reload schema'")
            created_table = True
            api = '/supabase/rest/v1/' + table
            user_headers = [{'apikey': secret['ANON_KEY'], 'Authorization': 'Bearer ' + token,
                             'Prefer': 'return=representation'} for token in tokens]
            for _ in range(30):
                ready = await client.get(api, headers=user_headers[0])
                if ready.status_code == 200:
                    break
                await asyncio.sleep(.5)
            assert ready.status_code == 200, 'PostgREST schema 缓存未更新'
            ws_url = base.replace('http://', 'ws://').replace('https://', 'wss://')
            management = await client.get('/supabase/realtime/v1/api/tenants', headers=headers)
            assert management.status_code == 404, 'Realtime 租户管理 API 不应经业务网关暴露'
            rejected = ws_url + '/supabase/realtime/v1/websocket?apikey=anp-invalid-token&vsn=1.0.0'
            try:
                async with connect(rejected, proxy=None, open_timeout=20):
                    raise AssertionError('Realtime 接受了无效令牌')
            except InvalidStatus as error:
                assert error.response.status_code in (401,403), '无效令牌应被鉴权拒绝'
            result['invalid_token_rejected_management_hidden'] = True
            ws_url += '/supabase/realtime/v1/websocket?apikey=' + secret['ANON_KEY'] + '&vsn=1.0.0'
            async with AsyncExitStack() as stack:
                sockets = [await stack.enter_async_context(connect(ws_url, proxy=None, open_timeout=20)) for _ in users]
                for index, socket in enumerate(sockets):
                    await send(socket, topic, 'phx_join', {
                        'access_token': tokens[index],
                        'config': {'broadcast': {'self': True, 'ack': True},
                                   'presence': {'key': users[index], 'enabled': True},
                                   'postgres_changes': [{'event': '*', 'schema': 'public', 'table': table}]}}, '1')
                    joined = await receive(socket, lambda m: m['event'] == 'phx_reply' and m.get('ref') == '1')
                    assert joined['payload']['status'] == 'ok', 'Realtime 频道加入失败'
                    status = await receive(socket, lambda m: m['event'] == 'system')
                    assert status['payload']['status'] == 'ok', 'Realtime 数据库订阅未就绪'
                result['websocket_via_vite_agent_envoy'] = True
                identifier = str(uuid.uuid4())
                started = time.monotonic()
                response = await client.post(api, headers=user_headers[0],
                                             json={'id': identifier, 'owner_id': users[0], 'value': 'created'})
                assert response.status_code == 201, 'Data API 插入失败'
                event = await receive(sockets[0], lambda m: m['event'] == 'postgres_changes')
                assert event['payload']['data']['record']['id'] == identifier, 'CDC 数据不一致'
                result['insert_notification_ms'] = round((time.monotonic() - started) * 1000, 2)
                response = await client.get(api, headers=user_headers[1])
                assert response.status_code == 200 and response.json() == [], '其他用户可以读取不属于自己的对象'
                response = await client.post(api, headers=user_headers[1],
                                             json={'id': str(uuid.uuid4()), 'owner_id': users[0], 'value': 'forbidden'})
                assert response.status_code == 403, 'RLS 未拒绝伪造对象归属'
                try:
                    await receive(sockets[1], lambda m: m['event'] == 'postgres_changes', timeout=1.5)
                except TimeoutError:
                    result['insert_cdc_rls_other_user_excluded'] = True
                else:
                    raise AssertionError('其他用户收到受 RLS 保护的对象变化')
                response = await client.patch(api + '?id=eq.' + identifier, headers=user_headers[0], json={'value': 'updated'})
                assert response.status_code == 200 and response.json()[0]['value'] == 'updated'
                event = await receive(sockets[0], lambda m: m['event'] == 'postgres_changes')
                assert event['payload']['data']['record']['value'] == 'updated'
                try:
                    await receive(sockets[1], lambda m: m['event'] == 'postgres_changes', timeout=1.5)
                except TimeoutError:
                    result['update_cdc_rls_other_user_excluded'] = True
                else:
                    raise AssertionError('其他用户收到受 RLS 保护的对象更新')
                result['data_api_insert_read_update_rls'] = True
                await send(sockets[0], topic, 'broadcast',
                           {'type': 'broadcast', 'event': 'anp-probe', 'payload': {'marker': suffix}}, '2')
                event = await receive(sockets[1], lambda m: m['event'] == 'broadcast')
                assert event['payload']['payload']['marker'] == suffix
                result['broadcast_between_clients'] = True
                await send(sockets[0], topic, 'presence',
                           {'type': 'presence', 'event': 'track', 'payload': {'marker': suffix}}, '3')
                event = await receive(sockets[1], lambda m: m['event'] == 'presence_diff' and bool(m['payload'].get('joins')))
                assert users[0] in event['payload']['joins']
                result['presence_track'] = True
                response = await client.delete(api + '?id=eq.' + identifier, headers=user_headers[0])
                assert response.status_code == 200 and response.json()[0]['id'] == identifier
                assert (await client.get(api, headers=user_headers[0])).json() == []
                event = await receive(sockets[0], lambda m: m['event'] == 'postgres_changes')
                assert event['payload']['data']['type'] == 'DELETE'
                assert event['payload']['data']['old_record']['id'] == identifier
                result['data_api_delete_and_cdc'] = True
            result['passed'] = True
        finally:
            if created_table:
                with check.postgres() as connection:
                    connection.execute(f'DROP TABLE IF EXISTS public.{table}')
                    connection.execute("NOTIFY pgrst, 'reload schema'")
            for user in users:
                response = await client.delete('/supabase/auth/v1/admin/users/' + user, headers=headers)
                assert response.status_code == 200, '隔离验收用户清理失败'
            result['isolated_fixture_cleaned'] = True
            output = ROOT / 'var/evidence/realtime-acceptance.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


if __name__ == '__main__':
    asyncio.run(verify())
    print('Supabase Auth、对象 CRUD/RLS、Realtime CDC/广播/在线状态验收通过')
