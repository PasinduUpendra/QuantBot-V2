#!/usr/bin/env python3
"""Deep analysis of HYDRA bot performance."""
import json

d = json.load(open('data/risk_state.json'))
trades = d['trade_history']

winners = [t for t in trades if t['pnl'] > 0]
losers = [t for t in trades if t['pnl'] <= 0]

print(f"Total trades: {len(trades)}")
print(f"Winners: {len(winners)} | Avg duration: {sum(t['duration'] for t in winners)/max(1,len(winners))/60:.0f} min | Avg PnL: ${sum(t['pnl'] for t in winners)/max(1,len(winners)):.2f}")
print(f"Losers:  {len(losers)} | Avg duration: {sum(t['duration'] for t in losers)/max(1,len(losers))/60:.0f} min | Avg PnL: ${sum(t['pnl'] for t in losers)/max(1,len(losers)):.2f}")
print()

mom = [t for t in trades if t['strategy'] == 'MOMENTUM']
mr = [t for t in trades if t['strategy'] == 'MEAN_REV']
momw = [t for t in mom if t['pnl'] > 0]
moml = [t for t in mom if t['pnl'] <= 0]
mrw = [t for t in mr if t['pnl'] > 0]
mrl = [t for t in mr if t['pnl'] <= 0]

mom_pnl = sum(t['pnl'] for t in mom)
mr_pnl = sum(t['pnl'] for t in mr)
print(f"MOMENTUM: {len(mom)} trades | W:{len(momw)} L:{len(moml)} | PnL: ${mom_pnl:.2f} | WR: {len(momw)/max(1,len(mom))*100:.1f}%")
print(f"MEAN_REV: {len(mr)} trades | W:{len(mrw)} L:{len(mrl)} | PnL: ${mr_pnl:.2f} | WR: {len(mrw)/max(1,len(mr))*100:.1f}%")
print()

quick = [t for t in trades if t['duration'] < 1800]
quickw = [t for t in quick if t['pnl'] > 0]
quickl = [t for t in quick if t['pnl'] <= 0]
print(f"Quick trades (<30min): {len(quick)} | W:{len(quickw)} L:{len(quickl)} | PnL: ${sum(t['pnl'] for t in quick):.2f}")

medium = [t for t in trades if 1800 <= t['duration'] < 3600]
medw = [t for t in medium if t['pnl'] > 0]
medl = [t for t in medium if t['pnl'] <= 0]
print(f"Medium trades (30-60m): {len(medium)} | W:{len(medw)} L:{len(medl)} | PnL: ${sum(t['pnl'] for t in medium):.2f}")

long_t = [t for t in trades if t['duration'] >= 3600]
longw = [t for t in long_t if t['pnl'] > 0]
longl = [t for t in long_t if t['pnl'] <= 0]
print(f"Long trades (>1hr):    {len(long_t)} | W:{len(longw)} L:{len(longl)} | PnL: ${sum(t['pnl'] for t in long_t):.2f}")
print()

# By symbol
symbols = {}
for t in trades:
    sym = t['symbol']
    if sym not in symbols:
        symbols[sym] = {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0}
    symbols[sym]['trades'] += 1
    symbols[sym]['pnl'] += t['pnl']
    if t['pnl'] > 0:
        symbols[sym]['wins'] += 1
    else:
        symbols[sym]['losses'] += 1

print("=== BY SYMBOL ===")
for sym, s in sorted(symbols.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = s['wins'] / max(1, s['trades']) * 100
    print(f"  {sym:10s}: {s['trades']:3d} trades | W:{s['wins']:2d} L:{s['losses']:2d} | WR: {wr:5.1f}% | PnL: ${s['pnl']:+7.2f}")

print()
# Consecutive loss streaks
max_streak = 0
cur_streak = 0
for t in trades:
    if t['pnl'] <= 0:
        cur_streak += 1
        max_streak = max(max_streak, cur_streak)
    else:
        cur_streak = 0
print(f"Max consecutive losses: {max_streak}")
print(f"Current equity: ${d['current_equity']:.2f}")
print(f"Peak equity: ${d['peak_equity']:.2f}")
print(f"Return: {(d['current_equity']-1000)/1000*100:.2f}%")
