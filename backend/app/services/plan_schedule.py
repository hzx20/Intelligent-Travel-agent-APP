"""Checks and corrections for explicit schedule requests."""
from copy import deepcopy
from datetime import date, timedelta
import re


_DAY = re.compile(r'第\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*天')
_CHINESE_NUMBERS = {name: index for index, name in enumerate('零一二三四五六七八九')}
_CHINESE_NUMBERS['两'] = 2
_INDOOR_MAJORITY = re.compile(r'^\s*(?:请|想|希望)?以\s*室内(?:场馆|活动|景点)?\s*为主')
_DAILY_REST = re.compile(
    r'(?<!不用)(?<!不要)(?<!无需)(?<!不必)(?<!不是)(?:每天|每日)\s*(?:都)?\s*(?:请|要|想|需要|需)?\s*'
    r'(?:(?:保留|安排|留出|预留|留)(?:一点|一些)?(?:时间)?\s*)?'
    r'(?P<lunch>午餐(?:和|与|后)?)?\s*(?P<rest>午休|休息)'
)
_TIME = re.compile(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]')
_OTHER_ARRANGEMENTS = ('餐饮', '交通', '酒店', '住宿', '休息')
_GENERIC_INDOOR_NAMES = {'', '室内场馆', '室内景点', '博物馆', '科技馆'}
_OUTDOOR_NAMES = ('动物园', '植物园', '森林公园', '露营地', '海滩', '沙滩')
_EXPLICIT_DATE = r'(?:[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]{4}年\s*[0-9]{1,2}月\s*[0-9]{1,2}[日号])'
_DATE_RANGE = re.compile(rf'(?P<start>{_EXPLICIT_DATE})(?:\s*(?:至|到|—|–|~|～|-)\s*'
                         rf'(?P<end>{_EXPLICIT_DATE}|(?:[0-9]{{1,2}}月\s*)?[0-9]{{1,2}}[日号]))?')


def trip_start_date(value, days):
    if not isinstance(value, str) or len(value) > 100:
        return None
    span = _DATE_RANGE.fullmatch(value.strip())
    if not span:
        return None
    start_parts = [int(part) for part in re.findall(r'[0-9]+', span['start'])]
    try:
        start = date(*start_parts)
        last = start + timedelta(days=days - 1)
        if span['end']:
            end_parts = [int(part) for part in re.findall(r'[0-9]+', span['end'])]
            end = date(*(start_parts[:3-len(end_parts)] + end_parts))
            if end != last:
                return None
        return start.isoformat()
    except (ValueError, OverflowError):
        return None


class ScheduleError(ValueError):
    def __init__(self, issues):
        super().__init__('；'.join(message for _, _, message in issues[:6]))
        self.issues = issues


class RepairableSchedule(ValueError):
    """The rest of the draft passed validation and can be reused."""

    def __init__(self, draft, requirements, error, *, rest_timing_preference):
        super().__init__(str(error))
        self.draft = draft
        self.requirements = requirements
        self.issues = error.issues
        self.rest_timing_preference = rest_timing_preference


def explicit_rest_timing(preferences):
    """Keep duration and clock wording near '休息'/'午休' for the model to honor."""
    if not isinstance(preferences, str):
        return ''
    return '；'.join(part for part in re.split(r'[。！？，；、;\n]', preferences)
                    if ('休息' in part or '午休' in part)
                    and re.search(r'上午|下午|傍晚|晚上|晚饭|[0-9０-９一二两三四五六七八九十半]+\s*(?:分钟|分|个?小时|点|[:：])', part))


def _day_number(text):
    if text.isascii() and text.isdigit():
        return int(text)
    if text.count('十') == 1:
        tens, units = text.split('十')
        return (10 * _CHINESE_NUMBERS.get(tens, 1) + _CHINESE_NUMBERS.get(units, 0)
                if len(tens) <= 1 and len(units) <= 1 else 0)
    return _CHINESE_NUMBERS.get(text, 0)


def schedule_requirements(collected, days):
    preferences = collected.get('preferences')
    if not isinstance(preferences, str):
        return {}
    indoor_days = set()
    for match in _DAY.finditer(preferences):
        number = _day_number(match[1])
        if 1 <= number <= days and _INDOOR_MAJORITY.match(preferences[match.end():]):
            indoor_days.add(number)
    daily_rest = _DAILY_REST.search(preferences)
    requirements = {}
    if daily_rest:
        requirements['daily_rest'] = 'midday' if daily_rest['lunch'] or daily_rest['rest'] == '午休' else 'any'
        if daily_rest['lunch']:
            requirements['daily_lunch'] = True
    if indoor_days:
        requirements['indoor_majority_days'] = sorted(indoor_days)
    return requirements


