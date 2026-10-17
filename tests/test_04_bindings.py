from anp.management.bindings import bind


def test_external_resources_rebind_and_remove_dependencies():
    document={'services':{'redis':{},'langfuse-web':{'depends_on':{'redis':{'condition':'service_healthy'}},'environment':{'KEEP':'yes'}}}}
    settings={'deployment':{'external_services':['redis'],'environment':{'langfuse-web':{'REDIS_HOST':'cloud.example','REDIS_AUTH':'${PW}'}},
        'dbx_connections':[{'id':'cloud','password':'${PW}'}]}}
    connections=bind(document,settings,{'PW':'private-value'})
    assert 'redis' not in document['services']
    service=document['services']['langfuse-web']
    assert service['depends_on']=={}
    assert service['environment']=={'KEEP':'yes','REDIS_HOST':'cloud.example','REDIS_AUTH':'private-value'}
    assert connections[0]['password']=='private-value'


def test_unknown_binding_fails_early():
    import pytest
    with pytest.raises(ValueError,match='未知组件'):
        bind({'services':{}},{'deployment':{'environment':{'typo':{'X':'1'}}}}, {})
