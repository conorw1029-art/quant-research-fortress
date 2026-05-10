import re
path='src/strategies/tsm.py'
with open(path,encoding='utf-8') as f: c=f.read()
c=c.replace('REBALANCE_FREQ = "M"','REBALANCE_FREQ = "ME"')
with open(path,'w',encoding='utf-8') as f: f.write(c)
print('TSM patched: M -> ME')
