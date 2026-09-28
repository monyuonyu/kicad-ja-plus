import sx,math
def libsyms(root):
    return {s[1]:s for s in sx.find(sx.one(root,"lib_symbols"),"symbol")}
def lib_pins(libsym):
    pins=[]
    def walk(x):
        for e in x:
            if isinstance(e,list):
                if e and e[0]=="pin":
                    at=sx.one(e,"at"); num=sx.one(e,"number")[1]; nm=sx.one(e,"name")[1]
                    pins.append((num,nm,float(at[1]),float(at[2])))
                else: walk(e)
    walk(libsym); return pins
def xform(sym,px,py):
    at=sx.one(sym,"at"); x,y=float(at[1]),float(at[2]); rot=int(float(at[3])) if len(at)>3 else 0
    mir=sx.one(sym,"mirror")
    # lib: y-up. mirror applied before rotation in KiCad? KiCad: transform = rotate then mirror. use eeschema convention
    X,Y=px,-py
    r=math.radians(-rot)
    c,s=round(math.cos(r)),round(math.sin(r))
    X,Y=X*c - Y*s, X*s + Y*c
    if mir:
        if mir[1]=="x": Y=-Y
        if mir[1]=="y": X=-X
    return round(x+X,3),round(y+Y,3)
def pins_abs(root):
    L=libsyms(root); out={}
    for s in sx.find(root,"symbol"):
        lid=sx.one(s,"lib_id")[1]; ref=sx.prop(s,"Reference")[2]
        for num,nm,px,py in lib_pins(L[lid]):
            out[(ref,num)]=(xform(s,px,py),nm)
    return out
def wire_pts(root):
    pts=[]
    for w in sx.find(root,"wire"):
        xy=sx.one(w,"pts"); a=[(round(float(p[1]),3),round(float(p[2]),3)) for p in sx.find(xy,"xy")]
        pts.append((a[0],a[1],w))
    return pts
