local q=grid_led local ah=ipairs local ak=midi_note_off local al=midi_note_on local aK=math.floor local bn=math.max local bx=collectgarbage local cq=pcall local dF=pairs
current_page=1; menu_active=false; pages={}
function draw()
grid_led_all(0)
if pages[current_page] and pages[current_page].draw then pages[current_page]:draw() end
if menu_active then
for p=1,16 do q(p,8, p==current_page and 15 or (pages[p] and 4 or 0)) end
local pg=pages[current_page]; if pg.ce then pg:ce() end
end
q(16,8,menu_active and 15 or 6); grid_refresh()
end
event_grid=function(x,y,z)
if x==16 and y==8 then
if z==1 then menu_active=not menu_active end return
end
if menu_active then
local pg=pages[current_page]
if pg.bE and pg:bE(x,y,z) then return end
if y==8 and z==1 and pages[x] then current_page=x; menu_active=false end return
end
if pages[current_page] then pages[current_page]:bb(x,y,z) end
end
local aN=0; local a5=0; local aW=0
function event_midi(d1,d2,d3)
if d1==248 then
if SC.cb then return end
aN=aN+1; aW=aW+1; SC.bT=true
if pages[3] then pages[3]:dg() end
if pages[4] then pages[4]:df() end
if aW>=96 then aW=0; if pages[3] then pages[3]:c7() end
if SC.a1 then SC.bY=SC.a1; SC.a1=nil end end
local s=pages[2]
if s then
if s.a7 and aW==0 then
s.bp={}; s.ap=nil; s.a7=false; s.ab=1
for r=1,6 do s.L[r]=1 end; s:bA(); aN=0; a5=0; return
end
if s.ap then
a5=a5+1; if a5>=s.ct then
a5=0; s.bB=not s.bB; s.ab=s.ap
for r=1,6 do s.L[r]=((s.ap-1)%s.W[r])+1 end; s:bA()
end
if aN>=6 then aN=0 end
elseif aN>=6 then aN=0; a5=0; s:dh() end
end
elseif d1==250 or d1==251 then
aN=1; a5=0; aW=0
if pages[2] then pages[2].ab=1; for y=1,6 do pages[2].L[y]=1 end; pages[2]:bA() end
if pages[3] then pages[3].bw=0; pages[3].S=1; pages[3].aV=0; pages[3].a6=1; pages[3].aQ=0
if pages[3].Q then pages[3].E=1; pages[3]:bc() end
end
if pages[4] then pages[4]:dG() end
elseif d1==252 then
SC.bT=false
if pages[3] then pages[3]:aH(); pages[3]:ai() end
if pages[4] then pages[4]:cP() end
for n=0,127 do for c=1,7 do ak(n,0,c) end end
end
end
function framework_tick() for i=1,#pages do if pages[i].bO then pages[i]:bO() end end; SC:dJ(); draw() end
function vgv(g,i,b7)
if g<=1 then return b7 end
local n=g-1; local p=(i-1)%16; local sh
if n<=4 then local k=(n==1 and 4)or(n==2 and 2)or(n==3 and 8)or 3; sh=(p%k==0) and 1 or 0.15
elseif n<=8 then local m=n-4; local k=(m<=2 and 4)or(m==3 and 2)or 8; local o=(m==1 and 2)or(m==4 and 4)or 1; sh=((p-o)%k==0) and 1 or 0.2
elseif n<=12 then local f=n-8; sh=(math.sin(p/16*f*6.2832)+1)*0.5
else local f=n-12; local a=(p%4==0) and 1 or 0; local b=(math.sin((p+1)/16*f*6.2832)+1)*0.35; sh=(a>b) and a or b end
return aK(76+32*sh+0.5)
end
pages[1]={cj="perf",da=1,cC=20,FT=0.5,FD=0.03,bz=1,ck=5,
fv={},ft={},fs={},fr={},fl={},aG=0,bl=false,O=1,bg=5,
init=function(s)
for i=1,s.ck*8 do s.fv[i]=0;s.ft[i]=0;s.fs[i]=0;s.fr[i]=s.FT;s.fl[i]=-1 end
for f=1,8 do local i=f; s.fv[i]=102;s.ft[i]=102;s.fs[i]=102;s.fl[i]=102; s:c0(s.cC+i-1,102) end
end,
c0=function(s,dD,dQ) midi_cc(dD,dQ,s.da) end,
cK=function(s,f) return (s.bz-1)*8+f end,
dK=function(s,b) if b>=1 and b<=s.ck then s.bz=b end end,
dM=function(s,r,aO)
local ac=(aO-5)*s.O
if r==1 then local p=pages[3]; if p then p.a8=aO; p.bP=ac end
else local t=pages[4] and pages[4].tk[r-1]; if t then t.bt=aO; t.b4=ac end end
end,
c3=function(s)
for r=1,4 do
if r==1 then local p=pages[3]; if p then p.bP=(p.a8-5)*s.O end
else local t=pages[4] and pages[4].tk[r-1]; if t then t.b4=(t.bt-5)*s.O end end
end
if pages[4] then pages[4].bh=(s.bg-5)*s.O end
end,
bO=function(s)
s.aG=(s.aG+1)%16; s.bl=(s.aG<8)
for i=1,s.ck*8 do
if s.fr[i]<s.FT then
s.fr[i]=s.fr[i]+s.FD
local t=s.fr[i]/s.FT; if t>1 then t=1 end
local e=t*(2-t)
local v=s.fs[i]+(s.ft[i]-s.fs[i])*e; s.fv[i]=v
local iv=aK(v+0.5)
if iv~=s.fl[i] then s.fl[i]=iv; s:c0(s.cC+i-1,iv) end
end
end
end,
ce=function(s) for b=1,5 do q(16,b, b==s.bz and 15 or 4) end end,
bE=function(s,x,y,z) if z==1 and x==16 and y<=7 then s:dK(y);menu_active=false;return true end return false end,
draw=function(s)
if SC.b5 then SC:ds(s); SC:dt(s) else
local cT=pages[2] and pages[2].X
for x=1,8 do local h=1
if x<=6 then local m=cT and cT[x]; h=m and(s.bl and 15 or 3)or 6 end
q(x,1,h)
end
for y=2,5 do
local r=y-1; local D,cg
if r==1 then D=(pages[3] and pages[3].a8) or 5; cg=pages[3] and pages[3].aS
else local t=pages[4] and pages[4].tk[r-1]; D=(t and t.bt) or 5; cg=t and t.X end
for x=1,8 do local h
if x==1 then h=cg and(s.bl and 15 or 8)or 3
else h=(x==5) and 4 or 2; if x==D then h=15 end end
q(x,y,h)
end
end
for x=1,8 do q(x,6,(x==s.O) and 15 or 2) end
for x=1,8 do q(x,7,(x==s.bg) and 15 or((x==5) and 4 or 2)) end
for x=1,8 do q(x,8, x==1 and 8 or 1) end
end
for f=1,8 do
local i=s:cK(f); local x=8+f; local dy=aK(s.fv[i]/127*7+0.5)
for y=1,8 do local db=9-y; q(x,y,((db-1)<=dy) and 7 or 2) end
end
end,
dA=function(s,x,y,z)
if z~=1 then return end
local r=y-1
if x==1 then
if r==1 then local p=pages[3]; if p then p.aS=not p.aS; if p.aS then p:aH();p:ai() end end
else local t=pages[4] and pages[4].tk[r-1]; if t then t.X=not t.X end end
return
end
if x>=2 and x<=8 then s:dM(r,x) end
end,
bb=function(s,x,y,z)
if x>=9 and x<=16 then
if z~=1 then return end
local i=s:cK(x-8); s.fs[i]=s.fv[i]; s.ft[i]=aK((8-y)/7*127+0.5); s.fr[i]=0; return
end
if SC.b5 then SC:bb(x,y,z); return end
if y>=2 and y<=5 then s:dA(x,y,z); return end
if z~=1 then return end
if y==8 then if x==1 then SC.b5=true end return end
if y==1 then if x<=6 and pages[2] then pages[2].X[x]=not pages[2].X[x] end return end
if y==6 then s.O=x; s:c3(); return end
if y==7 then s.bg=x; if pages[4] then pages[4].bh=(x-5)*s.O end return end
end
}
pages[2]={cj="seq",G={},L={},W={},vg={},ab=1,bm=16,bp={},ap=nil,ct=6,bB=false,a7=false,cm={60,61,62,63,64,65},ch=2,be={},b2=false,X={},aF=1,bo={},bF={},ca=1,
init=function(s) for y=1,6 do s.G[y]={}; for x=1,16 do s.G[y][x]=false end; s.L[y]=1; s.W[y]=16; s.be[y]=nil; s.vg[y]=1 end; s.aF=1; s.ab=1; s.bm=16; s.bp={}; s.ap=nil; s.ct=6; s.bB=false; s.a7=false end,
cr=function(s) local m=1; for y=1,6 do if s.W[y]>m then m=s.W[y] end end; s.bm=m; if s.ab>s.bm then s.ab=1 end end,
cX=function(s) local b1=99; local cc=0; local c=0; for x=1,15 do if s.bp[x] then c=c+1; if x<b1 then b1=x end; if x>cc then cc=x end end end
if c==0 then s.ap=nil; s.a7=false else s.ap=b1; local sp=cc-b1; s.ct=(sp==0 and 6 or (sp==1 and 12 or (sp==2 and 8 or (sp==3 and 6 or (sp==4 and 4 or 3))))) end end,
bA=function(s) for y=1,6 do if s.G[y][s.L[y]] and not s.X[y] then local nt=s.cm[y];al(nt,vgv(s.vg[y],s.L[y],100),s.ch);s.bo[y]=nt;s.bF[y]=s.ca end end end,
dh=function(s) s.ab=s.ab+1; if s.ab>s.bm then s.ab=1 end; for y=1,6 do s.L[y]=s.L[y]+1; if s.L[y]>s.W[y] then s.L[y]=1 end end; s:bA() end,
bO=function(s) for y=1,6 do local c=s.bF[y]; if c then if c<=1 then ak(s.bo[y],0,s.ch);s.bF[y]=nil;s.bo[y]=nil else s.bF[y]=c-1 end end end end,
bE=function(s,x,y,z)
if z==1 and y>=1 and y<=6 then s.W[y]=16; for i=1,16 do s.G[y][i]=false end; s.L[y]=1; s:cr(); return true end
return false
end,
draw=function(s)
for y=1,6 do local l=s.W[y]; local p=s.L[y]; local cx=(y==s.aF)
for x=1,16 do local h=0
if x<=l then h=s.G[y][x] and 8 or (cx and 3 or 2); if x==1 or x==l then h=bn(h,cx and 6 or 4) end; if x==p then h=13 end end
q(x,y,h)
end
end
for x=1,16 do local h
if x==1 and s.ap~=nil then h=s.a7 and 15 or 4
else h=(x==s.vg[s.aF]) and 15 or (x==1 and 3 or 2) end
q(x,7,h)
end
for x=1,15 do local h=0; if x<=s.bm then if s.bp[x] then h=s.bB and 15 or 6 elseif x==s.ab and s.ap==nil then h=11 else h=4 end end; q(x,8,h) end; q(16,8,4)
end,
bb=function(s,x,y,z)
if y==8 then if x==16 then return end; if z==1 and x<=s.bm then s.bp[x]=true; s:cX(); if s.ap==x then s.ab=x; for r=1,6 do s.L[r]=((x-1)%s.W[r])+1 end; s.bB=true; s:bA() end else s.bp[x]=nil; s:cX() end return end
if y==7 then
if z~=1 then return end
if x==1 and s.ap~=nil then s.a7=true; return end
s.vg[s.aF]=x; return
end
if z==1 then if s.be[y]==nil then s.be[y]=x; s.b2=false else local st=s.be[y]; if st==1 and x>1 then s.W[y]=x; s.aF=y; s.b2=true; if s.L[y]>s.W[y] then s.L[y]=1 end; s:cr() end end
else if s.be[y]==x then if not s.b2 then s.G[y][x]=not s.G[y][x] end; s.be[y]=nil; s.b2=false end end
end
}
pages[3]={cj="arp",ch=3,aT={{64,69,62,67,60,65},{61,66,71,64,69,62},{71,66,63,68,63,70},{68,63,70,65,60,67}},
bq={0,2,4,5,7,9,11},br={0,2,3,5,7,8,10},
cD={{0},{0,1,4},{0,2,4},{0,3,4},{0,4},{0,2,4,5},{0,2,4,6},{0,2,4,6,8}},
c9={{0,4,7,10},{0,4,7,11},{0,3,6},{0,4,8},{0,4,6,10},{0,4,8,10},{0,1,4,7}},
cN={65535,21845,52428,18761,61166,28013,6745,4369,22875,26214,48059,27437,19609,27997,61713,63761},
ae=1,bs=5,cf=false,A=nil,aS=false,bP=0,a8=5,
b6={},aw={},aU={},I={},aD={},aC={},ar={},aq={},aB={},aM={},aR={},aE={},Y={},
dj={},J={},dn={},ob={},sm={5,4,3,2},
F=1,H=1,a3=1,a4=1,bR=false,aG=0,Q=true,
bL=nil,bJ=nil,bH=nil,bK=nil,bI=nil,N=nil,an=nil,
S=1,aV=0,a6=1,aQ=0,aZ=nil,a0=nil,U=0,V=1,bi=1,
b0={[1]=12,[2]=8,[3]=6,[4]=4},
E=1,bw=0,u=nil,bW=nil,bf=0,aL=1,cV=8,pn=8,aY=0,Z=0,
K=1,aj=60,aJ=false,bS=false,au=nil,
bX=false,bj=false,
c5={1,3,2,4,3,5,4,6},
init=function(s)
for y=1,8 do s.aU[y]=1;s.aD[y]=1;s.ar[y]=1;s.aq[y]=1;s.aB[y]=1;s.aC[y]=1;s.aM[y]=1;s.aR[y]=false;s.aE[y]=1;s.Y[y]=nil end
end,
bC=function(s,g,i) local m=s.cN[g] or s.cN[1]; return (m>>(i-1))&1==1 end,
cZ=function(s)
local r,mn
if s.K>=2 then r=s.aj; mn=s.aJ
elseif s.A then r=s:rv(s.A); mn=(s.A.x%2==0)
elseif s.I[s.E] then r=s.I[s.E]; mn=s.aR[s.E]
else r=60; mn=false end
r=54+((r-54)%12)
return r,(mn and s.br or s.bq)
end,
cY=function(s,d)
local r,iv=s:cZ()
return r+12*(d//7)+iv[(d%7)+1]
end,
dk=function(s,d) return d%7==0 end,
dC=function(s,n)
local r,iv=s:cZ()
local bM=n-r; local cp=bM//12; local pc=bM%12
local bQ,bd=99,0
for di=0,6 do local dd=math.abs(iv[di+1]-pc); if dd<bQ then bQ=dd;bd=di end end
local dw=math.abs(12-pc); if dw<bQ then bQ=dw;bd=7 end
return cp*7+bd
end,
dl=function(s,cm,T)
local af=s.dn; for i=#af,1,-1 do af[i]=nil end
for _,n in ah(cm) do af[#af+1]=s:dC(n) end
table.sort(af)
for i=2,#af do if af[i]<=af[i-1] then af[i]=af[i-1]+1 end end
local bG=s.ob; for i=#bG,1,-1 do bG[i]=nil end
for _,d in ah(af) do local nn=s:cY(d+T); if nn>=0 and nn<=127 then bG[#bG+1]=nn end end
return bG
end,
rv=function(s,lr)
if not lr then return nil end
return s.aT[lr.x] and s.aT[lr.x][lr.y]
end,
cF=function(s,dB) local iv=s.aJ and s.br or s.bq;local bM=dB-s.aj;local cp=bM//12;local ac=bM%12;local b=1;local bd=99;for i=1,7 do local dd=iv[i]-ac;if dd<0 then dd=-dd end;if dd<bd then bd=dd;b=i end end;return cp*7+(b-1) end,
cO=function(s,x,y) local iv=s.aJ and s.br or s.bq;local d=(x-1)*6+(6-y);return s.aj+12*(d//7)+iv[(d%7)+1] end,
a9=function(s,r,m,mn)
local p=s.dj; for i=#p,1,-1 do p[i]=nil end
if not r or s.aS then return p end
local ko=12*s.aY
local lk=s.K>=2 and s.aj
if m>8 then local rr=r
if lk then local iv=s.aJ and s.br or s.bq;local d=s:cF(r);rr=s.aj+12*(d//7)+iv[(d%7)+1] end
for _,i in ah(s.c9[m-8]) do p[#p+1]=rr+i+ko end
elseif lk then local iv=s.aJ and s.br or s.bq;local d0=s:cF(r)
for _,cd in ah(s.cD[m]) do local d=d0+cd;p[#p+1]=s.aj+12*(d//7)+iv[(d%7)+1]+ko end
else local iv=mn and s.br or s.bq
for _,d in ah(s.cD[m]) do p[#p+1]=r+12*(d//7)+iv[(d%7)+1]+ko end
end
if #p>s.bs then for i=#p,s.bs+1,-1 do p[i]=nil end end
local T=(s.bP or 0)+((pages[4] and pages[4].bh) or 0)
if T~=0 then p=s:dl(p,T) end
local iv=s.Z or 0
if iv~=0 and #p>0 then local n=#p
for _=1,(iv>0 and iv or -iv) do
if iv>0 then local mi=1;for j=2,n do if p[j]<p[mi] then mi=j end end;p[mi]=p[mi]+12
else local ma=1;for j=2,n do if p[j]>p[ma] then ma=j end end;p[ma]=p[ma]-12 end
end
end
return p
end,
cM=function(s,r) if r then for x=1,4 do for y=1,6 do if s.aT[x][y]==r then return x,y end end end end end,
cS=function(s,rv,mn) if s.K==3 then return s.J end;return s:a9(rv,s.ae,mn) end,
c1=function(s,r) if s.aE[r]==3 and s.Y[r] then return s.Y[r] end;return s:a9(s.I[r],s.aD[r],s.aR[r]) end,
cB=function(s,p,d,st,c2)
if #p==0 then return nil end;local l=#p;local pi
if d==1 then pi=p[((st-1)%l)+1] elseif d==2 then pi=p[l-((st-1)%l)]
elseif d==3 then local cy=l*2;local ph=(st-1)%cy;pi=ph<l and p[ph+1] or p[cy-ph]
elseif d==4 then local sq=s.c5;pi=p[((sq[((st-1)%#sq)+1]-1)%l)+1] end
if c2 and st%2==0 and pi then pi=pi+12 end;return pi
end,
aH=function(s) for n in dF(s.b6) do ak(n,0,s.ch);s.b6[n]=nil end;if s.aZ then ak(s.aZ,0,s.ch);s.aZ=nil end end,
ai=function(s) for n in dF(s.aw) do ak(n,0,s.ch);s.aw[n]=nil end;if s.a0 then ak(s.a0,0,s.ch);s.a0=nil end end,
cl=function(s,v) local g=2+(v-76)//8; return g<2 and 2 or(g>6 and 6 or g) end,
cH=function(s,r) s:aH();local v=vgv(s.aM[r] or s.aL,s.S,95);for _,n in ah(s:c1(r)) do al(n,v,s.ch);s.b6[n]=true end;s.au=s:cl(v) end,
bc=function(s) s:aH();if not s.Q then return end;if (s.ar[s.E] or 1)==1 and (s.aq[s.E] or 1)==1 and s.I[s.E] then s:cH(s.E) end end,
c4=function(s)
if s.N then
local ql=s.N;s.N=nil;s.an=nil;s:ai();s.ae=ql.m
local qt=ql.t
if qt==1 and ql.s==1 then s.au=nil;for _,n in ah(s.K==3 and s.J or s:a9(ql.rv,ql.m,ql.mn)) do al(n,105,s.ch);s.aw[n]=true end
elseif qt==1 and ql.s>1 then s.U=999;s.V=1
elseif qt==2 or qt==3 then s.U=999;s.bi=1;s.V=1 end
elseif s.an then
local qm=s.an;s.an=nil;s.ae=qm;s:ai()
local rv=s:rv(s.A)
if s.F==1 and s.H==1 then
if rv then for _,n in ah(s:a9(rv,qm,s.A and s.A.x%2==0)) do al(n,105,s.ch);s.aw[n]=true end end
elseif s.F==1 and s.H>1 then s.U=999;s.V=1
elseif s.F==2 or s.F==3 then s.U=999;s.bi=1;s.V=1 end
end
end,
dO=function(s,rv)
local mn=s.A and(s.A.x%2==0)or false
if s.F==1 and s.H>1 then
s.U=s.U+1
if s.U>=s.b0[s.H] then
s.U=0;s:ai()
if s:bC(s.a4,s.V) then local v=vgv(s.aL,s.V,105);for _,n in ah(s:cS(rv,mn)) do al(n,v,s.ch);s.aw[n]=true end;s.au=s:cl(v) end
s.V=(s.V%16)+1
end
elseif s.F==2 or s.F==3 then
s.U=s.U+1
if s.U>=s.b0[s.H] then
s.U=0
if s:bC(s.a4,s.V) then
if s.a0 then ak(s.a0,0,s.ch);s.a0=nil end
local p=s:cB(s:cS(rv,mn),s.a3,s.bi,s.F==3)
if p then local v=vgv(s.aL,s.V,105);al(p,v,s.ch);s.a0=p;s.au=s:cl(v) end
s.bi=s.bi+1
end
s.V=(s.V%16)+1
end
end
end,
dg=function(s)
if aW%24==0 then s:c4() end
local rv=s:rv(s.A)
if rv then s:dO(rv) end
if s.Q and not s.u and not s.bX then
local t=s.ar[s.E] or 1;local sp=s.aq[s.E] or 1;local d=s.aB[s.E] or 1
if t==1 and sp>1 then
s.aV=s.aV+1
if s.aV>=s.b0[sp] then s.aV=0;s:aH();if s:bC(s.aC[s.E],s.S) then s:cH(s.E) end;s.S=(s.S%16)+1 end
elseif t==2 or t==3 then
s.aQ=s.aQ+1
if s.aQ>=s.b0[sp] then
s.aQ=0
if s:bC(s.aC[s.E],s.S) then
if s.aZ then ak(s.aZ,0,s.ch);s.aZ=nil end
local p=s:cB(s:c1(s.E),d,s.a6,t==3)
if p then local v=vgv(s.aM[s.E] or s.aL,s.S,98);al(p,v,s.ch);s.aZ=p;s.au=s:cl(v) end;s.a6=s.a6+1
end
s.S=(s.S%16)+1
end
end
end
end,
c7=function(s)
local qc=false
if s.bL then s.F=s.bL;s.bL=nil;qc=true end
if s.bJ then s.H=s.bJ;s.bJ=nil;qc=true end
if s.bH then s.a3=s.bH;s.bH=nil;qc=true end
if s.bK then s.bs=s.bK;s.bK=nil;qc=true end
if s.bI then s.a4=s.bI;s.bI=nil;qc=true end
if qc then s.U=999;s.V=1;s.bi=1 end
if s.bj then
s.bj=false;s.bX=false
s.S=1;s.a6=1;s.aV=0;s.aQ=0;s:bc()
end
if not s.Q then return end
s.bw=s.bw+1
if s.bw>=(s.aU[s.E] or 1) then
s.bw=0;local le=1;for r=1,8 do if s.I[r] then le=r end end
s.E=(s.E%le)+1;s.S=1;s.a6=1;s.aV=0;s.aQ=0;s:bc()
end
end,
bO=function(s)
s.aG=(s.aG+1)%48;s.bR=(s.aG%16<8)
local ts=s.aG/48; local dP=ts<0.5 and ts*2 or 2-ts*2; s.cV=8+aK(dP*7+0.5)
local tn=(s.aG%10)/10; local cu=tn<0.5 and tn*2 or 2-tn*2; local en=cu*cu*(3-2*cu); s.pn=5+aK(en*10+0.5)
if s.bW then s.bf=s.bf+1;if s.bf>14 then s.bW=nil;s.bf=0 end end
if s.au then if s.au<=1 then s:aH();s:ai();s.au=nil else s.au=s.au-1 end end
end,
draw=function(s)
local tg=s.u or s.E;local cI,cJ=s:cM(s.I[tg])
local at=s.u and s.ar[s.u] or (s.bL or s.F)
local ay=s.cV
local cQ,cR; if s.K==2 then cQ,cR=s:cM(s.aj) end
for y=1,6 do for x=1,4 do
local h
if s.K==3 then
h=(((x-1)*6+(6-y))%7==0) and (s.bR and 15 or 6) or 5
local gn=s:cO(x,y);for i=1,#s.J do if s.J[i]==gn then h=15;break end end
else
h=(x==1 or x==3) and 5 or 3
if s.u then if cI==x and cJ==y and at~=4 then h=s.pn end
elseif s.Q and cI==x and cJ==y and at~=4 then h=s.pn end
if cQ==x and cR==y then h=s.bR and 15 or 4 end
if s.A and y==s.A.y and x==s.A.x then h=15 end
end
q(x,y,h)
end end
local sm=s.sm;local c6=s.bK or s.bs
for x=5,8 do
local v=x-4;local as=s.u and s.aq[s.u] or(s.bJ or s.H);local ad=s.u and s.aB[s.u] or(s.bH or s.a3)
q(x,1,at==v and ay or 0)
q(x,2,as==v and ay or 0)
q(x,3,ad==v and ay or 0)
q(x,4,(sm[x-4]==c6) and ay or 0)
end
local ag=s.u and s.aC[s.u] or(s.bI or s.a4)
for y=5,8 do for x=5,8 do local aX=((y-5)*4)+(x-4)
q(x,y, aX==ag and (s:bC(ag,s.S) and 15 or 7) or 6) end end
local bV=(s.N and s.N.m) or s.an
local b3=s.ae;local cs=b3<=8 and b3 or b3-7
local cG=bV and(bV<=8 and bV or bV-7)
local av=s.aD[tg];local cz=av and(av<=8 and av or av-7)
for y=7,8 do for x=1,4 do
local mi=((y-7)*4)+x;local h=0
if s.u then if cz==mi and at~=4 then h=ay end
elseif s.Q and cz==mi and at~=4 then h=ay end
if mi==cs then h=15 end
if b3>8 and mi==1 and cs~=1 then h=ay end
if cG and mi==cG and mi~=cs then h=ay end
q(x,y,h)
end end
if s.N and s.N.co then local co=s.N.co;q(co.x,co.y,ay) end
for y=1,8 do
local dv=s.I[y]~=nil; local bU=s.aU[y] or 1
for x=9,12 do local aO=x-8; local h
if not dv then h=1 elseif aO<bU then h=3 elseif aO==bU then h=10 else h=2 end
if y==s.E and s.Q then if aO==bU then h=ay elseif aO<bU then h=5 end end
if s.bW==y and s.bf%3<2 then h=15 end
q(x,y,h)
end
end
local cE=s.u and s.aM[s.u] or s.aL
for y=1,4 do for x=13,16 do local aX=((y-1)*4)+(x-12); local h
if aX==cE then local cv=vgv(cE,s.S,100); h=4+aK((cv-76)/32*11+0.5); if h<4 then h=4 elseif h>15 then h=15 end
else h=(aX==1) and 3 or 2 end
q(x,y,h)
end end
for y=5,7 do q(13,y,0) end
q(14,5, s.K==1 and 15 or 3)
q(14,6, s.K==2 and 15 or (s.bS and 9 or 6))
q(14,7, s.K==3 and 15 or 3)
q(16,5, s.aY==1 and 15 or 3)
q(16,6, s.aY==0 and 12 or 5)
q(16,7, s.aY==-1 and 15 or 3)
q(15,6, 6)
q(15,5, s.Z>0 and math.min(15,6+s.Z*3) or 2)
q(15,7, s.Z<0 and math.min(15,6-s.Z*3) or 2)
q(13,8, s.Q and 6 or (s.bR and 15 or 2))
q(14,8,0)
end,
bE=function(s,x,y,z)
if z==1 and x>=9 and x<=12 and y>=1 and y<=8 then
s.I[y]=nil;s.aU[y]=1;s.aD[y]=1;s.ar[y]=1;s.aq[y]=1;s.aB[y]=1;s.aC[y]=1;s.aE[y]=1;s.Y[y]=nil;return true
end
return false
end,
bb=function(s,x,y,z)
if x>=9 and x<=12 and y<=8 then
if z==1 then
if s.K==3 then
local C=s.Y[y]; if not C then C={};s.Y[y]=C end
for i=#C,1,-1 do C[i]=nil end
for i=1,#s.J do if i<=6 then C[i]=s.J[i] end end
s.aE[y]=3; s.I[y]=C[1]
else
local rv=s:rv(s.A)
s.I[y]=rv or s.I[y]; s.aE[y]=1; s.Y[y]=nil
if s.A then s.aR[y]=(s.A.x%2==0) end
s.aD[y]=(s.N and s.N.m) or s.an or s.ae
end
s.ar[y]=s.F;s.aq[y]=s.H;s.aB[y]=s.a3;s.aC[y]=s.a4;s.aM[y]=s.aL;s.aU[y]=x-8
s.bW=y;s.bf=0;s.u=y
else if s.u==y then s.u=nil;s:bc() end end;return
end
if x>=13 and x<=16 and y<=4 then if z==1 then local aX=((y-1)*4)+(x-12); if s.u then s.aM[s.u]=aX else s.aL=aX end end;return end
if x==16 and y>=5 and y<=7 then if z==1 then s.aY=(y==5 and 1) or (y==6 and 0) or -1 end;return end
if x==15 and y>=5 and y<=7 then if z==1 then if y==6 then s.Z=0 elseif y==5 then s.Z=math.min(6,s.Z+1) else s.Z=bn(-6,s.Z-1) end end;return end
if x==14 and y>=5 and y<=7 then
if z==1 then s:ai();for i=#s.J,1,-1 do s.J[i]=nil end;s.A=nil;s.N=nil
if y==5 then s.K=1 elseif y==6 then s.K=2;s.bS=true else s.K=3 end
else if y==6 then s.bS=false end end
return
end
if x>=5 and x<=8 and y<=4 then
if z==1 then
local v=x-4;local tg=s.u or s.E
if y==4 then v=s.sm[v] end
if s.u then
if y==1 then s.ar[tg]=v;if v==4 then s:aH() end elseif y==2 then s.aq[tg]=v elseif y==3 then s.aB[tg]=v end
else
if y==1 then s.bL=v elseif y==2 then s.bJ=v elseif y==3 then s.bH=v elseif y==4 then s.bK=v end
end
end;return
end
if x>=5 and x<=8 and y>=5 and y<=8 then
if z==1 then local g=((y-5)*4)+(x-4);if s.u then s.aC[s.u]=g else s.bI=g end end;return
end
if x==13 and y==8 then if z==1 then s.Q=not s.Q;if s.Q then s:bc() else s:aH() end end;return end
if y>=7 and x<=4 then
local m=((y-7)*4)+x
if z==1 then
local mv=(s.cf and m>1) and (8+(m-1)) or m
if m==1 then s.cf=true end
if s.u then s.aD[s.u]=mv end
if s.F==1 and s.H==1 then
s.ae=mv;s.an=nil;s:ai()
if s.A then
local rv=s:rv(s.A)
for _,n in ah(s:a9(rv,mv,s.A.x%2==0)) do al(n,105,s.ch);s.aw[n]=true end
end
elseif s.N then s.N.m=mv;s.an=nil
else s.an=mv end
else
if m==1 then s.cf=false end
end;return
end
if x<=4 and y<=6 then
if s.K==3 then
local n=s:cO(x,y)
if s.u then
if z==1 then local st=s.u; local C=s.Y[st]; if not C then C={};s.Y[st]=C end
local f=false; for i=1,#C do if C[i]==n then table.remove(C,i);f=true;break end end
if not f and #C<6 then C[#C+1]=n end
if #C==0 then s.aE[st]=1;s.Y[st]=nil;s.I[st]=nil else s.aE[st]=3;s.I[st]=C[1] end
end
return
end
if z==1 then
local f=false;for i=1,#s.J do if s.J[i]==n then f=true;break end end
if not f then s.J[#s.J+1]=n end
if not s.A then
s.A={x=x,y=y};s.bX=true;s.bj=false
if s.F==1 and s.H==1 then for _,nn in ah(s.J) do al(nn,105,s.ch);s.aw[nn]=true end
else s.N={rv=n,t=s.F,s=s.H,m=s.ae,mn=false,co={x=x,y=y}} end
elseif s.F==1 and s.H==1 and not f then al(n,105,s.ch);s.aw[n]=true end
else
for i=1,#s.J do if s.J[i]==n then table.remove(s.J,i);break end end
if s.F==1 and s.H==1 then ak(n,0,s.ch);s.aw[n]=nil end
if #s.J==0 then s.A=nil;s.N=nil;s:ai();s.bj=true end
end
return
end
if z==1 then
if s.bS then s.aj=s.aT[x] and s.aT[x][y];s.aJ=(x%2==0);return end
local rv=s.aT[x] and s.aT[x][y]
if rv then
if s.K==1 then s.aj=rv;s.aJ=(x%2==0) end
if s.u then s.I[s.u]=rv;s.aR[s.u]=(x%2==0);s.aD[s.u]=s.ae;s.ar[s.u]=s.F;s.aq[s.u]=s.H;s.aB[s.u]=s.a3 end
s.A={x=x,y=y};s.bX=true;s.bj=false
local pm=s.an or s.ae;s.an=nil
if s.F==1 and s.H==1 then
for _,n in ah(s:a9(rv,s.ae,x%2==0)) do al(n,105,s.ch);s.aw[n]=true end
else s.N={rv=rv,t=s.F,s=s.H,m=pm,mn=(x%2==0),co={x=x,y=y}} end
end
else
if s.A and s.A.x==x and s.A.y==y then
s.A=nil;s.N=nil
s:ai()
s.bj=true
end
end
end
end
}
pages[4]={cj="mseq",dc=4,cU=16,R=1,bh=0,fc=0,bl=false,tk={},
init=function(s)
for k=1,3 do s.tk[k]={G={},aa=1,aI=16,D=1,aP=0,X=false,bt=5,b4=0,vg=1,bN=8,b9=6,pc=0,a2=nil,bk=nil,bZ=nil} end
s.bh=0; s.fc=0
end,
aA=function(s) return s.tk[s.R] end,
b8=function(s,k) return s.dc+k-1 end,
cL=function(s,k)
local t=s.tk[k]
if t.a2 then ak(t.a2,0,s:b8(k)); t.a2=nil end
if t.X then return end
local d=t.G[t.D]
if d~=nil then
local n=pages[3]:cY(d + t.b4 + s.bh)
if n and n>=0 and n<=127 then al(n,vgv(t.vg,t.D,100),s:b8(k)); t.a2=n end
end
end,
cP=function(s) for k=1,3 do local t=s.tk[k]; if t.a2 then ak(t.a2,0,s:b8(k)); t.a2=nil end end end,
df=function(s) for k=1,3 do local t=s.tk[k]; t.pc=t.pc+1; if t.pc>=t.b9 then t.pc=0; t.D=t.D+1; if t.D>t.aI or t.D<t.aa then t.D=t.aa end; s:cL(k) end end end,
dG=function(s) for k=1,3 do local t=s.tk[k]; t.pc=0; t.D=t.aa; s:cL(k) end end,
bO=function(s) s.fc=s.fc+1; if s.fc>=16 then s.fc=0 end; s.bl=s.fc<8 end,
dL=function(s,D)
local t=s:aA(); t.bN=D; local ac=D-8
local O=ac>=0 and (1+0.5*ac) or 1/(1+0.5*(-ac))
t.b9=bn(1,aK(6/O+0.5))
end,
de=function(s) local t=s:aA(); t.G={}; t.aa=1; t.aI=16; t.D=1 end,
cW=function(s,x,y) local t=s:aA(); local ba=t.aP+(7-y)
if t.G[x]==ba then t.G[x]=nil else t.G[x]=ba end end,
ce=function(s)
local t=s:aA(); local bl=s.bl
for k=1,3 do q(16,k, k==s.R and 15 or 4) end
for x=1,15 do local h=(x==8) and 4 or 2; if x==t.bN then h=bl and 15 or 8 end; q(x,1,h) end
for x=1,16 do local h=(x==1) and 3 or 2; if x==t.vg then h=bl and 15 or 8 end; q(x,7,h) end
q(15,5, bl and 9 or 4); q(15,6, bl and 9 or 4)
q(14,6, bl and 12 or 5)
end,
bE=function(s,x,y,z)
if x==16 and y<=3 then if z==1 then s.R=y end; return true end
if y==1 and x<=15 then if z==1 then s:dL(x) end; return true end
if y==7 then if z==1 then s.tk[s.R].vg=x end; return true end
if x==15 and y==5 then if z==1 then s:aA().aP=s:aA().aP+1 end; return true end
if x==15 and y==6 then if z==1 then s:aA().aP=s:aA().aP-1 end; return true end
if x==14 and y==6 then if z==1 then s:de() end; return true end
if y>=2 and y<=6 then if z==1 then s:cW(x,y) end; return true end
return false
end,
draw=function(s)
local t=s:aA()
for x=1,s.cU do
local dx=(x>=t.aa and x<=t.aI)
for y=1,7 do
local ba=t.aP+(7-y); local h=0
if pages[3]:dk(ba) then h=2 end
if dx then h=bn(h,1) end
if t.G[x]==ba then h=8 end
if x==t.D then h=bn(h,(t.G[x]==ba) and 15 or 5) end
q(x,y,h)
end
local l8=2
if x==t.aa or x==t.aI then l8=8 elseif x>t.aa and x<t.aI then l8=4 end
if x==t.D then l8=bn(l8,11) end
q(x,8,l8)
end
end,
bb=function(s,x,y,z)
local t=s:aA()
if y==8 then
if z==1 then
if t.bk==nil then t.bk=x
else t.bZ=x; local a,b=t.bk,t.bZ; if a>b then a,b=b,a end
t.aa=a; t.aI=b; if t.D<a or t.D>b then t.D=a end
end
else
if t.bZ~=nil then t.bk=nil; t.bZ=nil elseif t.bk==x then t.bk=nil end
end
return
end
if z==1 and x>=1 and x<=s.cU and y>=1 and y<=7 then s:cW(x,y) end
end
}
SC={b5=false,R=nil,by=nil,a1=nil,bY=nil,cb=false,bT=false,am=0,bu=127,bv=32,cw=1,bD=false,mt=0,ax={},c8={},dz={},
cn=function() return (SC.bu+SC.bv-1)//SC.bv end,
dE=function()
local B=SC.c8; for i=#B,1,-1 do B[i]=nil end; local n=0; local sf=string.format
local function w(v) if v<0 then v=0 elseif v>255 then v=255 end; n=n+1; B[n]=sf("%02x",v&255) end
local p1,p2,p3,p4=pages[1],pages[2],pages[3],pages[4]
w(4)
w(p1.bz); w(p1.O); w(p1.bg)
for i=1,40 do w(aK((p1.ft[i] or 0)+0.5)) end
for y=1,6 do local m=0; for x=1,16 do if p2.G[y][x] then m=m|(1<<(x-1)) end end; w(m&255); w((m>>8)&255) end
for y=1,6 do w(p2.W[y]) end
for y=1,6 do w(p2.vg[y]) end
local mm=0; for y=1,6 do if p2.X[y] then mm=mm|(1<<(y-1)) end end; w(mm); w(p2.ca); w(p2.aF)
w(p3.ae);w(p3.bs);w(p3.F);w(p3.H);w(p3.a3);w(p3.a4);w(p3.aL)
w(p3.aY+1);w(p3.Z+8);w(p3.K);w(p3.aj);w(p3.aJ and 1 or 0);w(p3.a8);w(p3.aS and 1 or 0);w(p3.Q and 1 or 0)
w(p3.A and p3.A.x or 0); w(p3.A and p3.A.y or 0)
for i=1,8 do
w(p3.I[i] or 0);w(p3.aD[i] or 1);w(p3.aR[i] and 1 or 0);w(p3.ar[i] or 1)
w(p3.aq[i] or 1);w(p3.aB[i] or 1);w(p3.aC[i] or 1);w(p3.aM[i] or 1);w(p3.aU[i] or 1)
w(p3.aE[i] or 1)
local C=p3.Y[i]; for j=1,6 do w(C and C[j] or 0) end
end
for k=1,3 do local t=p4.tk[k]
for x=1,16 do local d=t.G[x]; w(d==nil and 255 or (d+100)) end
w(t.aa);w(t.aI);w(t.aP+100);w(t.X and 1 or 0);w(t.bt);w(t.vg);w(t.bN)
end
return {table.concat(B)}
end,
unpack=function(P)
local s=P and P[1]; if type(s)~="string" then return false end
local ci=-1; local function r() ci=ci+2; return tonumber(s:sub(ci,ci+1),16) or 0 end
if r()~=4 then return false end
local p1,p2,p3,p4=pages[1],pages[2],pages[3],pages[4]
p1.bz=r(); p1.O=r(); p1.bg=r()
for i=1,40 do p1.ft[i]=r(); p1.fs[i]=p1.fv[i]; p1.fr[i]=0 end
for y=1,6 do local lo=r(); local hi=r(); local m=lo|(hi<<8); for x=1,16 do p2.G[y][x]=(m&(1<<(x-1)))~=0 end end
for y=1,6 do p2.W[y]=r() end
for y=1,6 do p2.vg[y]=r() end
local mm=r(); for y=1,6 do p2.X[y]=(mm&(1<<(y-1)))~=0 end; p2.ca=r(); p2.aF=r(); if p2.aF<1 then p2.aF=1 end
for y=1,6 do if p2.L[y]>p2.W[y] then p2.L[y]=1 end end; p2:cr()
p3.ae=r();p3.bs=r();p3.F=r();p3.H=r();p3.a3=r();p3.a4=r();p3.aL=r()
p3.aY=r()-1; p3.Z=r()-8; p3.K=r(); p3.aj=r(); p3.aJ=(r()==1); p3.a8=r(); p3.aS=(r()==1); p3.Q=(r()==1)
local lx=r(); local ly=r(); if lx>0 then p3.A={x=lx,y=ly} else p3.A=nil end
for i=1,8 do local v=r(); if v==0 then p3.I[i]=nil else p3.I[i]=v end
p3.aD[i]=r(); p3.aR[i]=(r()==1); p3.ar[i]=r(); p3.aq[i]=r(); p3.aB[i]=r(); p3.aC[i]=r(); p3.aM[i]=r(); p3.aU[i]=r()
p3.aE[i]=r()
local C=nil; for j=1,6 do local nn=r(); if nn>0 then if not C then C={} end; C[#C+1]=nn end end; p3.Y[i]=C
end
for k=1,3 do local t=p4.tk[k]
for x=1,16 do local d=r(); if d==255 then t.G[x]=nil else t.G[x]=d-100 end end
t.aa=r(); t.aI=r(); t.aP=r()-100; t.X=(r()==1); t.bt=r(); t.vg=r(); t.bN=r()
local ac=t.bN-8; local ml=ac>=0 and (1+0.5*ac) or 1/(1+0.5*(-ac)); t.b9=bn(1,aK(6/ml+0.5))
if t.D<t.aa or t.D>t.aI then t.D=t.aa end
end
p3.bP=(p3.a8-5)*p1.O
for k=1,3 do p4.tk[k].b4=(p4.tk[k].bt-5)*p1.O end
p4.bh=(p1.bg-5)*p1.O
return true
end,
dN=function()
pages[3]:aH(); pages[3]:ai(); pages[4]:cP()
local p2=pages[2]; for y=1,6 do if p2.bo[y] then ak(p2.bo[y],0,p2.ch); p2.bo[y]=nil; p2.bF[y]=nil end end
end,
dR=function(ao) local M=SC.dz; M[1]=4; for i=1,SC.bu do M[i+1]=SC.ax[i] and 1 or 0 end; cq(pset_write,SC.cw,M) end,
cA=function(ao,az)
bx()
local ok,B=cq(pset_read,az+1); if not ok or not B then return end
SC.cb=true
SC.dN(); SC.unpack(B); SC.by=az; B=nil
if SC.bT and pages[3].Q then pages[3]:bc() end
SC.cb=false
if SC.bD then SC.mt=8 end; bx()
end,
dH=function(ao,az) pset_write(az+1,SC.dE()); SC.ax[az]=true; SC.by=az; SC.bD=true; SC.mt=8; bx() end,
dm=function(ao,az) cq(pset_delete,az+1); SC.ax[az]=false; if SC.by==az then SC.by=nil end; SC.bD=true; SC.mt=8; bx() end,
du=function() if SC.bD then SC.mt=SC.mt-1; if SC.mt<=0 then SC.bD=false; SC:dR(); bx() end end end,
dJ=function(ao)
if SC.bY then local s=SC.bY; SC.bY=nil; SC:cA(s); SC.mt=8; return end
SC.du()
end,
dI=function(ao)
local ok,m=cq(pset_read,SC.cw)
if ok and type(m)=="table" and m[1]==4 then for i=1,SC.bu do SC.ax[i]=(m[i+1]==1) end
else for i=1,SC.bu do SC.ax[i]=false end end
m=nil; bx()
end,
dq=function(ao) local i=SC.R; if not i or not SC.ax[i] then return end; if SC.bT then SC.a1=i else SC:cA(i); SC.a1=nil end end,
dr=function(ao) local i=SC.R; if i then SC:dH(i) end end,
dp=function(ao) local i=SC.R; if i and SC.ax[i] then SC:dm(i); SC.R=nil end end,
ds=function(ao,p1)
local bl=p1.bl; local b7=SC.am*SC.bv
for li=1,SC.bv do local i=b7+li; local x=((li-1)%8)+1; local y=((li-1)//8)+1
local h=SC.ax[i] and 6 or 2
if SC.by==i then h=11 end
if SC.a1==i then h=bl and 15 or 1 elseif SC.R==i then h=bl and 15 or 4 end
q(x,y,h)
end
for y=5,7 do for x=1,8 do q(x,y,0) end end
for p=0,SC.cn()-1 do q(p+1,6, p==SC.am and 12 or 3) end
end,
dt=function(ao,p1)
q(1,8,15); q(2,8,0)
local a=SC.R
q(3,8,(a and SC.ax[a]) and (SC.a1 and (p1.bl and 15 or 3) or 12) or 3)
q(4,8, a and 12 or 3)
q(5,8,(a and SC.ax[a]) and 12 or 3)
q(6,8,0)
q(7,8, SC.am>0 and 8 or 2)
q(8,8, SC.am<SC.cn()-1 and 8 or 2)
end,
bb=function(ao,x,y,z)
if z~=1 then return true end
if y==8 then
if x==1 then SC.b5=false; SC.R=nil
elseif x==3 then SC:dq()
elseif x==4 then SC:dr()
elseif x==5 then SC:dp()
elseif x==7 then if SC.am>0 then SC.am=SC.am-1 end
elseif x==8 then if SC.am<SC.cn()-1 then SC.am=SC.am+1 end end
return true
end
if y>=1 and y<=4 then local i=SC.am*SC.bv+((y-1)*8+x); if i>=1 and i<=SC.bu then SC.R=(SC.R==i) and nil or i end end
return true
end
}
for i=1,#pages do pages[i]:init() end
pset_init("gridcomp"); SC:dI()
m_main=metro.init(framework_tick,0.03) m_main:start()
