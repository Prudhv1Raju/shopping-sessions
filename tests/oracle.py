"""Deliberately simple independent correctness oracle for small datasets only."""
import csv
import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

DAY = 86_400_000
WEEK = 7 * DAY
EPOCH = dt.datetime(1970, 1, 1)
STAMP = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(Z|[+-][0-9]{2}:[0-9]{2})\Z")


def uint(value):
    if not value or any(c < '0' or c > '9' for c in value):
        return None
    value = value.lstrip('0') or '0'
    return int(value) if len(value) <= 19 else 10**20


def timestamp(value):
    numeric = uint(value)
    if numeric is not None:
        return numeric
    match = STAMP.fullmatch(value)
    if not match:
        return None
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    zone = match.group(7)
    try:
        stamp = dt.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    offset = 0
    if zone != 'Z':
        hours, minutes = int(zone[1:3]), int(zone[4:6])
        if hours > 23 or minutes > 59:
            return None
        offset = (hours * 60 + minutes) * 60_000
        if zone[0] == '-':
            offset = -offset
    delta = stamp - EPOCH
    return delta.days * DAY + delta.seconds * 1000 - offset


def rows(path, width):
    files = sorted(path.glob('*.csv')) if path.is_dir() else [path]
    for file in files:
        lines = file.read_bytes().split(b'\n')
        for line in lines[1:]:
            cells = line.split(b',')
            if len(cells) != width:
                continue
            output = []
            for cell in cells:
                if cell.startswith(b'"') and cell.endswith(b'"') and len(cell) >= 2:
                    cell = cell[1:-1]
                if b'"' in cell:
                    break
                output.append(cell.decode('utf-8'))
            if len(output) == width:
                yield output


def key(value):
    return value.encode('utf-8')


def compute(root):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text())
    start, end = timestamp(manifest['window_start']), timestamp(manifest['window_end'])
    parents = {}

    def find(name):
        parents.setdefault(name, name)
        while parents[name] != name:
            parents[name] = parents[parents[name]]
            name = parents[name]
        return name

    for a, b in rows(root / 'links', 2):
        if a and b:
            a, b = find(a), find(b)
            if key(a) > key(b):
                a, b = b, a
            parents[b] = a
    products = dict(rows(root / 'products.csv', 2))
    channels = dict(rows(root / 'campaigns.csv', 2))
    earliest = {}
    for row in rows(root / 'events', 7):
        eid = row[0]
        if eid and (eid not in earliest or row[3] < earliest[eid][3]):
            earliest[eid] = row
    events = defaultdict(list)
    for eid, user, stamp, ingest, kind, product, price in earliest.values():
        time = timestamp(stamp)
        money = uint(price) if kind == 'purchase' else 0
        if not user or not product or kind not in ('view', 'cart', 'purchase'):
            continue
        if time is None or not start <= time < end:
            continue
        if kind == 'purchase' and (money is None or not 1 <= money <= 10_000_000):
            continue
        if timestamp(ingest) - time > DAY:
            continue
        events[find(user)].append((time, money, products.get(product, 'unknown')))
    unique_clicks = {}
    for row in rows(root / 'clicks', 4):
        if row[0]:
            unique_clicks.setdefault(row[0], row)
    clicks = defaultdict(list)
    for cid, user, campaign, stamp in unique_clicks.values():
        time = timestamp(stamp)
        if user and campaign in channels and time is not None and start - WEEK <= time < end:
            clicks[find(user)].append((time, cid, campaign))
    users = {}
    campaigns = defaultdict(lambda: [0, 0, 0, 0])
    for user, user_events in events.items():
        user_events.sort(key=lambda event: event[0])
        sessions = []
        for event in user_events:
            if not sessions or event[0] - sessions[-1][-1][0] > 1_800_000:
                sessions.append([])
            sessions[-1].append(event)
        for session in sessions:
            begin = session[0][0]
            day = (EPOCH + dt.timedelta(milliseconds=begin)).date().isoformat()
            total = sum(event[1] for event in session)
            urow = users.setdefault((user, day), [0, 0, 0, defaultdict(int)])
            urow[0] += 1
            urow[1] += len(session)
            urow[2] += total
            for _, money, category in session:
                if money:
                    urow[3][category] += money
            qualifying = [click for click in clicks[user] if begin - WEEK <= click[0] <= begin]
            credited = min(qualifying, key=lambda click: (-click[0], key(click[1])))[2] if qualifying else 'organic'
            crow = campaigns[credited, day]
            crow[0] += 1
            crow[1] += int(total > 0)
            crow[2] += total
            for campaign in {click[2] for click in qualifying}:
                campaigns[campaign, day][3] += 1
    user_rows = []
    for (user, day), (sessions, count, money, categories) in users.items():
        top = min(categories, key=lambda category: (-categories[category], key(category))) if categories else None
        user_rows.append([user, day, sessions, count, money, top])
    campaign_rows = [[campaign, channels.get(campaign, 'organic'), day, *numbers] for (campaign, day), numbers in campaigns.items()]
    return {'user_daily': user_rows, 'campaign_daily': campaign_rows}


def write_expected(input_dir, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    headers = {
        'user_daily': ['user_id', 'day', 'sessions', 'events', 'revenue_cents', 'top_category'],
        'campaign_daily': ['campaign_id', 'channel', 'day', 'sessions', 'purchasing_sessions', 'revenue_cents', 'assisted_sessions'],
    }
    for name, result in compute(input_dir).items():
        with (output_dir / (name + '.csv')).open('w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f, lineterminator='\n', quoting=csv.QUOTE_ALL)
            writer.writerow(headers[name])
            writer.writerows(result)


# Stable import interface used by independent differential tests.
run = compute


if __name__ == '__main__':
    import sys
    write_expected(sys.argv[1], sys.argv[2])
