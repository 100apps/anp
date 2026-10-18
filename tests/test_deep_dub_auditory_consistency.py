from copy import deepcopy
from apps.deep_dub.nodes.auditory_consistency import summarize


def test_identical_quotes_are_flagged_without_overwriting_model_opinions():
    measured={'windows':[{'index':0,'result':{'verdict':{'status':'mismatch','issues':[
        {'candidate_excerpt':'Keep twelve samples.','heard_excerpt':'Keep twelve samples.'},
        {'candidate_excerpt':'12','heard_excerpt':'19'}]}}},
        {'index':1,'result':{'verdict':{'status':'matched','issues':[]}}}]}
    original=deepcopy(measured);report=summarize(measured)
    assert report['mismatch_issues']==2 and report['identical_excerpt_issues']==1
    assert report['unsubstantiated_window_indexes']==[0]
    assert report['issue_locations']==[{'window_index':0,'issue_index':0}]
    assert report['adjudication_status']=='not_measured' and measured==original


def test_whitespace_is_normalized_but_case_punctuation_and_negation_are_preserved():
    cases=[(' A  word ','A word',True),('US','us',False),('Open?','Open.',False),('Do not remove','Remove',False),
           ('','',True),('','Added',False),('cafe\u0301','café',True)]
    for left,right,expected in cases:
        measured={'windows':[{'index':9,'result':{'verdict':{'status':'mismatch','issues':[{'candidate_excerpt':left,'heard_excerpt':right}]}}}]}
        assert (summarize(measured)['identical_excerpt_issues']==1)==expected
