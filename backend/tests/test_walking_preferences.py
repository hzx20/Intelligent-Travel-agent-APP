import json

from app.db.models import AiPlan
from app.services.walking_preferences import from_planning
from test_plan_trip_conversion import setup


def test_only_explicit_limits_are_copied():
    assert from_planning('带5岁孩子，避免爬山和长距离步行') == {
        'prefer_short_walks': True, 'check_climbs': True,
    }
    assert from_planning('每天步行距离不超过３．２公里') == {'daily_limit_m': 3200}
    assert from_planning('每天驾车不超过3公里，不用少走路') == {}


def test_conversion_and_versioned_edit_preserve_source_preferences(client):
    c, sessions, owner, _, pid, identity = setup(client)
    intent = {'destination':'杭州','preferences':'避免爬山和长距离步行，每天步行最多3.5公里'}
    with sessions() as db:
        db.get(AiPlan, pid).intent_json = json.dumps(intent, ensure_ascii=False)
        db.commit()
    trip = c.post(f'/api/trips/from-plan/{pid}/convert',headers=owner,json=identity).json()['trip']
    assert trip['walking_preferences'] == {'prefer_short_walks':True,'check_climbs':True,'daily_limit_m':3500}
    url = f"/api/trips/{trip['id']}"
    assert c.patch(url,headers=owner,json={'version':trip['version'],'walking_preferences':{'daily_limit_m':True}}).status_code==422
    patch={'version':trip['version'],'walking_preferences':{'daily_limit_m':2000}}
    assert c.patch(url,headers=owner,json=patch).status_code==200
    assert c.get(url,headers=owner).json()['walking_preferences']=={'daily_limit_m':2000}
    assert c.patch(url,headers=owner,json=patch).status_code==409
    with sessions() as db:
        assert json.loads(db.get(AiPlan,pid).intent_json)==intent