def _minute(value):
    if isinstance(value, str) and _TIME.fullmatch(value):
        return int(value[:2]) * 60 + int(value[3:])
    return None


def place_name(item):
    for key in ('poi', 'name'):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _duration(item):
    value = item.get('duration_min')
    return value if type(value) is int and 1 <= value <= 1440 else None


def validate_schedule(days, requirements):
    """Validate actual items and time intervals. A model's type labels are estimates."""
    issues = []
    indoor_days = requirements.get('indoor_majority_days', ())
    for index, day in enumerate(days, 1):
        items = [item for item in day['items'] if isinstance(item, dict)]
        rest = [item for item in items if item.get('type') == '休息']
        if rest or requirements.get('daily_rest'):
            starts = [_minute(item.get('time')) for item in items]
            if None in starts or starts != sorted(set(starts)):
                issues.append((index, 'structure', f'第{index}天必须按时间排序，且各安排的开始时间不能重复'))
                continue
            midday_rest = False
            for position, item in enumerate(items):
                if item.get('type') != '休息':
                    continue
                start = starts[position]
                duration = _duration(item)
                next_start = starts[position + 1] if position + 1 < len(starts) else 1440
                previous_duration = _duration(items[position - 1]) if position else None
                starts_during_previous = previous_duration is not None and starts[position - 1] + previous_duration > start
                if (not duration or start + duration > next_start or starts_during_previous
                        or place_name(item)):
                    kind = 'structure' if place_name(item) else 'rest_time'
                    issues.append((index, kind, f'第{index}天休息须独立安排，提供有效时长，不填地图地点，且不与前后安排重叠'))
                if 11 * 60 <= start < 15 * 60:
                    midday_rest = True
            if requirements.get('daily_rest') and not rest:
                issues.append((index, 'add_rest', f'第{index}天缺少独立的休息时段'))
            elif requirements.get('daily_rest') == 'midday' and not midday_rest:
                issues.append((index, 'rest_time', f'第{index}天须在11:00至15:00之间开始午间休息，请在午间留出完整时段并检查前后安排'))
            if requirements.get('daily_lunch') and not any(
                item.get('type') == '餐饮' and 11 * 60 <= start < 14 * 60
                for item, start in zip(items, starts)
            ):
                issues.append((index, 'add_lunch', f'第{index}天缺少11:00至14:00之间的午餐安排'))
        if index in indoor_days:
            outings = [item for item in items if item.get('type') not in _OTHER_ARRANGEMENTS]
            indoor_names = {
                item['poi'].strip().casefold() for item in outings
                if item.get('type') == '室内场馆' and isinstance(item.get('poi'), str)
            }
            if any(name.endswith(_OUTDOOR_NAMES) for name in indoor_names):
                issues.append((index, 'venue', f'第{index}天将明显包含户外游览的地点整体标为室内，请改为具体室内场馆'))
            elif len(indoor_names - _GENERIC_INDOOR_NAMES) * 2 <= len(outings):
                issues.append((index, 'venue', f'第{index}天须以室内场馆为主：请给出具体场馆名，且室内场馆超过当天游玩安排的一半'))
    if issues:
        raise ScheduleError(issues)


def repair_context(failure):
    days = []
    for number in sorted({number for number, _, _ in failure.issues}):
        items = failure.draft['days'][number - 1]['items']
        days.append({
            'day': number,
            'issues': [message for day, _, message in failure.issues if day == number],
            'items': [
                {'item': index, 'time': item.get('time'), 'duration_min': _duration(item),
                 'type': item.get('type'), 'poi': place_name(item)}
                for index, item in enumerate(items, 1)
            ],
        })
    context = {'requirements': failure.requirements, 'days': days}
    if failure.rest_timing_preference:
        context['rest_timing_preference'] = failure.rest_timing_preference
    return context


