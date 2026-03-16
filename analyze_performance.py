#!/usr/bin/env python3
"""Quick performance analysis for HYDRA evaluation."""
import json

d = json.load(open('data/risk_state.json'))
trades = d.get('trade_history', [])

if not trades:
    print("No trades in risk state")
    exit()

total_pnl = sum(t['pnl'] for t in trades)
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]
avg_win = sum(t['pnl'] for t in wins)/len(wins) if wins else 0
avg_loss = sum(t['pnl'] for t in losses)/len(losses) if losses else 0
gross_profit = sum(t['pnl'] for t in wins)
gross_loss = abs(sum(t['pnl'] for t in losses))
pf = gross_profit / gross_loss if gross_loss > 0 else 0

print("=" * 50)
print("CUMULATIVE TRADE ANALYSIS (current session)")
print("=" * 50)
print(f"Total trades:    {len(trades)}")
print(f"Wins:            {len(wins)} ({len(wins)/len(trades)*100:.1f}%)")
print(f"Losses:          {len(losses)} ({len(losses)/len(trades)*100:.1f}%)")
print(f"Avg win:         ${avg_win:.2f}")
print(f"Avg loss:        ${avg_loss:.2f}")
print(f"Total PnL:       ${total_pnl:.2f}")
print(f"Gross profit:    ${gross_profit:.2f}")
print(f"Gross loss:      ${gross_loss:.2f}")
print(f"Profit factor:   {pf:.2f}")
print()

# By strategy
strats = {}
for t in trades:
    s = t.get('strategy', 'UNKNOWN')
    if s not in strats:
        strats[s] = {'wins': 0, 'losses': 0, 'pnl': 0, 'win_pnl': 0, 'loss_pnl': 0}
    strats[s]['pnl'] += t['pnl']
    if t['pnl'] > 0:
        strats[s]['wins'] += 1
        strats[s]['win_pnl'] += t['pnl']
    else:
        strats[s]['losses'] += 1
        strats[s]['loss_pnl'] += t['pnl']

print("--- BY STRATEGY ---")
for s, v in sorted(strats.items()):
    total = v['wins'] + v['losses']
    wr = v['wins']/total*100 if total > 0 else 0
    print(f"  {s}: {total} trades | WR: {wr:.0f}% | PnL: ${v['pnl']:.2f} (W:${v['win_pnl']:.2f} L:${v['loss_pnl']:.2f})")

print()

# Best and worst trades
best = max(trades, key=lambda t: t['pnl'])
worst = min(trades, key=lambda t: t['pnl'])
print(f"Best trade:  {best['symbol']} {best['strategy']} ${best['pnl']:+.2f} ({best['pnl_pct']:+.2f}%)")
print(f"Worst trade: {worst['symbol']} {worst['strategy']} ${worst['pnl']:+.2f} ({worst['pnl_pct']:+.2f}%)")

max_consec = d.get('max_consecutive_losses', 0)
print(f"Max consec losses: {max_consec}")
print(f"Peak equity:       ${d['peak_equity']:.2f}")
print(f"Current equity:    ${d['current_equity']:.2f}")
dd = (1 - d['current_equity']/d['peak_equity'])*100 if d['peak_equity'] > 0 else 0
print(f"Max drawdown:      {dd:.2f}% from peak")

# Expectancy
expectancy = total_pnl / len(trades)
print(f"Expectancy/trade:  ${expectancy:.3f}")
