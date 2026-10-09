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
  function geometry(gl, positions, indices) {
    const p=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,p);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(positions),gl.STATIC_DRAW);
    const ix=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,ix);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,new Uint16Array(indices),gl.STATIC_DRAW);
    return {p,ix,count:indices.length};
  }
  function boxGeometry(gl){
    const p=[-.5,-.5,-.5,.5,-.5,-.5,.5,.5,-.5,-.5,.5,-.5,-.5,-.5,.5,.5,-.5,.5,.5,.5,.5,-.5,.5,.5];
    const ix=[0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,3,7,6,3,6,2,1,2,6,1,6,5,0,4,7,0,7,3];
    return geometry(gl,p,ix);
  }
  function sphereGeometry(gl, rows=9, cols=12){
    const p=[],ix=[];for(let r=0;r<=rows;r++){const v=r/rows*Math.PI;for(let c=0;c<=cols;c++){const u=c/cols*Math.PI*2;p.push(Math.sin(v)*Math.cos(u),Math.cos(v),Math.sin(v)*Math.sin(u));}}
    for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const a=r*(cols+1)+c,b=a+cols+1;ix.push(a,a+1,b,b,a+1,b+1);}
    return geometry(gl,p,ix);
  }
  function Scene(opts){
    this.opts=opts;this.canvas=opts.canvas;this.overlay=opts.overlay;this.host=this.canvas.parentElement;
    this.onCellActivate=opts.onCellActivate||function(){};this.cellRefresh=opts.cellRefresh||function(){return{};};
    this.model=null;this.cells=[];this.page=0;this.zoom=1;this.yaw=-0.18;this.pitch=0.28;this.panX=0;this.panY=0;this.active=false;this.raf=0;this.timer=0;this.listeners=[];this.screenMeta=[];
    this.host.dataset.renderer="webgl";this.canvas.hidden=false;this.canvas.classList.add("hive-gl-live");
    this.layer=node("div","hive-reference-layer",this.host);this.layer.appendChild(this.overlay);this.overlay.replaceChildren();
    try { this.initGL(); } catch(e) { console.error("CodeBee Hive3D WebGL init failed",e); if(opts.onFatal)opts.onFatal(e); return; }
    this.stageMeta=PLATES.map(([x,y],i)=>{const el=node("button","hg-badge",this.overlay);el.type="button";el.style.left="0%";el.style.top="0%";const name=node("b","hg-name",el),meta=node("span","hg-meta",el);this.listen(el,"click",()=>{const lane=this.stageMeta[i].lane;if(!lane)return;const cell=lane.cells.find(c=>c.status==="running")||lane.cells[lane.cells.length-1];if(cell)this.onCellActivate(this.model.runId,cell.rel,cell);});return{el,name,meta,lane:null};});
    this.monitors=SCREENS.map(([x,y,w,h],i)=>{const el=node("button","hg-monitor",this.overlay);el.type="button";el.style.left="0%";el.style.top="0%";el.style.width=(w/2048*100)+"%";el.style.height=(h/1151*100)+"%";const role=node("b","hg-monitor-role",el),status=node("span","hg-monitor-status",el),tail=node("span","hg-monitor-tail",el),time=node("span","hg-monitor-time",el);this.listen(el,"click",()=>{const cell=this.screenMeta[i];if(cell)this.onCellActivate(this.model.runId,cell.rel,cell);});return{el,role,status,tail,time};});
    const toolbar=this.host.parentElement.querySelector(".hive-scene-tools");this.pager=node("div","hive-scene-pager");this.pager.hidden=true;this.prev=node("button","hive-scene-btn",this.pager);this.prev.type="button";this.prev.textContent="‹";this.pageLabel=node("span","hive-scene-page-label",this.pager);this.next=node("button","hive-scene-btn",this.pager);this.next.type="button";this.next.textContent="›";if(toolbar)toolbar.insertBefore(this.pager,toolbar.querySelector(".hive-scene-spacer"));
    this.listen(this.prev,"click",()=>this.showPage(this.page-1));this.listen(this.next,"click",()=>this.showPage(this.page+1));this.bindSurface();
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(this.host);this.languageObserver=new MutationObserver(()=>this.updateOverlay());this.languageObserver.observe(document.documentElement,{attributes:true,attributeFilter:["data-lang"]});this.resize();this.updateOverlay();window.__hive3d=this;
  }
  Scene.prototype.initGL=function(){
    const gl=this.canvas.getContext("webgl",{alpha:true,antialias:true,powerPreference:"high-performance"})||this.canvas.getContext("experimental-webgl");
    if(!gl)throw Error("WebGL unavailable");this.gl=gl;
    const vs="attribute vec3 aPosition; uniform mat4 uMvp; uniform vec4 uColor; varying vec4 vColor; varying float vShade; void main(){gl_Position=uMvp*vec4(aPosition,1.0);vColor=uColor;vec3 n=normalize(aPosition);vec3 light=normalize(vec3(-0.45,0.85,0.55));vShade=0.62+0.38*max(0.0,dot(n,light));}";
    const fs="precision mediump float; varying vec4 vColor; varying float vShade; void main(){gl_FragColor=vec4(vColor.rgb*vShade,vColor.a);}";
    const program=gl.createProgram();gl.attachShader(program,shader(gl,gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl,gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));this.program=program;gl.useProgram(program);
    this.aPosition=gl.getAttribLocation(program,"aPosition");this.uMvp=gl.getUniformLocation(program,"uMvp");this.uColor=gl.getUniformLocation(program,"uColor");this.box=boxGeometry(gl);this.sphere=sphereGeometry(gl);gl.enable(gl.DEPTH_TEST);gl.enable(gl.CULL_FACE);gl.cullFace(gl.BACK);gl.clearColor(.78,.88,.96,1);
  };
  Scene.prototype.listen=function(el,type,fn,opts){el.addEventListener(type,fn,opts);this.listeners.push(()=>el.removeEventListener(type,fn,opts));};
  Scene.prototype.bindSurface=function(){
    let drag=null;this.listen(this.host,"contextmenu",e=>e.preventDefault());
    this.listen(this.host,"pointerdown",e=>{if(!this.active||e.target.closest("button")||e.button!==0)return;drag={x:e.clientX,y:e.clientY,yaw:this.yaw,pitch:this.pitch,px:this.panX,py:this.panY,shift:e.shiftKey};this.host.setPointerCapture(e.pointerId);this.host.classList.add("is-dragging");});
    this.listen(this.host,"pointermove",e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(drag.shift){this.panX=drag.px+dx/this.cssW*5;this.panY=drag.py-dy/this.cssH*4;}else{this.yaw=drag.yaw+dx*.006;this.pitch=clamp(drag.pitch+dy*.004,-.05,.85);}this.render();});
    const end=()=>{drag=null;this.host.classList.remove("is-dragging");};this.listen(this.host,"pointerup",end);this.listen(this.host,"pointercancel",end);
    this.listen(this.host,"dblclick",e=>{if(!e.target.closest("button"))this.resetView();});
    this.listen(this.host,"wheel",e=>{if(!this.active||e.target.closest("button"))return;e.preventDefault();this.zoom=clamp(this.zoom*Math.exp(-e.deltaY*.001),.72,2.4);this.render();},{passive:false});
    this.listen(this.canvas,"webglcontextlost",e=>{e.preventDefault();if(this.opts.onFatal)this.opts.onFatal(e);});
  };
  Scene.prototype.add=function(mesh,x,y,z,sx,sy,sz,color,rotY){this.objects.push({mesh,x,y,z,sx,sy,sz,color,rotY:rotY||0});};
  Scene.prototype.buildWorld=function(){
    const b=this.box,s=this.sphere;this.objects=[];
    // Bright, calm blue-white studio with a continuous floor and a single clean feature wall.
    this.add(b,0,-.18,0,19,.28,14,[.91,.95,.98,1]);
    this.add(b,0,2.48,-6.45,19,5.2,.24,[.10,.43,.74,1]);
    this.add(b,0,4.98,-2.8,19,.16,7.3,[.98,.99,1,1]);
    // Wall seams, lower trim, and glass side windows.
    this.add(b,0,.18,-6.28,18.7,.12,.06,[.06,.30,.55,1]);
    for(let x=-8;x<=8;x+=2.65)this.add(b,x,2.5,-6.29,.018,4.7,.025,[.06,.33,.60,1]);
    for(const side of [-1,1]){
      this.add(b,side*9.12,2.35,-.15,.18,4.55,12.1,[.78,.87,.94,1]);
      this.add(b,side*9.0,2.4,-.15,.05,4.6,12,[.20,.35,.46,1]);
      for(let z=-4.7;z<=4.8;z+=2.35)this.add(b,side*9.0,2.4,z,.06,4.35,.045,[.28,.43,.54,1]);
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
      this.add(b,x,3.58,-6.16,2.22,.76,.18,[.99,.995,1,1]);
      this.add(b,x,3.15,-6.03,1.72,.035,.025,stageColors[i]);
      this.add(b,x+1.05,3.58,-6.04,.09,.09,.08,[.38,.89,.96,1]);
    }
    // Floor grout creates a subtle, regular hex-inspired technical grid without texture assets.
    for(let x=-8.7;x<=8.7;x+=.72)this.add(b,x,-.025,.15,.012,.012,12.7,[.76,.84,.90,1]);
    for(let z=-5.8;z<=6.1;z+=.72)this.add(b,0,-.024,z,17.8,.012,.012,[.76,.84,.90,1]);
    // Fourteen desks: repeat identical assets, but leave generous aisles and keep the screen-facing side clear.
    for(let row=0;row<2;row++)for(let i=0;i<7;i++){
      const x=(i-3)*2.48,z=row===0?-2.0:3.0;
      // slim white desktop and two solid pedestals
      this.add(b,x,.54,z,2.12,.15,1.20,[.97,.98,1,1]);
      this.add(b,x-.77,.25,z+.03,.48,.52,1.02,[.80,.86,.91,1]);
      this.add(b,x+.77,.25,z+.03,.48,.52,1.02,[.80,.86,.91,1]);
      this.add(b,x,.43,z-.22,1.62,.045,.74,[1,1,1,1]);
      // monitor with dark bezel, blue glass, stand and cyan status edge
      this.add(b,x,.99,z-.48,1.03,.68,.10,[.045,.075,.10,1]);
      this.add(b,x,.995,z-.418,.91,.54,.018,[.025,.15,.25,1]);
      this.add(b,x,.61,z-.43,.10,.22,.10,[.32,.39,.45,1]);
      this.add(b,x,.49,z-.27,.54,.045,.34,[.16,.20,.24,1]);
      this.add(b,x,.72,z-.404,.76,.018,.012,[.10,.83,.94,1]);
      // keyboard, mouse, mouse pad
      this.add(b,x-.18,.655,z+.25,.60,.035,.20,[.15,.18,.21,1]);
      this.add(b,x+.38,.66,z+.22,.12,.06,.17,[.23,.27,.30,1]);
      this.add(b,x-.18,.64,z+.25,.66,.012,.24,[.75,.81,.86,1]);
      // Colored binders stand upright on a compact rack.
      for(let k=0;k<4;k++)this.add(b,x+.68+k*.115,.83,z+.35,.09,.43,.22,[[.13,.43,.78,1],[.95,.42,.24,1],[.16,.66,.46,1],[.93,.72,.28,1]][k]);
      // Ergonomic chair: five-spoke base, gas lift, padded seat and back.
      this.add(b,x,.08,z+1.10,.10,.28,.10,[.12,.15,.18,1]);
      this.add(b,x,.22,z+1.10,.62,.12,.56,[.11,.14,.17,1]);
      this.add(b,x,.57,z+1.35,.62,.72,.16,[.10,.13,.16,1]);
      this.add(b,x,.48,z+1.25,.48,.12,.12,[.18,.22,.25,1]);
      for(let k=0;k<5;k++){const a=k*Math.PI*2/5;this.add(b,x+Math.cos(a)*.38,.035,z+1.10+Math.sin(a)*.32,.30,.06,.075,[.08,.10,.12,1],a);}
      // Rounded bee helper robot: yellow head and abdomen, dark face, antennae and translucent wings.
      const by=1.25,bz=z+.88;
      this.add(s,x,by,bz,.35,.34,.32,[1,.70,.05,1]);
      this.add(s,x,by-.24,bz+.02,.28,.23,.26,[.98,.57,.035,1]);
      this.add(b,x,by-.19,bz+.245,.26,.065,.045,[.045,.055,.06,1]);
      this.add(s,x-.105,by+.035,bz+.285,.035,.035,.028,[.02,.03,.04,1]);
      this.add(s,x+.105,by+.035,bz+.285,.035,.035,.028,[.02,.03,.04,1]);
      this.add(b,x-.13,by+.20,bz+.01,.028,.18,.028,[.08,.09,.10,1]);
      this.add(b,x+.13,by+.20,bz+.01,.028,.18,.028,[.08,.09,.10,1]);
      this.add(s,x-.30,by+.10,bz-.03,.28,.075,.18,[.70,.91,1,.72]);
      this.add(s,x+.30,by+.10,bz-.03,.28,.075,.18,[.70,.91,1,.72]);
      this.add(b,x,by-.28,bz-.015,.18,.055,.22,[.07,.08,.09,1]);
    }
    // Planters and stylized leaves soften the room edges.
    for(const x of [-8.05,8.05]){
      this.add(b,x,.17,-4.25,.62,.34,.62,[.70,.77,.81,1]);
      for(let k=0;k<9;k++){const a=k*2.399;this.add(s,x+Math.cos(a)*.40,.80+(k%4)*.18,-4.25+Math.sin(a)*.34,.12,.43,.12,[.12,.48+(k%3)*.05,.22,1],a);}
    }
  };
  Scene.prototype.drawObject=function(o,viewProj){
    const m=mat4.multiply(mat4.translate(o.x+this.panX,o.y+this.panY,o.z),mat4.multiply(mat4.rotateY(o.rotY),mat4.scale(o.sx,o.sy,o.sz)));
    const mvp=mat4.multiply(viewProj,m),gl=this.gl;gl.bindBuffer(gl.ARRAY_BUFFER,o.mesh.p);gl.enableVertexAttribArray(this.aPosition);gl.vertexAttribPointer(this.aPosition,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,o.mesh.ix);gl.uniformMatrix4fv(this.uMvp,false,mvp);gl.uniform4fv(this.uColor,o.color);gl.drawElements(gl.TRIANGLES,o.mesh.count,gl.UNSIGNED_SHORT,0);
  };
  Scene.prototype.render=function(){
    if(!this.gl||!this.active)return;const gl=this.gl;const w=Math.max(1,this.cssW),h=Math.max(1,this.cssH);const dpr=Math.min(window.devicePixelRatio||1,1.6);
    const bw=Math.floor(w*dpr),bh=Math.floor(h*dpr);if(this.canvas.width!==bw||this.canvas.height!==bh){this.canvas.width=bw;this.canvas.height=bh;}
    gl.viewport(0,0,bw,bh);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);
    const radius=16/this.zoom;const eye=[Math.sin(this.yaw)*radius,3.0+Math.sin(this.pitch)*radius*.34,Math.cos(this.yaw)*radius-1.3];const view=mat4.lookAt(eye,[0,1.45,0],[0,1,0]);const proj=mat4.perspective(.73,w/h,.1,70);const vp=mat4.multiply(proj,view);this.viewProj=vp;
    for(const o of this.objects)this.drawObject(o,vp);
    this.layoutOverlayPositions();
  };
  Scene.prototype.projectWorld=function(x,y,z){
    if(!this.viewProj||!this.cssW||!this.cssH)return null;
    const m=this.viewProj,px=x+this.panX,py=y+this.panY;
    const cx=m[0]*px+m[4]*py+m[8]*z+m[12],cy=m[1]*px+m[5]*py+m[9]*z+m[13],cw=m[3]*px+m[7]*py+m[11]*z+m[15];
    if(cw<=.05)return null;
    const nx=cx/cw,ny=cy/cw;
    return{x:(nx*.5+.5)*this.cssW,y:(1-(ny*.5+.5))*this.cssH,visible:nx> -1.3&&nx<1.3&&ny> -1.3&&ny<1.3};
  };
  Scene.prototype.layoutOverlayPositions=function(){
    if(!this.overlay||this.overlay.hidden)return;
    this.stageMeta.forEach((p,i)=>{
      const x=-7.1+i*2.84,q=this.projectWorld(x,3.58,-6.16);
      if(!q||!q.visible){p.el.style.visibility="hidden";return;}
      p.el.style.visibility="visible";p.el.style.left=(q.x/this.cssW*100)+"%";p.el.style.top=(q.y/this.cssH*100)+"%";
    });
    this.monitors.forEach((m,i)=>{
      const row=Math.floor(i/7),col=i%7,x=(col-3)*2.48,z=row===0?-2:3,q=this.projectWorld(x,1.0,z-.48);
      if(!q||!q.visible){m.el.style.visibility="hidden";return;}
      m.el.style.visibility="visible";m.el.style.left=(q.x/this.cssW*100)+"%";m.el.style.top=(q.y/this.cssH*100)+"%";
      m.el.style.transform="translate(-50%,-100%)";
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
  Scene.prototype.setActive=function(active){this.active=!!active;if(this.timer)clearInterval(this.timer);if(this.raf)cancelAnimationFrame(this.raf);this.raf=0;this.layer.hidden=!this.active;this.canvas.hidden=!this.active;if(this.active){this.updateOverlay();this.timer=setInterval(()=>{if(!document.hidden&&this.host.offsetParent!==null)this.updateOverlay();},1000);const tick=()=>{if(!this.active)return;this.render();this.raf=requestAnimationFrame(tick);};this.render();this.raf=requestAnimationFrame(tick);}};
  Scene.prototype.resize=function(){this.cssW=this.host.clientWidth;this.cssH=this.host.clientHeight;if(this.cssW&&this.cssH)this.render();};
  Scene.prototype.zoomAt=function(factor){this.zoom=clamp(this.zoom/factor,.72,2.4);this.render();};
  Scene.prototype.resetView=function(){this.zoom=1;this.yaw=-.18;this.pitch=.28;this.panX=0;this.panY=0;this.render();};
  Scene.prototype.info=function(){return{renderer:"webgl",cells:(this.cells||[]).length,screens:SCREENS.length,lanes:this.model?this.model.lanes.length:0,page:this.page,zoom:this.zoom,cssW:this.cssW,cssH:this.cssH,objects:this.objects?this.objects.length:0};};
  Scene.prototype.projectCell=function(rel){const i=this.screenMeta.findIndex(c=>c.rel===rel);if(i<0)return null;const [x,y,w,h]=SCREENS[i];return{x:(x+w/2)/2048*this.cssW,y:(y+h/2)/1151*this.cssH};};
  Scene.prototype.dispose=function(){this.setActive(false);if(this.resizeObserver)this.resizeObserver.disconnect();if(this.languageObserver)this.languageObserver.disconnect();this.listeners.forEach(fn=>fn());this.pager.remove();if(this.gl){const gl=this.gl;for(const o of [this.box,this.sphere])if(o){gl.deleteBuffer(o.p);gl.deleteBuffer(o.ix);}gl.deleteProgram(this.program);}if(window.__hive3d===this)delete window.__hive3d;};
  const originalCreate=(opts)=>new Scene(opts);
  // Initialize geometry after the constructor has successfully acquired WebGL.
  const create=(opts)=>{const scene=originalCreate(opts);if(scene.gl){scene.buildWorld();scene.render();}return scene;};
  return {create};
})();