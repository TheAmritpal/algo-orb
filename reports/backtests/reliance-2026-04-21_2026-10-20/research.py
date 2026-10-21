"""Throwaway, read-only historical research; never imports a strategy worker.

Run from repository root with its venv. Cache contains public market data only.
"""
import calendar
import csv
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
CACHE = Path(os.getenv('ORB_RESEARCH_CACHE','/tmp/reliance-orb-history'))
OUT = ROOT / 'reports/backtests/reliance-2026-04-21_2026-10-20'
START, END, SPLIT = date(2026,4,21), date(2026,10,20), date(2026,8,21)
IST = ZoneInfo('Asia/Kolkata')
UNDERLYING = 'NSE_EQ|INE002A01018'

def emit(*v):
    print(*v, flush=True)

def month_ranges(start, end):
    while start <= end:
        last = min(end, date(start.year,start.month,calendar.monthrange(start.year,start.month)[1]))
        yield start, last
        start = last + timedelta(days=1)

class Source:
    def __init__(self):
        if '--offline' in sys.argv:
            from types import SimpleNamespace
            def unavailable(*args, **kwargs):
                raise RuntimeError('Missing cached data in offline mode')
            self.client=None
            self.calls=0
            self.expired=SimpleNamespace(get_expiries=unavailable,get_expired_option_contracts=unavailable,get_expired_historical_candle_data=unavailable)
            self.history=SimpleNamespace(get_historical_candle_data1=unavailable)
            self.options=SimpleNamespace(get_option_contracts=unavailable)
            return
        from dotenv import load_dotenv
        import upstox_client as sdk
        from app.repositories.tokens import get_token
        load_dotenv('.env')
        token = get_token(os.getenv('UPSTOX_LIVE_API_KEY') or os.getenv('UPSTOX_API_KEY'),os.getenv('ORB_BROKER_USER_ID',''))
        if token is None:
            raise RuntimeError('No configured account token')
        cfg=sdk.Configuration();cfg.access_token=token.access_token
        self.client=sdk.ApiClient(cfg)
        self.client.rest_client.pool_manager.connection_pool_kw['retries']=False
        self.expired=sdk.ExpiredInstrumentApi(self.client)
        self.history=sdk.HistoryV3Api(self.client)
        self.options=sdk.OptionsApi(self.client)
        self.calls=0

    def cached(self,key,method,*args,**kwargs):
        path=CACHE/(hashlib.sha256(key.encode()).hexdigest()+'.json')
        if path.exists():
            return json.loads(path.read_text())['data']
        for attempt in range(3):
            time.sleep(.22)
            try:
                response=method(*args,**kwargs,_request_timeout=(5,20))
                if response.status != 'success':
                    raise RuntimeError('Unsuccessful historical response')
                data=response.data
                if hasattr(data,'candles'):
                    data=data.candles
                elif isinstance(data,list):
                    data=[x.to_dict() if hasattr(x,'to_dict') else x for x in data]
                data=json.loads(json.dumps(data,default=str))
                path.write_text(json.dumps({'request':key,'data':data}))
                self.calls+=1
                return data
            except Exception as exc:
                # Read-only requests may retry. Never print tokens or HTTP headers.
                if attempt==2:
                    code=getattr(exc,'status',None)
                    raise RuntimeError(f'Historical request failed: {key}; type={type(exc).__name__}; status={code}') from None
                time.sleep(1+attempt)

    def close(self):
        if self.client is None:
            return
        self.client.rest_client.pool_manager.clear()
        self.client.pool.close();self.client.pool.join()

def timestamp(value):
    return datetime.fromisoformat(value).astimezone(IST)

def normalize(rows):
    found={}
    for row in rows:
        ts=timestamp(row[0])
        vals=tuple(float(v) for v in row[1:7])
        assert len(vals)>=5 and all(math.isfinite(v) for v in vals)
        o,h,l,c=vals[:4]
        assert 0<l<=min(o,c)<=max(o,c)<=h
        if ts in found:
            assert found[ts]==vals, f'Conflicting candle at {ts}'
        found[ts]=vals
    return dict(sorted(found.items()))

