"""Offline replay: ₹5/₹10, skip expiry, opening high-low <= ₹20.

Reuses the archived six-month dataset and execution model. No broker or DB calls.
"""
import csv
import hashlib
import importlib.util
import json
import tarfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parent
BASE = OUT.parent / 'reliance-2026-04-21_2026-10-20'
spec = importlib.util.spec_from_file_location('prior_research', BASE / 'research.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)


def write_csv(name, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (OUT / name).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main():
    manifest = {x['file']: x for x in json.loads((BASE / 'data-manifest.json').read_text())}
    cache = {}
    with tarfile.open(BASE / 'market-data.tar.gz') as archive:
        for member in archive.getmembers():
            raw = archive.extractfile(member).read()
            assert hashlib.sha256(raw).hexdigest() == manifest[Path(member.name).name]['sha256']
            payload = json.loads(raw)
            cache[payload['request']] = payload['data']

    underlying = []
    for key, data in cache.items():
        if key.startswith('spot15:'):
            underlying.extend(data)
    sessions = defaultdict(list)
    for moment, bar in model.normalize(underlying).items():
        if model.START <= moment.date() <= model.END and model.dt_time(9, 15) <= moment.time() < model.dt_time(15, 30):
            sessions[moment.date()].append((moment, bar))
    assert len(sessions) == 125
    assert all(len(bars) == 25 for bars in sessions.values())
    contracts = [c for key, data in cache.items() if key.startswith('contracts:') or key == 'active_contracts' for c in data]
    cfg = model.FILTERS['skip_expiry']
    exit_cfg = ('fixed_5_10', 5, 10, False)
    trades, baseline, daily, excluded, rejected = [], [], [], [], []
    for day, candles in sorted(sessions.items()):
        opening = candles[0][1]
        width = Decimal(str(opening[1])) - Decimal(str(opening[2]))
        exceeds = width > Decimal('20')
        expiries = sorted({c['expiry'][:10] for c in contracts if c['expiry'][:10] >= str(day) and not c['weekly']})
        expiry_day = expiries[0] == str(day)
        signal = model.select_signal(day, candles, cfg, contracts)
        trade = None
        reason = None
        if signal:
            key = f"option1:{signal['instrument']}:{day}"
            bars = {t: b for t, b in model.normalize(cache[key]).items() if t.date() == day and model.dt_time(9, 15) <= t.time() < model.dt_time(15, 30)}
            trade, reason = model.simulate(signal, bars, exit_cfg, .10)
            if trade:
                trade = dict(trade, opening_range_points=str(width))
                baseline.append(trade)
                if not exceeds:
                    trades.append(trade)
            elif not exceeds:
                rejected.append(dict(date=str(day), reason=reason))
        status = 'expiry_day' if expiry_day else 'opening_range_above_20' if exceeds else 'no_signal' if signal is None else reason if reason else 'traded'
        day_row = dict(date=str(day), opening_high=opening[1], opening_low=opening[2], opening_range_points=str(width),
                       expiry_day=expiry_day, opening_range_above_20=exceeds, status=status,
                       original_signal=bool(signal), pnl_without_range_filter=trade['net'] if trade else None)
        daily.append(day_row)
        if exceeds:
            excluded.append(day_row)

    previous = json.loads((BASE / 'results.json').read_text())
    expected = next(r for r in previous['ranked_by_training'] if r['variant'] == 'skip_expiry__fixed_5_10')
    baseline_metrics = model.metrics(baseline)
    assert baseline_metrics['trades'] == expected['all_trades'] == 108
    assert baseline_metrics['net'] == expected['all_net']
    result = model.metrics(trades)
    assert len({t['date'] for t in trades}) == len(trades)
    for t in trades:
        assert Decimal(t['opening_range_points']) <= 20
        assert t['date'] != t['expiry']
        assert round((t['exit'] - t['entry']) * t['quantity'], 2) == t['gross']
        assert round(t['gross'] - t['charges'], 2) == t['net']
    removed = [t for t in baseline if Decimal(t['opening_range_points']) > 20]
    assert round(baseline_metrics['net'] - sum(t['net'] for t in removed), 2) == result['net']
    monthly = [dict(month=month, **model.metrics([t for t in trades if t['date'].startswith(month)]))
               for month in sorted({t['date'][:7] for t in trades})]
    assert round(sum(row['net'] for row in monthly), 2) == result['net']
    summary = dict(period=dict(start=str(model.START), end=str(model.END), sessions=len(sessions)),
                   rules=dict(stop=5, target=10, skip_expiry=True, max_opening_high_minus_low=20,
                              exclude_only_strictly_above_20=True, lots=2, adverse_execution_each_side=.10),
                   baseline_without_range_filter=baseline_metrics, with_range_filter=result,
                   improvement_net=round(result['net'] - baseline_metrics['net'], 2),
                   training=model.metrics([t for t in trades if t['date'] < str(model.SPLIT)]),
                   validation=model.metrics([t for t in trades if t['date'] >= str(model.SPLIT)]),
                   status_counts={s: sum(d['status'] == s for d in daily) for s in sorted({d['status'] for d in daily})},
                   excluded_days=excluded, removed_trades=removed, monthly=monthly,
                   execution_rejections=rejected, source_response_hashes_verified=len(manifest))
    (OUT / 'results.json').write_text(json.dumps(summary, indent=2))
    write_csv('trades.csv', trades)
    write_csv('daily-decisions.csv', daily)
    write_csv('excluded-days.csv', excluded)
    write_csv('monthly.csv', monthly)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
