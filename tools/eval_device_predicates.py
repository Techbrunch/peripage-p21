import re, sys, json

import os
SRC = os.environ.get('Y6F', 'out/sources/y6/f.java')
src = open(SRC, encoding='utf-8', errors='replace').read()

# 1) String predicates:  public boolean NAME(String str) { ...  return EXPR; }
str_preds = {}
for m in re.finditer(r'public boolean (\w+)\(String str\) \{(.*?)\n    \}', src, re.S):
    name, body = m.group(1), m.group(2)
    rm = re.search(r'return ([^;]+);\s*$', body.strip(), re.S)
    if rm:
        str_preds[name] = rm.group(1)

# 2) No-arg delegators: public boolean NAME() { ... return OTHER(this.f51362c.getName()); }
delegators = {}
for m in re.finditer(r'public boolean (\w+)\(\) \{(.*?)\n    \}', src, re.S):
    name, body = m.group(1), m.group(2)
    rm = re.search(r'return (\w+)\(this\.f51362c\.getName\(\)\);', body)
    if rm:
        delegators[name] = rm.group(1)
        continue
    rm = re.search(r'return (\w+)\(\)\.startsWith', body)
    # composite no-arg like m1()
    rm2 = re.search(r'return ([^;]+);\s*$', body.strip(), re.S)
    if rm2 and 'f51362c' not in rm2.group(1):
        delegators[name] = ('EXPR', rm2.group(1))

def ev(expr, dev, depth=0):
    if depth > 40: raise RecursionError
    e = expr.replace('\n', ' ')
    e = re.sub(r'\s+', ' ', e).strip()
    # str.startsWith("X") / equals / equalsIgnoreCase / contains
    def rep_str(m):
        meth, arg = m.group(1), m.group(2)
        if meth == 'startsWith':        return str(dev.startswith(arg))
        if meth == 'contains':          return str(arg in dev)
        if meth == 'equals':            return str(dev == arg)
        if meth == 'equalsIgnoreCase':  return str(dev.lower() == arg.lower())
        return 'False'
    e = re.sub(r'str\.(startsWith|contains|equals|equalsIgnoreCase)\("([^"]*)"\)', rep_str, e)
    # "LITERAL".equals(str) / equalsIgnoreCase(str)
    e = re.sub(r'"([^"]*)"\.equalsIgnoreCase\(str\)', lambda m: str(dev.lower()==m.group(1).lower()), e)
    e = re.sub(r'"([^"]*)"\.equals\(str\)', lambda m: str(dev==m.group(1)), e)
    # nested predicate calls  NAME(str)  and NAME()
    def rep_call(m):
        n = m.group(1)
        if n in str_preds: return '(' + ev(str_preds[n], dev, depth+1) + ')'
        return 'UNKNOWN_' + n
    e = re.sub(r'\b(\w+)\(str\)', rep_call, e)
    def rep_noarg(m):
        n = m.group(1)
        if n in delegators:
            d = delegators[n]
            if isinstance(d, tuple): return '(' + ev(d[1], dev, depth+1) + ')'
            if d in str_preds:       return '(' + ev(str_preds[d], dev, depth+1) + ')'
        return 'UNKNOWN_' + n
    e = re.sub(r'\b(\w+)\(\)(?!\.)', rep_noarg, e)
    return e

def evaluate(name, dev):
    if name in str_preds:  expr = str_preds[name]
    elif name in delegators:
        d = delegators[name]
        expr = d[1] if isinstance(d, tuple) else str_preds.get(d, None)
        if expr is None: return 'no-body'
    else: return 'not-found'
    e = ev(expr, dev)
    e = e.replace('||', ' or ').replace('&&', ' and ').replace('!', ' not ')
    if 'UNKNOWN' in e: return 'UNRESOLVED: ' + e[:200]
    try:    return eval(e)
    except Exception as ex: return 'ERR ' + str(ex) + ' :: ' + e[:200]

if __name__ == '__main__':
    dev = sys.argv[1]
    names = sys.argv[2:]
    for n in names:
        print(f'{n:6} = {evaluate(n, dev)}')
