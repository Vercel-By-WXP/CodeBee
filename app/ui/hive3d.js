/* CodeBee Hive3D: dependency-free WebGL scene with live HTML task overlays. */
window.Hive3D = (function () {
  "use strict";
  const tr = (key, ...args) => typeof window.t === "function" ? window.t(key, ...args) : key;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const STATUS = { running:"运行中", done:"完成", queued:"排队中", failed:"失败", timeout:"超时", cancelled:"已取消" };
  const STAGES = ["规划","起草","评审","执行","打磨","合成"];
  const SCREENS = [
    [163,441,127,55],[389,441,126,55],[616,441,125,55],[843,441,125,55],
    [1073,441,122,55],[1298,441,125,55],[1531,441,120,55],[1750,441,125,55],
    [166,724,165,66],[470,724,165,66],[778,724,165,66],[1091,724,162,66],[1392,724,163,66],[1695,724,165,66]
  ];
  const PLATES = [[278,206],[578,206],[876,206],[1175,206],[1473,206],[1766,206]];
  const node = (tag, cls, parent) => { const el=document.createElement(tag); el.className=cls; if(parent)parent.appendChild(el); return el; };
  const mat4 = {
    perspective(fovy, aspect, near, far) { const f=1/Math.tan(fovy/2), nf=1/(near-far); return new Float32Array([f/aspect,0,0,0,0,f,0,0,0,0,(far+near)*nf,-1,0,0,2*far*near*nf,0]); },
    multiply(a,b) { const o=new Float32Array(16); for(let c=0;c<4;c++)for(let r=0;r<4;r++)o[c*4+r]=a[r]*b[c*4]*1+a[4+r]*b[c*4+1]+a[8+r]*b[c*4+2]+a[12+r]*b[c*4+3]; return o; },
    translate(x,y,z) { const o=new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);o[12]=x;o[13]=y;o[14]=z;return o; },
    scale(x,y,z) { return new Float32Array([x,0,0,0,0,y,0,0,0,0,z,0,0,0,0,1]); },
    rotateX(a) { const c=Math.cos(a),s=Math.sin(a);return new Float32Array([1,0,0,0,0,c,s,0,0,-s,c,0,0,0,0,1]); },
    rotateY(a) { const c=Math.cos(a),s=Math.sin(a);return new Float32Array([c,0,-s,0,0,1,0,0,s,0,c,0,0,0,0,1]); },
    lookAt(eye, center, up) { let z=norm(sub(eye,center)),x=norm(cross(up,z)),y=cross(z,x); return new Float32Array([x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1]); }
  };
  const sub=(a,b)=>a.map((v,i)=>v-b[i]), dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0), cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]], norm=a=>{const l=Math.hypot(...a)||1;return a.map(v=>v/l);};
  function shader(gl,type,source){const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;}
  function geometry(gl, positions, indices, normals) {
    const p=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,p);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(positions),gl.STATIC_DRAW);
    const ix=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ix);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,new Uint16Array(indices),gl.STATIC_DRAW);
    return {p,ix,count:indices.length,positions,indices,normals:normals||positions,smooth:Boolean(normals)};
  }
  function boxGeometry(gl){
    const p=[-.5,-.5,-.5,.5,-.5,-.5,.5,.5,-.5,-.5,.5,-.5,-.5,-.5,.5,.5,-.5,.5,.5,.5,.5,-.5,.5,.5];
    const ix=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,1,2,6,1,6,5,0,4,7,0,7,3];
    return geometry(gl,p,ix);
  }
  function roundedBoxGeometry(gl, segments=4, radius=.12){
    const positions=[],normals=[],indices=[];
    const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
    const point=(p)=>{
      const core=p.map(v=>clamp(v,-.5+radius,.5-radius));
      const d=p.map((v,i)=>v-core[i]),len=Math.hypot(...d)||1;
      return {p:core.map((v,i)=>v+d[i]/len*radius),n:d.map(v=>v/len)};
    };
    const faces=[
      (u,v)=>[.5,u,v],(u,v)=>[-.5,u,v],
      (u,v)=>[u,.5,v],(u,v)=>[u,-.5,v],
      (u,v)=>[u,v,.5],(u,v)=>[u,v,-.5]
    ];
    const emitTriangle=(p0,p1,p2)=>{
      const a=point(p0),b=point(p1),d=point(p2);
      const u=b.p.map((v,i)=>v-a.p[i]),v=d.p.map((q,i)=>q-a.p[i]);
      const cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];
      const n=[0,1,2].map(i=>a.n[i]+b.n[i]+d.n[i]);
      if(cross[0]*n[0]+cross[1]*n[1]+cross[2]*n[2]<0){const tmp=b.p;b.p=d.p;d.p=tmp;const tn=b.n;b.n=d.n;d.n=tn;}
      for(const vtx of [a,b,d]){positions.push(...vtx.p);normals.push(...vtx.n);indices.push(indices.length);}
    };
    for(const face of faces){
      for(let row=0;row<segments;row++)for(let col=0;col<segments;col++){
        const u0=-.5+col/segments,u1=-.5+(col+1)/segments;
        const v0=-.5+row/segments,v1=-.5+(row+1)/segments;
        const p00=face(u0,v0),p10=face(u1,v0),p11=face(u1,v1),p01=face(u0,v1);
        emitTriangle(p00,p10,p11);emitTriangle(p00,p11,p01);
      }
    }
    return geometry(gl,positions,indices,normals);
  }
  function sphereGeometry(gl, rows=9, cols=12){
    const p=[],ix=[];for(let r=0;r<=rows;r++){const v=r/rows*Math.PI;for(let c=0;c<=cols;c++){const u=c/cols*Math.PI*2;p.push(Math.sin(v)*Math.cos(u),Math.cos(v),Math.sin(v)*Math.sin(u));}}
    for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const a=r*(cols+1)+c,b=a+cols+1;ix.push(a,a+1,b,b,a+1,b+1);}
    return geometry(gl,p,ix,p.slice());
  }
  function Scene(opts){
    this.opts=opts;this.canvas=opts.canvas;this.overlay=opts.overlay;this.host=this.canvas.parentElement;
    this.onCellActivate=opts.onCellActivate||function(){};this.cellRefresh=opts.cellRefresh||function(){return{};};
    this.model=null;this.cells=[];this.page=0;this.zoom=1;this.yaw=-0.18;this.pitch=0.28;this.panX=0;this.panY=0;this.active=false;this.raf=0;this.timer=0;this.listeners=[];this.screenMeta=[];
    this.host.dataset.renderer="webgl";this.canvas.hidden=false;this.canvas.classList.add("hive-gl-live");
    this.layer=node("div","hive-reference-layer",this.host);this.layer.appendChild(this.overlay);this.overlay.replaceChildren();
    try { this.initGL(); } catch(e) { console.error("CodeBee Hive3D WebGL init failed",e); this.failed=true; this.failure=e; return; }
    this.stageMeta=PLATES.map(([x,y],i)=>{const el=node("button","hg-badge",this.overlay);el.type="button";el.style.left="0%";el.style.top="0%";const name=node("b","hg-name",el),meta=node("span","hg-meta",el);this.listen(el,"click",()=>{const lane=this.stageMeta[i].lane;if(!lane)return;const cell=lane.cells.find(c=>c.status==="running")||lane.cells[lane.cells.length-1];if(cell)this.onCellActivate(this.model.runId,cell.rel,cell);});return{el,name,meta,lane:null};});
    this.links=Array.from({length:PLATES.length-1},()=>node("span","hg-flow-link",this.overlay));
    this.monitors=SCREENS.map(([x,y,w,h],i)=>{const el=node("button", "hg-monitor", this.overlay);el.type="button";el.style.left="0%";el.style.top="0%";el.style.width=(w/2048*100)+"%";el.style.height=(h/1151*100)+"%";const role=node("b","hg-monitor-role",el),status=node("span","hg-monitor-status",el),tail=node("span","hg-monitor-tail",el),time=node("span","hg-monitor-time",el);this.listen(el,"click",()=>{const cell=this.screenMeta[i];if(cell)this.onCellActivate(this.model.runId,cell.rel,cell);});return{el,role,status,tail,time};});
    const toolbar=this.host.parentElement.querySelector(".hive-scene-tools");this.pager=node("div","hive-scene-pager");this.pager.hidden=true;this.prev=node("button","hive-scene-btn",this.pager);this.prev.type="button";this.prev.textContent="‹";this.pageLabel=node("span","hive-scene-page-label",this.pager);this.next=node("button","hive-scene-btn",this.pager);this.next.type="button";this.next.textContent="›";if(toolbar)toolbar.insertBefore(this.pager,toolbar.querySelector(".hive-scene-spacer"));
    this.listen(this.prev,"click",()=>this.showPage(this.page-1));this.listen(this.next,"click",()=>this.showPage(this.page+1));this.bindSurface();
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(this.host);this.languageObserver=new MutationObserver(()=>this.updateOverlay());this.languageObserver.observe(document.documentElement,{attributes:true,attributeFilter:["data-lang"]});this.resize();this.updateOverlay();window.__hive3d=this;
  }
  Scene.prototype.initGL=function(){
    const gl=this.canvas.getContext("webgl",{alpha:true,antialias:true,powerPreference:"high-performance"})||this.canvas.getContext("experimental-webgl");
    if(!gl)throw Error("WebGL unavailable");this.gl=gl;
    const vs="attribute vec3 aPosition; attribute vec3 aNormal; attribute vec4 aColor; uniform mat4 uViewProj; varying vec4 vColor; varying float vShade; varying vec3 vWorld; varying vec3 vNormal; void main(){gl_Position=uViewProj*vec4(aPosition,1.0);vColor=aColor;vWorld=aPosition;vNormal=normalize(aNormal);vec3 n=normalize(aNormal);vec3 light=normalize(vec3(-0.40,0.82,0.48));float diffuse=max(0.0,dot(n,light));float sky=clamp(n.y*0.5+0.5,0.0,1.0);vShade=0.50+0.37*diffuse+0.13*sky;}";
    const fs="precision mediump float; varying vec4 vColor; varying float vShade; varying vec3 vWorld; varying vec3 vNormal; uniform vec3 uEyePosition; void main(){vec3 n=normalize(vNormal);vec3 l=normalize(vec3(-0.40,0.82,0.48));vec3 v=normalize(uEyePosition-vWorld);vec3 h=normalize(l+v);float spec=pow(max(0.0,dot(n,h)),26.0)*0.085;float rim=pow(1.0-max(0.0,dot(n,v)),3.0)*0.045;vec3 lit=vColor.rgb*vShade+vec3(0.48,0.76,0.92)*(spec+rim);gl_FragColor=vec4(min(lit,vec3(1.0)),vColor.a);}";
    const program=gl.createProgram();gl.attachShader(program,shader(gl,gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl,gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));this.program=program;gl.useProgram(program);
    this.aPosition=gl.getAttribLocation(program,"aPosition");this.aNormal=gl.getAttribLocation(program,"aNormal");this.aColor=gl.getAttribLocation(program,"aColor");this.uViewProj=gl.getUniformLocation(program,"uViewProj");this.uEyePosition=gl.getUniformLocation(program,"uEyePosition");this.box=boxGeometry(gl);this.roundBox=roundedBoxGeometry(gl,4,.12);this.sphere=sphereGeometry(gl,16,24);gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.clearColor(0,0,0,0);
  };
  Scene.prototype.listen=function(el,type,fn,opts){el.addEventListener(type,fn,opts);this.listeners.push(()=>el.removeEventListener(type,fn,opts));};
  Scene.prototype.bindSurface=function(){
    let drag=null;this.listen(this.host,"contextmenu",e=>e.preventDefault());
    this.listen(this.host,"pointerdown",e=>{if(!this.active||e.target.closest("button")||e.button!==0)return;drag={x:e.clientX,y:e.clientY,yaw:this.yaw,pitch:this.pitch,px:this.panX,py:this.panY,shift:e.shiftKey};this.host.setPointerCapture(e.pointerId);this.host.classList.add("is-dragging");});
    this.listen(this.host,"pointermove",e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(drag.shift){this.panX=drag.px+dx/this.cssW*5;this.panY=drag.py-dy/this.cssH*4;}else{this.yaw=drag.yaw+dx*.006;this.pitch=clamp(drag.pitch+dy*.004,-.05,.85);}this.render();});
    const end=()=>{drag=null;this.host.classList.remove("is-dragging");};this.listen(this.host,"pointerup",end);this.listen(this.host,"pointercancel",end);
    this.listen(this.host,"dblclick",e=>{if(!e.target.closest("button"))this.resetView();});
    this.listen(this.host,"wheel",e=>{if(!this.active||e.target.closest("button"))return;e.preventDefault();this.zoom=clamp(this.zoom*Math.exp(-e.deltaY*.001),.72,2.4);this.render();},{passive:false});
    this.listen(this.canvas,"webglcontextlost",e=>{e.preventDefault();this.setActive(false);if(this.opts.onFatal)this.opts.onFatal(e);});
  };
  Scene.prototype.add=function(mesh,x,y,z,sx,sy,sz,color,rotY){this.objects.push({mesh,x,y,z,sx,sy,sz,color,rotY:rotY||0});};
  Scene.prototype.buildWorld=function(){
    const b=this.box,rb=this.roundBox,s=this.sphere;this.objects=[];
    // Bright, calm blue-white studio with a continuous floor and a single clean feature wall.
    this.add(b,0,-.18,0,19,.28,14,[.91,.95,.98,1]);
    this.add(b,0,2.48,-6.45,19,5.2,.24,[.045,.16,.28,1]);
    this.add(b,0,4.98,-2.8,19,.16,7.3,[.98,.99,1,1]);
    // Wall seams, lower trim, and glass side windows.
    this.add(b,0,.18,-6.28,18.7,.12,.06,[.10,.34,.47,1]);
    for(let x=-8;x<=8;x+=2.65)this.add(b,x,2.5,-6.29,.018,4.7,.025,[.075,.25,.37,1]);
    for(const side of [-1,1]){
      // Open-framed side glazing: avoid opaque side slabs that visually cut the room into boxes.
      for(const y of [.12,4.62])this.add(b,side*8.95,y,-.15,.075,.075,11.9,[.78,.87,.93,1]);
      for(let z=-5.55;z<=5.56;z+=2.22){
        this.add(b,side*8.95,2.36,z,.075,4.48,.075,[.84,.91,.96,1]);
        this.add(b,side*8.90,2.36,z-1.11,.018,4.34,2.10,[.55,.78,.92,.12]);
      }
    }
    // Ceiling light fixtures and soft-colored illuminated panels.
    for(let x=-6.3;x<=6.4;x+=6.3){
      this.add(b,x,4.87,-1.8,2.65,.075,.30,[.20,.24,.29,1]);
      this.add(b,x,4.80,-1.8,2.40,.045,.24,[1,.99,.91,1]);
    }
    // Six stage cards mounted on the blue wall, with luminous cyan underlines.
    const stageColors=[[.22,.83,.94,1],[.24,.80,.93,1],[.27,.84,.96,1],[.20,.87,.95,1],[.25,.81,.95,1],[.28,.88,.95,1]];
    for(let i=0;i<6;i++){
      const x=-7.1+i*2.84;
      this.add(rb,x,3.58,-6.16,2.40,.82,.18,[.10,.30,.42,1]);
      this.add(rb,x,3.58,-6.04,2.28,.68,.045,[.035,.11,.18,1]);
      this.add(b,x,3.87,-6.012,1.86,.018,.008,[.18,.66,.80,1]);
      this.add(b,x,3.14,-6.03,1.86,.035,.025,stageColors[i]);
      this.add(b,x+1.05,3.58,-6.04,.09,.09,.08,[.38,.89,.96,1]);
    }
    // Subtle hexagonal grout follows the reference floor and is baked into the static GPU batch.
    const tileR=.56, tileDX=1.5*tileR, tileDZ=Math.sqrt(3)*tileR;
    for(let row=0;row<13;row++){
      const cz=-5.55+row*tileDZ;
      for(let col=-10;col<=10;col++){
        const cx=col*tileDX+(row%2)*tileDX/2;
        if(Math.abs(cx)>9.0)continue;
        for(let edge=0;edge<3;edge++){
          const a0=edge*Math.PI/3,a1=(edge+1)*Math.PI/3;
          const x0=cx+tileR*Math.cos(a0),z0=cz+tileR*Math.sin(a0);
          const x1=cx+tileR*Math.cos(a1),z1=cz+tileR*Math.sin(a1);
          const rotation=Math.atan2(-(z1-z0),x1-x0);
          this.add(b,(x0+x1)/2,-.032,(z0+z1)/2,tileR,.008,.009,[.92,.95,.97,1],rotation);
        }
      }
    }
    // Reference layout: eight compact rear stations and six wider front stations.
    // Every monitor overlay is projected from these exact same coordinates below.
    const deskRows=[
      {row:0,z:-2.15,width:2.03,screenWidth:.79,screenHeight:.48,xs:[-7.77,-5.55,-3.33,-1.11,1.11,3.33,5.55,7.77]},
      {row:1,z:2.95,width:2.42,screenWidth:.91,screenHeight:.54,xs:[-7.0,-4.2,-1.4,1.4,4.2,7.0]}
    ];
    this.deskPositions=[];
    for(const config of deskRows)for(const x of config.xs){
      const {row,z,width,screenWidth,screenHeight}=config;
      this.deskPositions.push({row,x,z,width,screenWidth,screenHeight});
      this.add(rb,x,.54,z,width,.15,1.20,[.89,.93,.96,1]);
      this.add(b,x,.49,z-.603,width*.72,.014,.012,[.08,.42,.57,1]);
      this.add(rb,x-width*.36,.25,z+.03,.43,.52,1.02,[.80,.86,.91,1]);
      this.add(rb,x+width*.36,.25,z+.03,.43,.52,1.02,[.80,.86,.91,1]);
      this.add(rb,x,.43,z-.22,width*.78,.045,.74,[1,1,1,1]);
      this.add(rb,x,.99,z-.48,screenWidth+.14,screenHeight+.14,.10,[.045,.075,.10,1]);
      this.add(b,x,.995,z-.418,screenWidth,screenHeight,.018,[.025,.15,.25,1]);
      this.add(b,x,.61,z-.43,.10,.22,.10,[.32,.39,.45,1]);
      this.add(b,x,.49,z-.27,.54,.045,.34,[.16,.20,.24,1]);
      this.add(b,x,.72,z-.404,screenWidth*.84,.018,.012,[.10,.83,.94,1]);
      this.add(rb,x-.18,.655,z+.25,.60,.035,.20,[.15,.18,.21,1]);
      this.add(b,x+.38,.66,z+.22,.12,.06,.17,[.23,.27,.30,1]);
      this.add(b,x-.18,.64,z+.25,.66,.012,.24,[.75,.81,.86,1]);
      const binderStart=x+width/2-.48;
      for(let k=0;k<4;k++)this.add(b,binderStart+k*.095,.83,z+.35,.075,.43,.22,[[.13,.43,.78,1],[.95,.42,.24,1],[.16,.66,.46,1],[.93,.72,.28,1]][k]);
      this.add(b,x,.08,z+1.10,.10,.28,.10,[.12,.15,.18,1]);
      this.add(rb,x,.22,z+1.10,.62,.12,.56,[.11,.14,.17,1]);
      this.add(rb,x,.57,z+1.35,.62,.72,.16,[.075,.11,.15,1]);
      this.add(rb,x,.57,z+1.434,.42,.48,.018,[.13,.18,.22,1]);
      this.add(rb,x,.48,z+1.25,.48,.12,.12,[.18,.22,.25,1]);
      for(let k=0;k<5;k++){const a=k*Math.PI*2/5;this.add(b,x+Math.cos(a)*.38,.035,z+1.10+Math.sin(a)*.32,.30,.06,.075,[.08,.10,.12,1],a);}
      const by=1.25,bz=z+.88;
      this.add(s,x,by,bz,.35,.34,.32,[1,.70,.05,1]);
      this.add(s,x,by-.24,bz+.02,.28,.23,.26,[.98,.57,.035,1]);
      this.add(b,x,by-.19,bz+.245,.26,.065,.045,[.045,.055,.06,1]);
      this.add(rb,x,by-.055,bz-.285,.285,.082,.06,[.025,.09,.13,1]);
      this.add(rb,x,by-.055,bz-.322,.205,.026,.012,[.06,.48,.61,1]);
      this.add(s,x-.105,by-.055,bz-.338,.022,.022,.018,[.18,.97,1,1]);
      this.add(s,x+.105,by-.055,bz-.338,.022,.022,.018,[.18,.97,1,1]);
      this.add(s,x,by-.065,bz+.305,.20,.045,.018,[.045,.055,.06,1]);
      this.add(b,x-.13,by+.23,bz+.13,.028,.18,.028,[.08,.23,.31,1]);
      this.add(b,x+.13,by+.23,bz+.13,.028,.18,.028,[.08,.23,.31,1]);
      this.add(s,x-.13,by+.34,bz+.13,.035,.032,.035,[.10,.84,.98,1]);
      this.add(s,x+.13,by+.34,bz+.13,.035,.032,.035,[.10,.84,.98,1]);
      for(const side of [-1,1]){
        this.add(s,x+side*.22,by-.28,bz-.11,.10,.11,.20,[.98,.62,.035,1]);
        this.add(s,x+side*.27,by-.40,bz-.43,.10,.08,.38,[.99,.66,.045,1]);
        this.add(s,x+side*.27,.69,z+.25,.08,.065,.09,[1,.72,.08,1]);
      }
      this.add(s,x-.30,by+.10,bz-.03,.28,.075,.18,[.70,.91,1,.72]);
      this.add(s,x+.30,by+.10,bz-.03,.28,.075,.18,[.70,.91,1,.72]);
      this.add(b,x,by-.28,bz-.015,.18,.055,.22,[.07,.08,.09,1]);
    }
    // Planters and stylized leaves soften the room edges.
    for(const x of [-8.05,8.05]){
      this.add(rb,x,.17,-4.25,.62,.34,.62,[.70,.77,.81,1]);
      for(let k=0;k<7;k++){const a=k*2.399;this.add(s,x+Math.cos(a)*.36,.80+(k%4)*.15,-4.25+Math.sin(a)*.30,.10,.36,.10,[.08,.32+(k%3)*.035,.22,1],a);}
    }
    // Low-opacity contact shadows share the same 8+6 workstation positions.
    for(const desk of this.deskPositions){
      const {x,z,width}=desk;
      this.add(s,x,-.026,z,width*.68,.026,.73,[.13,.23,.31,.15]);
      this.add(s,x,-.025,z+1.08,.78,.018,.58,[.12,.20,.27,.10]);
    }
  };
  Scene.prototype.buildBatches=function(){
    const channels={opaque:{positions:[],normals:[],colors:[]},transparent:{positions:[],normals:[],colors:[]}};
    const emit=(target,p,n,color)=>{
      target.positions.push(p[0],p[1],p[2]);target.normals.push(n[0],n[1],n[2]);
      target.colors.push(color[0],color[1],color[2],color[3]==null?1:color[3]);
    };
    const worldPoint=(o,v)=>{const x=v[0]*o.sx,z=v[2]*o.sz,a=o.rotY||0,ca=Math.cos(a),sa=Math.sin(a);return [o.x+ca*x+sa*z,o.y+v[1]*o.sy,o.z-sa*x+ca*z];};
    const smoothNormal=(o,v)=>{const x=v[0]/Math.max(.0001,o.sx),y=v[1]/Math.max(.0001,o.sy),z=v[2]/Math.max(.0001,o.sz),a=o.rotY||0,ca=Math.cos(a),sa=Math.sin(a);const n=[ca*x+sa*z,y,-sa*x+ca*z],l=Math.hypot(n[0],n[1],n[2])||1;return n.map(v=>v/l);};
    const faceNormal=(a,b,c)=>{const u=[b[0]-a[0],b[1]-a[1],b[2]-a[2]],v=[c[0]-a[0],c[1]-a[1],c[2]-a[2]],n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],l=Math.hypot(n[0],n[1],n[2])||1;return n.map(v=>v/l);};
    for(const o of this.objects){
      const mesh=o.mesh,src=mesh.positions,ix=mesh.indices,normals=mesh.normals||src;
      const target=(o.color[3]!=null&&o.color[3]<.999)?channels.transparent:channels.opaque;
      for(let i=0;i<ix.length;i+=3){
        const local=[0,1,2].map(k=>[src[ix[i+k]*3],src[ix[i+k]*3+1],src[ix[i+k]*3+2]]);
        const world=local.map(v=>worldPoint(o,v));
        const smooth=mesh===this.sphere||mesh.smooth;
        const face=smooth?null:faceNormal(world[0],world[1],world[2]);
        for(let k=0;k<3;k++){
          const ix3=ix[i+k]*3,normal=[normals[ix3],normals[ix3+1],normals[ix3+2]];
          emit(target,world[k],face||smoothNormal(o,normal),o.color);
        }
      }
    }
    const gl=this.gl,upload=data=>{if(!data.length)return null;const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(data),gl.STATIC_DRAW);return buffer;};
    this.batchBuffers={};
    for(const key of ["opaque","transparent"]){
      const channel=channels[key];
      this.batchBuffers[key]={positions:upload(channel.positions),normals:upload(channel.normals),colors:upload(channel.colors),count:channel.positions.length/3};
    }
    for(const mesh of [this.box,this.roundBox,this.sphere])if(mesh){if(mesh.p)gl.deleteBuffer(mesh.p);if(mesh.ix)gl.deleteBuffer(mesh.ix);mesh.p=null;mesh.ix=null;}
  };
  Scene.prototype.drawBatch=function(batch){
    if(!batch||!batch.count)return;
    const gl=this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER,batch.positions);gl.enableVertexAttribArray(this.aPosition);gl.vertexAttribPointer(this.aPosition,3,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,batch.normals);gl.enableVertexAttribArray(this.aNormal);gl.vertexAttribPointer(this.aNormal,3,gl.FLOAT,false,0,0);
    gl.bindBuffer(gl.ARRAY_BUFFER,batch.colors);gl.enableVertexAttribArray(this.aColor);gl.vertexAttribPointer(this.aColor,4,gl.FLOAT,false,0,0);
    gl.drawArrays(gl.TRIANGLES,0,batch.count);
  };
  Scene.prototype.render=function(){
    if(!this.gl||!this.active)return;const gl=this.gl;const w=Math.max(1,this.cssW),h=Math.max(1,this.cssH);const dpr=Math.min(window.devicePixelRatio||1,1.6);
    const bw=Math.floor(w*dpr),bh=Math.floor(h*dpr);if(this.canvas.width!==bw||this.canvas.height!==bh){this.canvas.width=bw;this.canvas.height=bh;}
    gl.viewport(0,0,bw,bh);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);
    const radius=16/this.zoom;const eye=[Math.sin(this.yaw)*radius,3.0+Math.sin(this.pitch)*radius*.34,Math.cos(this.yaw)*radius-1.3];const view=mat4.lookAt(eye,[0,.25,0],[0,1,0]);const proj=mat4.perspective(.73,w/h,.1,70);const vp=mat4.multiply(mat4.multiply(proj,view),mat4.translate(this.panX,this.panY,0));this.viewProj=vp;
    gl.uniformMatrix4fv(this.uViewProj,false,vp);gl.uniform3f(this.uEyePosition,eye[0],eye[1],eye[2]);
    this.drawBatch(this.batchBuffers&&this.batchBuffers.opaque);
    const translucent=this.batchBuffers&&this.batchBuffers.transparent;
    if(translucent&&translucent.count){gl.depthMask(false);this.drawBatch(translucent);gl.depthMask(true);}
    this.layoutOverlayPositions();
  };
  Scene.prototype.projectWorld=function(x,y,z){
    if(!this.viewProj||!this.cssW||!this.cssH)return null;
    const m=this.viewProj,px=x,py=y;
    const cx=m[0]*px+m[4]*py+m[8]*z+m[12],cy=m[1]*px+m[5]*py+m[9]*z+m[13],cw=m[3]*px+m[7]*py+m[11]*z+m[15];
    if(cw<=.05)return null;
    const nx=cx/cw,ny=cy/cw;
    return{x:(nx*.5+.5)*this.cssW,y:(1-(ny*.5+.5))*this.cssH,visible:nx> -1.3&&nx<1.3&&ny> -1.3&&ny<1.3};
  };
  Scene.prototype.projectRect=function(points){
    const projected=points.map(p=>this.projectWorld(p[0],p[1],p[2]));
    if(projected.some(p=>!p||!p.visible))return null;
    const xs=projected.map(p=>p.x),ys=projected.map(p=>p.y);
    const left=Math.min(...xs),top=Math.min(...ys),right=Math.max(...xs),bottom=Math.max(...ys);
    if(right<0||bottom<0||left>this.cssW||top>this.cssH)return null;
    return {left,top,right,bottom,width:right-left,height:bottom-top,centerX:(left+right)/2,centerY:(top+bottom)/2};
  };
  Scene.prototype.layoutOverlayPositions=function(){
    if(!this.overlay||this.overlay.hidden||!this.cssW||!this.cssH)return;
    // Stage labels occupy the front face of each physical wall panel, rather than a fixed HUD row.
    this.stageMeta.forEach((p,i)=>{
      const x=-7.1+i*2.84,z=-6.07;
      const rect=this.projectRect([[x-1.17,3.17,z],[x+1.17,3.17,z],[x+1.17,3.99,z],[x-1.17,3.99,z]]);
      if(!rect){p.el.style.visibility="hidden";return;}
      p.el.style.visibility="visible";
      p.el.style.left=(rect.centerX/this.cssW*100)+"%";
      p.el.style.top=(rect.centerY/this.cssH*100)+"%";
      p.el.style.width=Math.max(54,rect.width*.96)+"px";
      p.el.style.height=Math.max(28,rect.height*.88)+"px";
      p.el.style.transform="translate(-50%,-50%)";
    });
    // Connectors stay on the wall plane and follow the same camera transform as the cards.
    this.links.forEach((link,i)=>{
      const leftX=-7.1+i*2.84+1.22,rightX=-7.1+(i+1)*2.84-1.22;
      const a=this.projectWorld(leftX,3.58,-6.03),b=this.projectWorld(rightX,3.58,-6.03);
      if(!a||!b||!a.visible||!b.visible){link.style.visibility="hidden";return;}
      const dx=b.x-a.x,dy=b.y-a.y;
      link.style.visibility="visible";
      link.style.left=(a.x/this.cssW*100)+"%";link.style.top=(a.y/this.cssH*100)+"%";
      link.style.width=Math.hypot(dx,dy)+"px";
      link.style.transform="translateY(-50%) rotate("+Math.atan2(dy,dx)+"rad)";
    });
    // Project all four corners of the actual glass. This keeps task text inside each screen
    // while the camera rotates, pitches, pans or zooms; the former three-point average drifted.
    this.monitors.forEach((m,i)=>{
      const desk=this.deskPositions&&this.deskPositions[i];
      if(!desk){m.el.style.visibility="hidden";return;}
      const {x,z,screenWidth,screenHeight}=desk;
      const halfW=screenWidth/2,halfH=screenHeight/2;
      const rect=this.projectRect([[x-halfW,.995-halfH,z-.418],[x+halfW,.995-halfH,z-.418],
        [x+halfW,.995+halfH,z-.418],[x-halfW,.995+halfH,z-.418]]);
      if(!rect||rect.width<8||rect.height<6){m.el.style.visibility="hidden";return;}
      m.el.style.visibility="visible";
      m.el.style.left=(rect.left/this.cssW*100)+"%";
      m.el.style.top=(rect.top/this.cssH*100)+"%";
      m.el.style.width=Math.max(8,rect.width-2)+"px";
      m.el.style.height=Math.max(6,rect.height-2)+"px";
      m.el.style.transform="none";
    });
  };
    Scene.prototype.sync=function(model){const changed=!this.model||this.model.runId!==model.runId;this.model=model;this.cells=model.lanes.flatMap((lane,stageIdx)=>lane.cells.map(cell=>({...cell,stageIdx})));if(changed){const r=this.cells.findIndex(c=>c.status==="running");this.page=r<0?0:Math.floor(r/SCREENS.length);this.resetView();}this.showPage(this.page);};
  Scene.prototype.showPage=function(page){const pages=Math.max(1,Math.ceil((this.cells||[]).length/SCREENS.length));this.page=clamp(page,0,pages-1);this.screenMeta=(this.cells||[]).slice(this.page*SCREENS.length,(this.page+1)*SCREENS.length);this.pager.hidden=pages<=1;this.prev.disabled=this.page===0;this.next.disabled=this.page===pages-1;this.pageLabel.textContent=(this.page+1)+" / "+pages;this.updateOverlay();};
  Scene.prototype.updateOverlay=function(){
    const lanes=this.model?this.model.lanes:[];
    this.stageMeta.forEach((p,i)=>{const lane=lanes[i];p.lane=lane||null;p.name.textContent=tr("阶段 {0} · {1}",i+1,lane?tr(lane.nameKey||lane.name):tr(STAGES[i]));p.meta.textContent=lane?tr("{0} / {1} 步骤",lane.settled,lane.count):tr("等待任务");p.el.classList.toggle("hg-active",!!(lane&&lane.active));p.el.disabled=!lane||!lane.cells.length;p.el.title=tr("点击查看实时日志");});
    this.monitors.forEach((m,i)=>{const c=this.screenMeta[i],refresh=c&&c.rel?this.cellRefresh(c.rel)||{}:{},state=c?c.status:"idle";m.el.className="hg-monitor st-"+state;m.el.disabled=!c;m.el.dataset.log=c?c.rel:"";m.role.textContent=c?tr(c.role):tr("空闲工位");m.status.textContent=c?tr(STATUS[state]||"完成"):tr("待命");m.tail.textContent=c?refresh.tail||c.displayTail||(state==="running"?tr("等待日志输出…"):state==="queued"?tr("等待执行"):tr("（无输出）")):tr("等待任务");m.time.textContent=c?refresh.elapsed||c.displayElapsed||"":"";m.el.title=c?[tr(c.role),tr(STATUS[state]||"完成"),c.displayAgent,m.tail.textContent,tr("点击查看实时日志")].filter(Boolean).join(" · "):tr("空闲工位");m.el.setAttribute("aria-label",m.el.title);});
    this.prev.setAttribute("aria-label",tr("上一组工位"));this.next.setAttribute("aria-label",tr("下一组工位"));
  };
  Scene.prototype.setActive=function(active){this.active=!!active;if(this.timer)clearInterval(this.timer);if(this.raf)cancelAnimationFrame(this.raf);this.raf=0;this.layer.hidden=!this.active;this.canvas.hidden=!this.active;if(this.active){this.updateOverlay();this.render();this.timer=setInterval(()=>{if(!document.hidden&&this.host.offsetParent!==null)this.updateOverlay();},1000);}};
  Scene.prototype.resize=function(){this.cssW=this.host.clientWidth;this.cssH=this.host.clientHeight;if(this.cssW&&this.cssH)this.render();};
  Scene.prototype.zoomAt=function(factor){this.zoom=clamp(this.zoom/factor,.72,2.4);this.render();};
  Scene.prototype.resetView=function(){this.zoom=1;this.yaw=-.18;this.pitch=.28;this.panX=0;this.panY=0;this.render();};
  Scene.prototype.info=function(){return{renderer:"webgl",cells:(this.cells||[]).length,screens:SCREENS.length,lanes:this.model?this.model.lanes.length:0,page:this.page,zoom:this.zoom,cssW:this.cssW,cssH:this.cssH,objects:this.objects?this.objects.length:0,deskRows:this.deskPositions?[this.deskPositions.filter(d=>d.row===0).length,this.deskPositions.filter(d=>d.row===1).length]:[]};};
  Scene.prototype.projectCell=function(rel){const i=this.screenMeta.findIndex(c=>c.rel===rel),desk=this.deskPositions&&this.deskPositions[i];if(i<0||!desk)return null;const p=this.projectWorld(desk.x,.995,desk.z-.418);return p&&p.visible?{x:p.x,y:p.y}:null;};
  Scene.prototype.dispose=function(){this.setActive(false);if(this.resizeObserver)this.resizeObserver.disconnect();if(this.languageObserver)this.languageObserver.disconnect();this.listeners.forEach(fn=>fn());if(this.pager)this.pager.remove();if(this.gl){const gl=this.gl;if(this.batchBuffers)for(const pass of Object.values(this.batchBuffers))for(const key of ["positions","normals","colors"])if(pass&&pass[key])gl.deleteBuffer(pass[key]);for(const o of [this.box,this.roundBox,this.sphere])if(o){if(o.p)gl.deleteBuffer(o.p);if(o.ix)gl.deleteBuffer(o.ix);}if(this.program)gl.deleteProgram(this.program);}if(window.__hive3d===this)delete window.__hive3d;};
  const originalCreate=(opts)=>new Scene(opts);
  // Initialize geometry after the constructor has successfully acquired WebGL.
  const create=(opts)=>{const scene=originalCreate(opts);if(scene.failed||!scene.gl)return null;scene.buildWorld();scene.buildBatches();scene.render();return scene;};
  return {create};
})();