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


# ---- 変えた所だけ書き換える(2026-10-29)
# dump は全体を自前の書式で書き直すので、値を 1 つ変えただけでも数千行が差分になり、改行コード(CRLF)も LF に
# なっていた。parse_keep で元の文字列と各要素の位置を覚え、dump_keep で「変わっていない要素は元の文字のまま」
# 書く。改行コードは元のまま(CRLF なら CRLF)。
def read_keep(path):
    """ファイル → (木, 覚え書き)。覚え書きは dump_keep に渡す"""
    with open(path, encoding="utf-8", newline="") as f:
        return parse_keep(f.read())


def parse_keep(text):
    crlf = "\r\n" in text
    s = text.replace("\r\n", "\n") if crlf else text
    pos = 0; stack = [([], [])]; starts = []
    while True:
        m = TOK.match(s, pos)
        if not m or m.end() == pos: break
        a = m.start(m.lastindex); pos = m.end()
        if m.group(1): stack.append(([], [])); starts.append(a)
        elif m.group(2):
            l, sp = stack.pop(); st = starts.pop()
            stack[-1][0].append(l); stack[-1][1].append((st, pos, sp))
        elif m.group(3) is not None:
            stack[-1][0].append(Q(unesc(m.group(3)[1:-1]))); stack[-1][1].append((a, pos, None))
        else:
            stack[-1][0].append(m.group(4)); stack[-1][1].append((a, pos, None))
    tree = stack[0][0][0]
    import copy
    return tree, {"text": s, "crlf": crlf, "tree": copy.deepcopy(tree), "span": stack[0][1][0]}


def dump_keep(new, keep):
    """keep(parse_keep の覚え書き)の元の文字列を土台に、new のうち変わった所だけ書き換えた文字列"""
    s, span = keep["text"], keep["span"]
    out = s[:span[0]] + _keep(s, keep["tree"], span, new) + s[span[1]:]
    return out.replace("\n", "\r\n") if keep["crlf"] else out


def write_keep(path, new, keep):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(dump_keep(new, keep))


def _indent(sep):
    return sep.rsplit("\n", 1)[-1].count("\t") if "\n" in sep else 0


def _keep(s, old, span, new):
    import difflib
    start, end, kids = span
    if not isinstance(new, list) or not isinstance(old, list) or kids is None:
        if old == new and type(old) is type(new):
            return s[start:end]
        return dump(new, _indent("\n" + s[:start].rsplit("\n", 1)[-1]))
    if old == new:
        return s[start:end]
    if not kids:
        return dump(new, 0)
    lead = s[start + 1:kids[0][0]]
    seps = [lead] + [s[kids[i - 1][1]:kids[i][0]] for i in range(1, len(kids))]
    tail = s[kids[-1][1]:end - 1]
    list_sep = next((seps[i] for i in range(1, len(kids)) if "\n" in seps[i]), None)
    if list_sep is None:
        list_sep = "\n" + "\t" * (_indent("\n" + s[:start].rsplit("\n", 1)[-1]) + 1)

    def text_of(o, n, i):
        return _keep(s, o, kids[i], n)

    def fresh(n):
        if isinstance(n, list):
            return list_sep, dump(n, _indent(list_sep))
        return " ", dump(n)

    parts = []   # (区切り, 文字)
    sm = difflib.SequenceMatcher(None, [dump(x) for x in old], [dump(x) for x in new], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                parts.append((seps[i1 + k], s[kids[i1 + k][0]:kids[i1 + k][1]]))
        elif op == "delete":
            continue
        elif op == "insert":
            parts += [fresh(n) for n in new[j1:j2]]
        else:   # replace: 同じ位置どうしは中へ(値 1 つの違いはそこだけ)、余りは足す/消す
            for k in range(j2 - j1):
                if k < i2 - i1:
                    parts.append((seps[i1 + k], text_of(old[i1 + k], new[j1 + k], i1 + k)))
                else:
                    parts.append(fresh(new[j1 + k]))
    if not parts:
        return "()"
    return "(" + lead + parts[0][1] + "".join(sep + t for sep, t in parts[1:]) + tail + ")"
