/* ============ WebGL 深空 Fragment Shader ============ */

export const VS = `attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}`

export const FS = `precision highp float;
uniform vec2 u_res;uniform float u_time,u_theme,u_fx,u_glow,u_neb,u_nebs,u_par,u_scan,u_star;uniform vec2 u_mouse;
float hash(vec2 p){p=fract(p*vec2(123.34,456.21));p+=dot(p,p+45.32);return fract(p.x*p.y);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);float a=hash(i),b=hash(i+vec2(1,0)),c=hash(i+vec2(0,1)),d=hash(i+vec2(1,1));vec2 u=f*f*(3.-2.*f);return mix(mix(a,b,u.x),mix(c,d,u.x),u.y);}
float fbm(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*noise(p);p*=2.02;a*=.5;}return v;}
void main(){
 vec2 uv=(gl_FragCoord.xy-.5*u_res)/u_res.y;
 vec2 mm=(u_mouse-.5)*.10*(u_par/26.);
 float th=u_theme;
 vec3 base=mix(vec3(.91,.93,.96),vec3(.020,.032,.042),th);
 vec2 lp=vec2(0.,.14)+mm*.6;
 float vig=smoothstep(1.25,.05,length(uv-lp));
 vec3 glow=mix(vec3(.05,.50,.42),vec3(.13,.88,.54),th);
 base+=glow*vig*mix(.08,.26,th)*u_glow;
 float n=fbm(uv*u_nebs+vec2(u_time*.02,-u_time*.015)+mm*2.);
 float n2=fbm(uv*(u_nebs*1.7)-n*1.6+u_time*.03);
 vec3 neb=mix(vec3(.05,.40,.34),vec3(.13,.88,.54),th);
 base+=neb*smoothstep(.46,.92,n2)*mix(.035,.13,th)*u_neb;
 float c2=fbm(uv*3.1+vec2(-u_time*.018,u_time*.012));
 vec3 cold=mix(vec3(.05,.32,.42),vec3(.21,.78,1.0),th);
 base+=cold*smoothstep(.55,.95,c2)*mix(.02,.06,th)*u_neb;
 vec2 g=abs(fract((uv+mm*.5)*7.)-.5);
 float line=1.-smoothstep(0.,.022,min(g.x,g.y));
 vec3 gridc=mix(vec3(.06,.20,.28),vec3(.36,1.,.7),th);
 base+=gridc*line*mix(.07,.035,th)*(0.5+0.5*u_fx);
 vec2 sg=floor((uv+mm*.4)*150.);float s=hash(sg);
 float star=smoothstep(.986,.999,s)*(0.5+0.5*sin(u_time*2.+s*40.));
 base+=vec3(star)*mix(0.,.95,th)*u_star;
 float sy=fract(u_time*.055);float scan=1.-smoothstep(0.,.07,abs(uv.y-(sy*2.6-1.3)));
 base+=glow*scan*mix(.04,.09,th)*u_scan;
 base*=mix(1.,smoothstep(1.35,.25,length(uv)),mix(.35,.62,th));
 gl_FragColor=vec4(base,1.);
}`