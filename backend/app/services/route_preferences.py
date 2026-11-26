from typing import Literal


RouteMode = Literal['walking', 'driving']
MODES = ('walking', 'driving')


def is_stop(item):
    return not (item.kind == '休息' and item.lng is None and item.lat is None and not item.amap_id)


def located(item):
    return item.lng is not None and item.lat is not None


def adjacent(items):
    stops = [item for item in items if is_stop(item)]
    return zip(stops, stops[1:])


def ordered(items):
    return sorted(items, key=lambda item: (item.sort_order, item.id))


def leg_key(origin, destination):
    return f'{origin.id}:{destination.id}'


def selection(origin, destination, mode):
    return {'mode': mode, 'points': [[item.lng, item.lat, item.amap_id or '']
                                     for item in (origin, destination)]}


def active_selections(saved, items):
    current = {}
    for origin, destination in adjacent(items):
        key = leg_key(origin, destination)
        row = (saved or {}).get(key)
        if (row and row.get('mode') in MODES and located(origin) and located(destination)
                and row == selection(origin, destination, row['mode'])):
            current[key] = row
    return current