FILTERS={
    'base':dict(cutoff=dt_time(15),buffer=0,range_max=None,skip_expiry=False),
    'cutoff_11':dict(cutoff=dt_time(11),buffer=0,range_max=None,skip_expiry=False),
    'cutoff_12':dict(cutoff=dt_time(12),buffer=0,range_max=None,skip_expiry=False),
    'cutoff_13':dict(cutoff=dt_time(13),buffer=0,range_max=None,skip_expiry=False),
    'range_max_1.5pct':dict(cutoff=dt_time(15),buffer=0,range_max=.015,skip_expiry=False),
    'range_max_2pct':dict(cutoff=dt_time(15),buffer=0,range_max=.02,skip_expiry=False),
    'buffer_0.05pct':dict(cutoff=dt_time(15),buffer=.0005,range_max=None,skip_expiry=False),
    'buffer_0.1pct':dict(cutoff=dt_time(15),buffer=.001,range_max=None,skip_expiry=False),
    'skip_expiry':dict(cutoff=dt_time(15),buffer=0,range_max=None,skip_expiry=True),
}
EXITS=[('fixed_3_6',3,6,False),('fixed_4_8',4,8,False),('fixed_5_10',5,10,False),
       ('fixed_5_15',5,15,False),('fixed_7_14',7,14,False),('fixed_10_18',10,18,False),
       ('pct_20_40',.20,.40,True),('pct_25_50',.25,.50,True),('pct_30_60',.30,.60,True)]

def select_signal(day, candles, cfg, contracts):
    opening=candles[0][1]
    high,low=opening[1],opening[2]
    range_fraction=(high-low)/opening[0]
    if cfg['range_max'] and range_fraction>cfg['range_max']:
        return None
    expiries=sorted({x['expiry'][:10] for x in contracts if x['expiry'][:10]>=day.isoformat() and not x['weekly']})
    if not expiries:
        raise RuntimeError('Missing expiry metadata '+str(day))
    expiry=expiries[0]
    if cfg['skip_expiry'] and expiry==day.isoformat():
        return None
    for ts,bar in candles[1:]:
        signal_time=ts+timedelta(minutes=15)
        if signal_time.time()>cfg['cutoff']:
            break
        close=bar[3]
        direction='CE' if close>high*(1+cfg['buffer']) else 'PE' if close<low*(1-cfg['buffer']) else None
        if not direction:
            continue
        options=[x for x in contracts if x['expiry'][:10]==expiry and x['instrument_type']==direction and
                 (float(x['strike_price'])<close if direction=='CE' else float(x['strike_price'])>close)]
        if not options:
            raise RuntimeError(f'Missing ITM contract {day} {direction} {close}')
        option=min(options,key=lambda x:(abs(float(x['strike_price'])-close),x['instrument_key']))
        return dict(date=str(day),signal_time=signal_time.isoformat(),direction=direction,spot=close,
                    opening_high=high,opening_low=low,range_fraction=range_fraction,
                    instrument=option['instrument_key'],strike=option['strike_price'],expiry=expiry,
                    lot_size=option['lot_size'],quantity=2*option['lot_size'],symbol=option['trading_symbol'])
    return None

def fees(entry,exit_price,quantity,brokerage_per_order=30):
    buy,sell=entry*quantity,exit_price*quantity
    turnover=buy+sell
    brokerage=brokerage_per_order*2
    exchange=turnover*.0003553
    ipft=turnover*.000005
    sebi=turnover*.000001
    return brokerage+exchange+ipft+sebi+.18*(brokerage+exchange+ipft)+sell*.0015+buy*.00003

def simulate(signal, bars, exit_cfg, slip=.10):
    name,sd,td,pct=exit_cfg
    ts=timestamp(signal['signal_time'])
    end=ts.replace(hour=15,minute=25)
    if ts not in bars or bars[ts][4]<=0:
        return None,'missing_entry_minute'
    # Fill proxy: first post-signal minute's open plus adverse execution allowance.
    entry=round(math.ceil((bars[ts][0]+slip-1e-9)/.05)*.05,2)
    stop=round(math.floor(((entry*(1-sd) if pct else entry-sd)+1e-9)/.05)*.05,2)
    target=round(math.ceil(((entry*(1+td) if pct else entry+td)-1e-9)/.05)*.05,2)
    if stop<=0:
        return None,'nonpositive_stop'
    if (entry-stop)*signal['quantity']>10000+1e-7:
        return None,'planned_loss_above_10000'
    seen=ts-timedelta(minutes=1)
    missing=0
    ambiguous=False
    for moment,bar in bars.items():
        if moment<ts or moment.date()!=ts.date() or moment.time()>=dt_time(15,30) or bar[4]<=0:
            continue
        missing+=max(0,int((moment-seen).total_seconds()/60)-1)
        seen=moment
        o,h,l,c=bar[:4]
        if moment>=end:
            raw=o;reason='time';break
        if o<=stop:
            raw=o;reason='stop_gap';break
        if o>=target:
            raw=target;reason='target';break
        if l<=stop:
            ambiguous=h>=target
            raw=stop;reason='stop';break
        if h>=target:
            raw=target;reason='target';break
    else:
        return None,'missing_1525_exit'
    exit_price=round(max(.05,math.floor((raw-slip+1e-9)/.05)*.05),2)
    gross=round((exit_price-entry)*signal['quantity'],2)
    charges=round(fees(entry,exit_price,signal['quantity']),2)
    return dict(signal,exit_setting=name,entry=entry,stop=stop,target=target,exit=exit_price,
                exit_time=moment.isoformat(),exit_reason=reason,ambiguous_bar=ambiguous,
                timed_exit_delay_minutes=int((moment-end).total_seconds()/60) if reason=='time' else 0,
                missing_minutes_before_exit=missing,premium_outlay=round(entry*signal['quantity'],2),
                planned_loss=round((entry-stop)*signal['quantity'],2),gross=gross,charges=charges,net=round(gross-charges,2)),None

