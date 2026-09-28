import ctypes, numpy as np
lib=ctypes.CDLL("libngspice.so.0"); OUT=[]
SC=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_void_p)
EX=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_int,ctypes.c_bool,ctypes.c_bool,ctypes.c_int,ctypes.c_void_p)
def _sc(s,i,p): OUT.append(s.decode(errors='ignore')); return 0
cb=(SC(_sc),SC(lambda s,i,p:0),EX(lambda a,b,c,d,p:0)); lib.ngSpice_Init(cb[0],cb[1],cb[2],None,None,None,None)
def load(n):
    L=n.strip().splitlines(); arr=(ctypes.c_char_p*(len(L)+1))(*[l.encode() for l in L],None)
    lib.ngSpice_Command(b"destroy all"); lib.ngSpice_Circ(arr)
def op(n,ex):
    load(n); lib.ngSpice_Command(b"op"); r={}
    for e in ex:
        OUT.clear(); lib.ngSpice_Command(("print %s"%e).encode()); x=[o for o in OUT if '=' in o]
        r[e]=float(x[-1].split('=')[-1].split()[0]) if x else None
    return r
def tran(n,vecs,f):
    load(n); lib.ngSpice_Command(b"run"); lib.ngSpice_Command(b"set wr_singlescale"); lib.ngSpice_Command(b"set wr_vecnames")
    lib.ngSpice_Command(("wrdata %s %s"%(f," ".join(vecs))).encode()); return np.genfromtxt(f,names=True)
M=""".model QN NPN(Is=2.04f Bf=250 Ne=1.5 Ise=5f Ikf=0.5 Br=3.4 Rb=50 Re=0.5 Rc=1 Vaf=100 Cjc=5.5p Cje=10p Tf=500p Tr=10n)
.model DL D(Is=3e-17 N=1.5 Rs=2)
.model LR D(Is=1e-20 N=1.6 Rs=5)
.model PM VDMOS(pchan Vto=-2.0 Kp=8 Rd=0.02 Rs=0.01 Rg=3 Cgdmax=300p Cgdmin=50p Cgs=800p Cjo=300p Is=1e-12)
"""
