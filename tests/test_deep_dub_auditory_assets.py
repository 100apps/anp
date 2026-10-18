from copy import deepcopy
from uuid import UUID
import pytest
from anp.errors import BusinessError
from apps.deep_dub.nodes.auditory_review import CONTROLS
from apps.deep_dub.services.auditory_review import evidence_assets


def fixture():
    assets=[{'id':str(UUID(int=i+1)),'stage_attempt_id':'attempt','project_id':'project','media_type':'audio/wav','bytes':1000} for i in range(len(CONTROLS))]
    note={'id':str(UUID(int=100)),'stage_attempt_id':'attempt','project_id':'project','media_type':'application/json','bytes':2000}
    assets.append(note)
    return {'id':'attempt','project_id':'project','outputs':sorted(a['id'] for a in assets)},assets,note


def test_json_is_identified_by_type_and_ownership_not_sorted_uuid_position():
    attempt,assets,note=fixture()
    assert attempt['outputs'][-1]==note['id']
    assert evidence_assets(attempt,assets)[1]==note
    assert evidence_assets({**attempt,'outputs':list(reversed(attempt['outputs']))},list(reversed(assets)))[1]==note


@pytest.mark.parametrize('defect',['second_json','wrong_type','other_project','other_attempt','missing','duplicate','wrong_id','oversize'])
def test_rejects_ambiguous_or_misbound_artifact_sets(defect):
    attempt,assets,note=fixture();assets=deepcopy(assets)
    if defect=='second_json':assets[0]['media_type']='application/json'
    if defect=='wrong_type':assets[0]['media_type']='text/plain'
    if defect=='other_project':assets[0]['project_id']='other'
    if defect=='other_attempt':assets[0]['stage_attempt_id']='other'
    if defect=='missing':assets.pop()
    if defect=='duplicate':attempt['outputs'][0]=attempt['outputs'][1]
    if defect=='wrong_id':assets[0]['id']=str(UUID(int=200))
    if defect=='oversize':assets[-1]['bytes']=16*1024**2+1
    with pytest.raises(BusinessError):evidence_assets(attempt,assets)