def metrics(trades):
    total=peak=dd=0
    for t in sorted(trades,key=lambda x:x['signal_time']):
        total+=t['net'];peak=max(peak,total);dd=max(dd,peak-total)
    wins=sum(t['net']>0 for t in trades)
    profit=sum(max(0,t['net']) for t in trades)
    loss=-sum(min(0,t['net']) for t in trades)
    return dict(trades=len(trades),gross=round(sum(t['gross'] for t in trades),2),
                charges=round(sum(t['charges'] for t in trades),2),net=round(total,2),
                win_rate=round(100*wins/len(trades),2) if trades else 0,
                profit_factor=round(profit/loss,3) if loss else None,max_drawdown=round(dd,2),
                max_premium_outlay=max((t['premium_outlay'] for t in trades),default=0),
                gap_trades=sum(t['missing_minutes_before_exit']>0 for t in trades),
                ambiguous_trades=sum(t['ambiguous_bar'] for t in trades))

def write_csv(name,rows):
    if not rows:
        return
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with (OUT/name).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields, lineterminator="\n");writer.writeheader();writer.writerows(rows)

def main():
    OUT.mkdir(parents=True,exist_ok=True);CACHE.mkdir(parents=True,exist_ok=True)
    source=Source()
    try:
        spot=[]
        for a,b in month_ranges(START,END):
            rows=source.cached(f'spot15:{a}:{b}',source.history.get_historical_candle_data1,UNDERLYING,'minutes','15',str(b),str(a))
            spot.extend(rows);emit('Underlying',a,b,'candles',len(rows))
        normalized=normalize(spot)
        days=defaultdict(list)
        for ts,bar in normalized.items():
            if START<=ts.date()<=END and dt_time(9,15)<=ts.time()<dt_time(15,30):
                days[ts.date()].append((ts,bar))
        invalid={str(d):len(cs) for d,cs in days.items() if len(cs)!=25 or cs[0][0].time()!=dt_time(9,15) or any(b[0]-a[0]!=timedelta(minutes=15) for a,b in zip(cs,cs[1:]))}
        assert not invalid, f'Incomplete underlying sessions: {invalid}'
        expiries=source.cached('expiries',source.expired.get_expiries,UNDERLYING)
        contracts=[]
        for expiry in expiries:
            if str(START)<=expiry<=str(END):
                rows=source.cached('contracts:'+expiry,source.expired.get_expired_option_contracts,UNDERLYING,expiry)
                contracts.extend(rows);emit('Contracts',expiry,len(rows))
        active=source.cached('active_contracts',source.options.get_option_contracts,UNDERLYING)
        contracts.extend(active)
        selections={}
        for filter_name,cfg in FILTERS.items():
            selections[filter_name]=[s for d,cs in sorted(days.items()) if (s:=select_signal(d,cs,cfg,contracts))]
        unique={ (s['date'],s['instrument']):s for values in selections.values() for s in values }
        emit('Coverage',len(days),'sessions;',len(unique),'unique day/contracts;', {k:len(v) for k,v in selections.items()})
        # Fetch one day per unique selected contract/date, avoiding irrelevant strikes.
        option_bars={}
        out_of_session=0
        for index,(key,s) in enumerate(sorted(unique.items()),1):
            instrument=s['instrument'];day=s['date']
            if len(instrument.split('|'))>=3:
                rows=source.cached(f'option1:{instrument}:{day}',source.expired.get_expired_historical_candle_data,instrument,'1minute',day,day)
            else:
                rows=source.cached(f'option1:{instrument}:{day}',source.history.get_historical_candle_data1,instrument,'minutes','1',day,day)
            normalized_options=normalize(rows)
            out_of_session+=sum(not (dt_time(9,15)<=ts.time()<dt_time(15,30)) for ts in normalized_options)
            option_bars[key]={ts:bar for ts,bar in normalized_options.items() if dt_time(9,15)<=ts.time()<dt_time(15,30) and ts.date().isoformat()==day}
            if index%10==0 or index==len(unique):
                emit('Option data',index,'/',len(unique))
        results=[];all_trades=[];all_skips=[];by_variant={}
        for filter_name,signals in selections.items():
            for exit_cfg in EXITS:
                variant=filter_name+'__'+exit_cfg[0]
                trades=[];skips=[]
                for signal in signals:
                    t,reason=simulate(signal,option_bars[(signal['date'],signal['instrument'])],exit_cfg)
                    if t:
                        t['variant']=variant;trades.append(t)
                    else:
                        skips.append(dict(variant=variant,date=signal['date'],reason=reason))
                by_variant[variant]=trades
                row=dict(variant=variant,filter=filter_name,exit_setting=exit_cfg[0],signals=len(signals),skipped=len(skips))
                for prefix,selected in [('all',trades),('train',[t for t in trades if t['date']<str(SPLIT)]),('test',[t for t in trades if t['date']>=str(SPLIT)])]:
                    row.update({prefix+'_'+k:v for k,v in metrics(selected).items()})
                results.append(row);all_trades.extend(trades);all_skips.extend(skips)
        # Selection uses training results only, requiring a minimum sample.
        eligible=[r for r in results if r['train_trades']>=20]
        winner=max(eligible,key=lambda r:r['train_net'])
        baseline=next(r for r in results if r['variant']=='base__fixed_5_10')
        current=next(r for r in results if r['variant']=='base__fixed_10_18')
        monthly=[]
        for variant in [baseline['variant'],current['variant'],winner['variant']]:
            for month in sorted({t['date'][:7] for t in by_variant[variant]}):
                monthly.append(dict(variant=variant,month=month,**metrics([t for t in by_variant[variant] if t['date'].startswith(month)])))
        sensitivity=[]
        for variant in dict.fromkeys([baseline['variant'],current['variant'],winner['variant']]):
            filter_name,exit_name=variant.split('__')
            exit_cfg=next(e for e in EXITS if e[0]==exit_name)
            for slip in [0,.05,.10,.25,.50]:
                trades=[]
                for s in selections[filter_name]:
                    t,_=simulate(s,option_bars[(s['date'],s['instrument'])],exit_cfg,slip)
                    if t:trades.append(t)
                sensitivity.append(dict(variant=variant,slippage_each_side=slip,**metrics(trades)))
        coverage=dict(start=str(START),end=str(END),train_end='2026-08-20',test_start=str(SPLIT),
                      sessions=len(days),train_sessions=sum(d<SPLIT for d in days),test_sessions=sum(d>=SPLIT for d in days),
                      first_session=str(min(days)),last_session=str(max(days)),spot_candles=len(normalized),
                      option_day_contracts=len(unique),option_candles=sum(len(b) for b in option_bars.values()),
                      empty_option_days=[list(k) for k,b in option_bars.items() if not b],
                      skip_counts=dict(Counter(s['reason'] for s in all_skips)),lot_sizes=sorted({s['lot_size'] for s in unique.values()}),
                      variants=len(results),new_api_calls=source.calls,out_of_session_option_bars_excluded=out_of_session)
        payload=dict(coverage=coverage,baseline=baseline,current=current,training_selected=winner,
                     ranked_by_training=sorted(results,key=lambda r:r['train_net'],reverse=True),monthly=monthly,sensitivity=sensitivity)
        (OUT/'results.json').write_text(json.dumps(payload,indent=2))
        write_csv('comparison.csv',results);write_csv('trades.csv',all_trades);write_csv('skipped.csv',all_skips)
        write_csv('monthly.csv',monthly);write_csv('slippage.csv',sensitivity)
        write_csv('signals.csv',[dict(s,filter=k) for k,values in selections.items() for s in values])
        emit('RESULT',json.dumps({k:payload[k] for k in ['coverage','baseline','current','training_selected']},indent=2))
    finally:
        source.close()

if __name__=='__main__':
    main()