def lunch_gap_repair(failure):
    """Reserve a break only when every affected day has a supported lunch gap."""
    if (failure.rest_timing_preference or failure.requirements.get('daily_rest') != 'midday'
            or not failure.requirements.get('daily_lunch')):
        return None
    changes = []
    for number, kind, _ in failure.issues:
        if kind != 'add_rest':
            return None
        items = failure.draft['days'][number - 1]['items']
        for pos, item in enumerate(items[:-1]):
            start = _minute(item.get('time'))
            if item.get('type') != '餐饮' or not 11 * 60 <= start < 14 * 60:
                continue
            duration = _duration(item)
            access = item.get('access')
            transfer = access.get('min') if isinstance(access, dict) else None
            if not duration or type(transfer) is not int or transfer < 0:
                continue
            rest_starts = start + duration + transfer
            next_start = _minute(items[pos + 1].get('time'))
            if rest_starts < 15 * 60 and rest_starts + 30 <= next_start:
                time = f'{rest_starts // 60:02d}:{rest_starts % 60:02d}'
                changes.append({'day':number, 'changes':[{'op':'add_rest', 'time':time, 'duration_min':30}]})
                break
        else:
            return None
    return {'days':changes}


def apply_schedule_repair(failure, patch):
    allowed = {}
    for number, kind, _ in failure.issues:
        allowed.setdefault(number, {'move'}).add({'rest_time': 'set_rest'}.get(kind, kind))
    if not isinstance(patch, dict) or set(patch) != {'days'} or not isinstance(patch['days'], list):
        raise ValueError('日程纠正必须只包含 days 数组')
    draft = deepcopy(failure.draft)
    seen = set()
    for row in patch['days']:
        if not isinstance(row, dict) or set(row) != {'day', 'changes'}:
            raise ValueError('日程纠正只能包含目标日期和对应调整')
        number, changes = row['day'], row['changes']
        if type(number) is not int or number not in allowed or number in seen:
            raise ValueError('日程纠正包含无关或重复日期')
        seen.add(number)
        items = draft['days'][number - 1]['items']
        if not isinstance(changes, list) or not 1 <= len(changes) <= len(items) + 2:
            raise ValueError(f'第{number}天调整数量无效')
        _apply_day_changes(items, changes, allowed[number])
    if seen != allowed.keys():
        raise ValueError('日程纠正缺少需要调整的日期')
    return draft


_CHANGE_FIELDS = {
    'add_rest': {'op', 'time', 'duration_min'},
    'add_lunch': {'op', 'time', 'duration_min'},
    'set_rest': {'op', 'item', 'time', 'duration_min'},
    'move': {'op', 'item', 'time'},
}


def _apply_day_changes(items, changes, allowed):
    original = items[:]
    original_place_order = [id(item) for item in original if place_name(item)]
    touched, added = set(), set()
    for change in changes:
        op = change.get('op') if isinstance(change, dict) else None
        if not isinstance(op, str) or op not in allowed or set(change) != _CHANGE_FIELDS[op]:
            raise ValueError('日程纠正包含不允许的操作或字段')
        time = change['time']
        if _minute(time) is None:
            raise ValueError('日程调整时间格式应为 HH:MM')
        duration = _duration(change)
        if op != 'move' and not duration:
            raise ValueError('新增或调整的时段需要正整数分钟数')
        if op in ('add_rest', 'add_lunch'):
            if op in added:
                raise ValueError('同一天不能重复新增同类时段')
            added.add(op)
            rest = op == 'add_rest'
            activity = ('午休' if '11:00' <= time < '15:00' else '休息') if rest else '午餐'
            items.append({'type': '休息' if rest else '餐饮', 'poi': '', 'time': time, 'duration_min': duration,
                          'activity': activity, 'cost_estimate': 0 if rest else None,
                          'cost_category': '其他' if rest else '餐饮'})
            continue
        index = change['item']
        if type(index) is not int or not 1 <= index <= len(original) or index in touched:
            raise ValueError('日程调整指向无效或重复的原安排编号')
        touched.add(index)
        item = original[index - 1]
        if op == 'set_rest':
            if item.get('type') != '休息' or (_duration(item) and duration != _duration(item)):
                raise ValueError('仅能调整休息的时间，已知时长须保留')
            item['duration_min'] = duration
        item['time'] = time
    items.sort(key=lambda item: item['time'])
    if [id(item) for item in items if place_name(item)] != original_place_order:
        raise ValueError('日程纠正改变了既有地点的游览顺序')
    for position, item in enumerate(items):
        start = _minute(item['time'])
        next_start = _minute(items[position + 1]['time']) if position+1 < len(items) else 1440
        if start >= next_start or start + (_duration(item) or 0) > next_start:
            raise ValueError('日程调整后存在时间重叠或跨天')
