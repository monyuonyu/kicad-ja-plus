# 最小限の S式 パーサ/シリアライザ (KiCad用)
import re
TOK=re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')
class Q(str): pass   # quoted string
def unesc(t):
    out=[];i=0
    while i<len(t):
        c=t[i]
        if c=='\\' and i+1<len(t):
            n=t[i+1]; out.append({'n':'\n','"':'"','\\':'\\'}.get(n,'\\'+n)); i+=2
        else: out.append(c); i+=1
    return ''.join(out)
def parse(s):
    pos=0; stack=[[]]
    while True:
        m=TOK.match(s,pos)
        if not m or m.end()==pos: break
        pos=m.end()
        if m.group(1): stack.append([])
        elif m.group(2): l=stack.pop(); stack[-1].append(l)
        elif m.group(3) is not None: stack[-1].append(Q(unesc(m.group(3)[1:-1])))
        else: stack[-1].append(m.group(4))
    return stack[0][0]
def dump(x,ind=0):
    if isinstance(x,list):
        simple=all(not isinstance(e,list) for e in x)
        if simple: return "("+" ".join(dump(e) for e in x)+")"
        out="("+" ".join(dump(e) for e in x[:1] if not isinstance(e,list))
        rest=x[1:] if not isinstance(x[0],list) else x
        for e in rest:
            if isinstance(e,list): out+="\n"+"\t"*(ind+1)+dump(e,ind+1)
            else: out+=" "+dump(e)
        return out+"\n"+"\t"*ind+")"
    if isinstance(x,Q): return '"'+x.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n')+'"'
    return x
def find(x,name): return [e for e in x if isinstance(e,list) and e and e[0]==name]
def one(x,name):
    r=find(x,name); return r[0] if r else None
def prop(sym,key):
    for p in find(sym,"property"):
        if p[1]==key: return p
