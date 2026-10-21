"""Offline comparison of opening-range caps and five exit settings.

Replays archived Upstox data; no database access, broker calls or live orders.
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
spec = importlib.util.spec_from_file_location('original_model', BASE / 'research.py')
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)
EXITS = [(3, 6), (5, 10), (5, 15), (10, 18), (7, 14)]
CAPS = [24, 20]


def write_csv(name, rows):
    if rows:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        with (OUT / name).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def main():
    manifest = {row['file']: row for row in json.loads((BASE / 'data-manifest.json').read_text())}
    cache = {}
    with tarfile.open(BASE / 'market-data.tar.gz') as archive:
        for member in archive.getmembers():
            raw = archive.extractfile(member).read()
            assert hashlib.sha256(raw).hexdigest() == manifest[Path(member.name).name]['sha256']
            payload = json.loads(raw)
            cache[payload['request']] = payload['data']
    spot = [bar for key, data in cache.items() if key.startswith('spot15:') for bar in data]
    sessions = defaultdict(list)
    for ts, bar in model.normalize(spot).items():
        if model.START <= ts.date() <= model.END and model.dt_time(9, 15) <= ts.time() < model.dt_time(15, 30):
            sessions[ts.date()].append((ts, bar))
    assert len(sessions) == 125 and all(len(bars) == 25 for bars in sessions.values())
    contracts = [c for key, data in cache.items() if key.startswith('contracts:') or key == 'active_contracts' for c in data]
    signals = []
    for day, candles in sorted(sessions.items()):
        signal = model.select_signal(day, candles, model.FILTERS['skip_expiry'], contracts)
        if signal:
            width = Decimal(str(candles[0][1][1])) - Decimal(str(candles[0][1][2]))
            signals.append(dict(signal, opening_range_points=str(width)))
    assert len(signals) == 108
    bars_by_key = {}
    for signal in signals:
        day, instrument = signal['date'], signal['instrument']
        rows = cache[f'option1:{instrument}:{day}']
        bars_by_key[(day, instrument)] = {
            ts: bar for ts, bar in model.normalize(rows).items()
            if ts.date().isoformat() == day and model.dt_time(9, 15) <= ts.time() < model.dt_time(15, 30)
        }

    comparisons, all_trades, skipped, monthly, sensitivity = [], [], [], [], []
    previous = json.loads((BASE / 'results.json').read_text())
    for stop, target in EXITS:
        exit_cfg = (f'fixed_{stop}_{target}', stop, target, False)
        # Reproduce each uncapped reference before introducing the new caps.
        reference = []
        for signal in signals:
            trade, _ = model.simulate(signal, bars_by_key[(signal['date'], signal['instrument'])], exit_cfg, .10)
            if trade:
                reference.append(trade)
        expected = next(r for r in previous['ranked_by_training'] if r['variant'] == f'skip_expiry__{exit_cfg[0]}')
        ref_metrics = model.metrics(reference)
        assert ref_metrics['trades'] == expected['all_trades']
        assert ref_metrics['net'] == expected['all_net']
        for cap in CAPS:
            variant = f'cap_{cap}__{exit_cfg[0]}'
            eligible = [s for s in signals if Decimal(s['opening_range_points']) <= cap]
            trades = []
            for signal in eligible:
                trade, reason = model.simulate(signal, bars_by_key[(signal['date'], signal['instrument'])], exit_cfg, .10)
                if trade:
                    trade = dict(trade, variant=variant, opening_cap=cap, stop_distance=stop, target_distance=target)
                    assert trade['date'] != trade['expiry']
                    assert Decimal(trade['opening_range_points']) <= cap
                    trades.append(trade)
                else:
                    skipped.append(dict(variant=variant, date=signal['date'], reason=reason))
            assert len({t['date'] for t in trades}) == len(trades)
            row = dict(variant=variant, opening_cap=cap, stop_distance=stop, target_distance=target,
                       eligible_signals=len(eligible), rejected_entries=len(eligible)-len(trades), **model.metrics(trades))
            for prefix, selected in [('first_four_months', [t for t in trades if t['date'] < str(model.SPLIT)]),
                                     ('final_two_months', [t for t in trades if t['date'] >= str(model.SPLIT)])]:
                row.update({prefix+'_'+k: value for k, value in model.metrics(selected).items()})
            if (stop, target) == (5, 10):
                prior = OUT.parent / f'reliance-2026-04-21_2026-10-20-opening-max{cap}' / 'results.json'
                assert row['net'] == json.loads(prior.read_text())['with_range_filter']['net']
            comparisons.append(row)
            all_trades.extend(trades)
            for month in sorted({t['date'][:7] for t in trades}):
                monthly.append(dict(variant=variant, month=month, **model.metrics([t for t in trades if t['date'].startswith(month)])))
            for slip in [.05, .10, .25, .50]:
                adjusted = []
                for signal in eligible:
                    trade, _ = model.simulate(signal, bars_by_key[(signal['date'], signal['instrument'])], exit_cfg, slip)
                    if trade:
                        adjusted.append(trade)
                sensitivity.append(dict(variant=variant, adverse_execution_each_side=slip, **model.metrics(adjusted)))
    by_profit = sorted(comparisons, key=lambda row: row['net'], reverse=True)
    payload = dict(start=str(model.START), end=str(model.END), sessions=len(sessions),
                   skip_expiry=True, lots=2, adverse_execution_each_side=.10,
                   repeated_5_10_pair_tested_once=True, combinations=len(comparisons),
                   best_net=by_profit[0], lowest_drawdown=min(comparisons, key=lambda row: row['max_drawdown']),
                   ranked_by_net=by_profit, monthly=monthly, sensitivity=sensitivity,
                   source_hashes_verified=len(manifest))
    write_csv('comparison.csv', comparisons)
    write_csv('trades.csv', all_trades)
    write_csv('skipped.csv', skipped)
    write_csv('monthly.csv', monthly)
    write_csv('slippage.csv', sensitivity)
    (OUT / 'results.json').write_text(json.dumps(payload, indent=2))
    print(json.dumps(dict(best_net=payload['best_net'], lowest_drawdown=payload['lowest_drawdown'],
                         comparison=[{k: row[k] for k in ('opening_cap', 'stop_distance', 'target_distance', 'trades', 'net', 'max_drawdown', 'win_rate', 'first_four_months_net', 'final_two_months_net')} for row in by_profit]), indent=2))


if __name__ == '__main__':
    main()
