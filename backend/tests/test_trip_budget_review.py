import json

from app.db.models import AiPlan
from app.services.trip_budget import from_plan
from test_trips import account


def test_travel_conversion_retains_reference_but_never_seeds_quotes(client):
    c, sessions = client
    owner = account(c, 'budget_review_owner')
    other = account(c, 'budget_review_other')
    with sessions() as db:
        owner_id = c.get('/api/auth/me', headers=owner).json()['id']
        original = json.dumps({'days':[{}],
            'budget_summary':{'总计':0},
            'budget_validation':{'summary_checked':True,'calculation_basis':'items_and_additions_v1',
                                 'unpriced_items':14,'unlisted_categories':['住宿']}})
        plan = AiPlan(user_id=owner_id, session_id='budget-review', status='completed',
                      intent_json='{"budget":3000,"dates":"2026-11-21","destination":"杭州"}', result_json=original)
        db.add(plan)
        db.commit()
        plan_id, identity = plan.id, {'version':plan.version, 'revision':plan.revision}
    conversion = f'/api/trips/from-plan/{plan_id}/convert'
    response = c.post(conversion, headers=owner, json=identity)
    assert response.status_code == 201, response.text
    trip = response.json()['trip']
    reference = {'limit_minor':300000,'subtotal_minor':0,'unpriced_items':14,'unlisted_categories':['住宿']}
    assert trip['budget_review'] == {'reference':reference}
    root = f"/api/trips/{trip['id']}"
    url = root + '/budget-review'
    entries = {
        'lodging':{'status':'quoted','amount_minor':320025,'source':'合成测试 两晚三人含税','checked_on':'2026-11-18'},
        'admission':{'status':'quoted','amount_minor':0,'source':'现场标价：基本参观免费','checked_on':'2026-11-18'},
        'intercity_transport':{'status':'excluded','source':'往返铁路单独预算'},
    }
    payload = {'version':trip['version'], 'checklist':{'limit_minor':300000,'entries':entries}}
    assert c.patch(url, headers=other, json=payload).status_code == 404
    result = c.patch(url, headers=owner, json=payload)
    assert result.status_code == 200 and result.headers['Cache-Control'] == 'private, no-store'
    current = result.json()
    assert current['budget_review']['reference'] == reference
    assert current['budget_review']['checklist']['entries']['admission']['amount_minor'] == 0
    assert current['budget_review']['checklist']['entries']['lodging']['amount_minor'] == 320025
    assert current['budget_review']['checklist_scope'] == {'start_date':'2026-11-21','destination':'杭州','days':1}
    assert c.patch(url, headers=owner, json=payload).status_code == 409
    assert c.get(root, headers=owner).json() == current
    assert 'budget_review' not in c.get('/api/trips', headers=owner).json()['items'][0]
    assert c.get(root+'/expenses', headers=owner).json() == {'items':[],'totals':{}}
    share = c.post(root+'/sharing/links', headers=owner, json={'mode':'view'})
    assert share.status_code == 201
    public = c.post('/api/trip-sharing/view', json={'token':share.json()['token']})
    assert public.status_code == 200
    assert 'budget_review' not in public.json() and '合成测试' not in public.text
    shifted = c.patch(root, headers=owner, json={'version':current['version'],'start_date':'2026-11-22'}).json()
    assert shifted['budget_review'] == current['budget_review']
    assert c.post(conversion, headers=owner, json=identity).json()['trip'] == shifted
    with sessions() as db:
        assert db.get(AiPlan, plan_id).result_json == original


def test_untrustworthy_summary_or_incomplete_manual_source_does_not_become_a_quote(client):
    assert from_plan({'budget':'3000'}, {'budget_summary':{'总计':0}, 'budget_validation':{
        'summary_checked':True,'calculation_basis':'legacy'}}) == {'reference':{'limit_minor':300000}}
    assert from_plan({'budget':True}, {}) == {}
    c, _ = client
    owner = account(c, 'budget_invalid_owner')
    trip = c.post('/api/trips', headers=owner, json={'title':'费用测试'}).json()
    url = f"/api/trips/{trip['id']}/budget-review"
    for row in (
        {'status':'quoted','amount_minor':0,'source':'官网','checked_on':None},
        {'status':'quoted','amount_minor':0,'source':' ','checked_on':'2026-11-18'},
        {'status':'quoted','amount_minor':True,'source':'官网','checked_on':'2026-11-18'},
        {'status':'excluded','amount_minor':100,'source':'原因'},
    ):
        result = c.patch(url, headers=owner, json={'version':1,'checklist':{'limit_minor':300000,'entries':{'lodging':row}}})
        assert result.status_code == 422
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
