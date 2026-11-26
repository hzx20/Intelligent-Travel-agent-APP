"""A structurally valid over-budget model result must not become saved history."""
import json

from app.db.models import AiPlan
from app.services import graph, itinerary
from test_trips import account
from test_v13_jobs import _wait_job


def test_real_graph_stops_before_map_and_history_after_two_invalid_budgets(client, monkeypatch):
    c, sessions = client
    owner = account(c, 'budget_job_owner')
    calls, later_nodes = [], []

    async def clarify(*args, **kwargs):
        return {'done': True, 'collected': {'destination': '杭州', 'days': 3, 'budget': 3000},
                'question': None, 'missing': []}

    async def model(*args, **kwargs):
        calls.append(1)
        return json.dumps({'budget_format': 'items_and_additions_v1',
            'additional_costs': [{'name': '三日补充费用', 'category': '住宿', 'amount': 3250}],
            'days': [{'day': day, 'items': [{'poi': '公开地点', 'time': '09:00', 'cost_category': '其他'}]}
                                   for day in range(1, 4)],
            'budget_summary': {'交通': 300, '住宿': 1500, '餐饮': 800, '门票': 650, '总计': 3650}})

    def forbidden(*args, **kwargs):
        later_nodes.append(True)
        raise AssertionError('Invalid itinerary must not reach maps or weather')

    monkeypatch.setattr(graph, 'clarify_turn', clarify)
    monkeypatch.setattr(itinerary, 'chat', model)
    monkeypatch.setattr(graph.map_verify, 'verify_itinerary_spots', forbidden)
    monkeypatch.setattr(graph.weather, 'fetch_weather', forbidden)
    response = c.post('/api/plan/jobs', headers=owner, json={
        'history': [], 'user_input': '杭州三天，预算3000元', 'prev_collected': {}})
    assert response.status_code == 200, response.text
    final, _ = _wait_job(c, response.json()['job_id'], headers=owner)
    assert final['status'] == 'error' and final['result']['done'] is False
    assert not final['result'].get('itinerary')
    assert calls == [1, 1] and later_nodes == []
    with sessions() as db:
        assert db.query(AiPlan).count() == 0
