package top.kira.host;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.util.SparseArray;
import android.view.View;

/** Jelly tiles with rounded white glyphs, sharing one silhouette with the official component icons. */
final class ShellIcons {
    static final int ENVIRONMENT=0, CORE=1, BRIDGE=2, KEY=3, REFRESH=4, BACKUP=5,
        UPDATE=6, PACKAGE=7, LOG=8, TERMINAL=9, BATTERY=10, SETTINGS=11, ABOUT=12,
        MENU=13, CLOSE=14, CONNECT=15, DEVICE=16, HERO=17, UNINSTALL=18;
    private static final int WHITE=0xFFFFFFFF, INK=0xFF5A4263, GOLD=0xFFFFCB7A;
    private static final int[] ROSE={0xFFF9A6CA,0xFFE0669F}, LILAC={0xFFC5ABF7,0xFF8F70DC},
        PEACH={0xFFFDB59B,0xFFEC7B8D};
    /** Every tile and both official icons use this rounded square inside a 64-unit box. */
    private static final float EDGE=5f, CORNER=12f;
    private static final SparseArray<Bitmap> CACHE=new SparseArray<>();
    private static final Paint BLIT=new Paint(Paint.FILTER_BITMAP_FLAG);
    private static volatile Bitmap kiraIcon, napcatIcon;

    /** Canvas callers initialize once; original artwork stays untinted inside rounded corners. */
    static synchronized void initialize(Context context) {
        BitmapFactory.Options options=new BitmapFactory.Options();
        options.inScaled=false;
        options.inSampleSize=4;
        if(kiraIcon==null) kiraIcon=BitmapFactory.decodeResource(context.getResources(),R.drawable.official_kira,options);
        if(napcatIcon==null) napcatIcon=BitmapFactory.decodeResource(context.getResources(),R.drawable.official_napcat,options);
    }

    static View view(Context context,int kind) {
        initialize(context);
        return new Illustration(context,kind);
    }

    static int actionKind(int action) {
        switch(action) {
            case 0:case 14:return CORE;
            case 1:return BRIDGE;
            case 2:case 12:return REFRESH;
            case 3:return BACKUP;
            case 4:return UPDATE;
            case 5:return PACKAGE;
            case 6:return LOG;
            case 8:return TERMINAL;
            case 9:return BATTERY;
            case 10:return ABOUT;
            case 11:return SETTINGS;
            case 13:return UNINSTALL;
            default:return ENVIRONMENT;
        }
    }

    static final class Illustration extends View {
        private final int kind;
        Illustration(Context context,int kind) {
            super(context);this.kind=kind;
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
            setFocusable(false);setClickable(false);
        }
        @Override protected void onDraw(Canvas canvas) {
            float side=Math.min(getWidth(),getHeight());
            ShellIcons.draw(canvas,kind,(getWidth()-side)/2f,(getHeight()-side)/2f,side);
        }
    }

    /** Icons are rasterized once per size; animated callers such as the dock only blit. */
    static void draw(Canvas canvas,int kind,float left,float top,float size) {
        int pixels=Math.round(size);
        if(pixels<1||pixels>2048) return;
        Bitmap icon;
        synchronized(CACHE) {
            int key=kind<<12|pixels;
            icon=CACHE.get(key);
            if(icon==null) {
                icon=Bitmap.createBitmap(pixels,pixels,Bitmap.Config.ARGB_8888);
                Canvas target=new Canvas(icon);
                target.scale(pixels/64f,pixels/64f);
                paint(target,kind,new Paint(Paint.ANTI_ALIAS_FLAG|Paint.FILTER_BITMAP_FLAG),new Path());
                CACHE.put(key,icon);
            }
        }
        canvas.drawBitmap(icon,left,top,BLIT);
    }

    private static void paint(Canvas c,int kind,Paint p,Path path) {
        p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);
        RectF box=new RectF(EDGE,EDGE,64-EDGE,64-EDGE);
        switch(kind) {
            case CORE:case BRIDGE:official(c,kind,p,path,box);return;
            case MENU:
                // 三道圆头横线，中间一道收短并带一颗玫红圆点。
                stroke(p,INK,5f);c.drawLine(16,21,48,21,p);c.drawLine(16,32,37,32,p);c.drawLine(16,43,48,43,p);
                fill(p,ShellTheme.PRIMARY);c.drawCircle(46.2f,32,2.9f,p);return;
            case CLOSE:
                stroke(p,INK,4.4f);c.drawLine(23.5f,23.5f,40.5f,40.5f,p);c.drawLine(40.5f,23.5f,23.5f,40.5f,p);return;
            case HERO:hero(c,p,path);return;
            default:
        }
        tile(c,p,box,palette(kind));
        switch(kind) {
            case ENVIRONMENT:cloud(c,p,ROSE[1]);sparkle(c,p,path,46.5f,19.5f,4.2f,WHITE);break;
            case KEY:
                c.save();c.rotate(-40,32,32);
                stroke(p,WHITE,4.2f);c.drawCircle(22.5f,32,6.8f,p);c.drawLine(29.6f,32,48,32,p);
                c.drawLine(40.6f,32,40.6f,38.4f,p);c.drawLine(47,32,47,37,p);
                c.restore();break;
            case REFRESH:
                stroke(p,WHITE,4.2f);
                c.drawArc(20.5f,20.5f,43.5f,43.5f,205,118,false,p);c.drawArc(20.5f,20.5f,43.5f,43.5f,25,118,false,p);
                arrow(c,p,32,32,11.5f,323);arrow(c,p,32,32,11.5f,143);break;
            case BACKUP:
                fill(p,WHITE);c.drawRoundRect(17,19.5f,47,27.5f,3.2f,3.2f,p);
                stroke(p,WHITE,3.8f);
                path.reset();path.moveTo(20,31);path.lineTo(20,41);path.quadTo(20,45,24,45);path.lineTo(40,45);path.quadTo(44,45,44,41);path.lineTo(44,31);
                c.drawPath(path,p);
                stroke(p,WHITE,3.4f);c.drawLine(28.5f,35.5f,35.5f,35.5f,p);break;
            case UPDATE:
                stroke(p,WHITE,4f);
                path.reset();path.moveTo(19,37);path.lineTo(19,41);path.quadTo(19,45,23,45);path.lineTo(41,45);path.quadTo(45,45,45,41);path.lineTo(45,37);
                c.drawPath(path,p);
                c.drawLine(32,37.5f,32,19.5f,p);
                path.reset();path.moveTo(25,26.2f);path.lineTo(32,19.2f);path.lineTo(39,26.2f);c.drawPath(path,p);break;
            case PACKAGE:
                stroke(p,WHITE,3.8f);
                path.reset();path.moveTo(32,17.5f);path.lineTo(44.6f,24.6f);path.lineTo(44.6f,39.4f);path.lineTo(32,46.5f);
                path.lineTo(19.4f,39.4f);path.lineTo(19.4f,24.6f);path.close();c.drawPath(path,p);
                path.reset();path.moveTo(19.4f,24.6f);path.lineTo(32,31.8f);path.lineTo(44.6f,24.6f);path.moveTo(32,31.8f);path.lineTo(32,46.5f);
                c.drawPath(path,p);break;
            case LOG:
                stroke(p,WHITE,3.8f);c.drawRoundRect(20.5f,17,43.5f,47,5.5f,5.5f,p);
                stroke(p,WHITE,3.2f);c.drawLine(26.5f,26,37.5f,26,p);c.drawLine(26.5f,32,37.5f,32,p);c.drawLine(26.5f,38,32.5f,38,p);break;
            case TERMINAL:
                stroke(p,WHITE,3.8f);c.drawRoundRect(16.5f,20,47.5f,44,6,6,p);
                path.reset();path.moveTo(23.5f,27.5f);path.lineTo(28.8f,32);path.lineTo(23.5f,36.5f);c.drawPath(path,p);
                c.drawLine(32.5f,36.5f,39.5f,36.5f,p);break;
            case BATTERY:
                stroke(p,WHITE,3.8f);c.drawRoundRect(16,23.5f,44,40.5f,5.5f,5.5f,p);
                fill(p,WHITE);c.drawRoundRect(46,28.5f,49,35.5f,1.5f,1.5f,p);
                c.drawRoundRect(20.2f,27.7f,33.5f,36.3f,2.4f,2.4f,p);break;
            case SETTINGS:
                // 两道调节滑杆：白色滑轨上各有一枚实心旋钮，旋钮外留一圈底色把它和滑轨分开。
                stroke(p,WHITE,3.6f);c.drawLine(18.5f,25,45.5f,25,p);c.drawLine(18.5f,39,45.5f,39,p);
                fill(p,palette(kind)[1]);c.drawCircle(38,25,7.2f,p);c.drawCircle(26,39,7.2f,p);
                fill(p,WHITE);c.drawCircle(38,25,4.9f,p);c.drawCircle(26,39,4.9f,p);break;
            case ABOUT:
                stroke(p,WHITE,3.8f);c.drawCircle(32,32,13,p);
                fill(p,WHITE);c.drawCircle(32,25.4f,2.4f,p);
                stroke(p,WHITE,4.2f);c.drawLine(32,31.4f,32,39.2f,p);break;
            case CONNECT:
                stroke(p,WHITE,3.8f);
                path.reset();path.moveTo(29,21.5f);path.lineTo(24,21.5f);path.quadTo(20,21.5f,20,25.5f);path.lineTo(20,40);
                path.quadTo(20,44,24,44);path.lineTo(38.5f,44);path.quadTo(42.5f,44,42.5f,40);path.lineTo(42.5f,35);c.drawPath(path,p);
                c.drawLine(32.5f,31.5f,45.5f,18.5f,p);
                path.reset();path.moveTo(36.5f,18.5f);path.lineTo(45.5f,18.5f);path.lineTo(45.5f,27.5f);c.drawPath(path,p);break;
            case UNINSTALL:
                // 圆角垃圾桶：桶盖、提手和两道竖线，表示移除程序。
                stroke(p,WHITE,3.6f);c.drawLine(19,23.5f,45,23.5f,p);
                path.reset();path.moveTo(28,23.5f);path.lineTo(28,20.5f);path.quadTo(28,18.5f,30,18.5f);path.lineTo(34,18.5f);
                path.quadTo(36,18.5f,36,20.5f);path.lineTo(36,23.5f);c.drawPath(path,p);
                path.reset();path.moveTo(22.5f,27);path.lineTo(24,43);path.quadTo(24.4f,46,27.4f,46);path.lineTo(36.6f,46);
                path.quadTo(39.6f,46,40,43);path.lineTo(41.5f,27);c.drawPath(path,p);
                stroke(p,WHITE,3f);c.drawLine(29.2f,31.5f,29.6f,40.5f,p);c.drawLine(34.8f,31.5f,34.4f,40.5f,p);break;
            case DEVICE:
                // 手机轮廓，屏幕里一道心跳线表示运行状态，底部是手势条。
                stroke(p,WHITE,3.8f);c.drawRoundRect(21,15.5f,43,48.5f,6,6,p);
                stroke(p,WHITE,2.8f);
                path.reset();path.moveTo(25.8f,31.5f);path.lineTo(28.8f,31.5f);path.lineTo(30.8f,26.5f);path.lineTo(33.4f,35.5f);
                path.lineTo(35.4f,31.5f);path.lineTo(38.2f,31.5f);c.drawPath(path,p);
                c.drawLine(29.5f,42.8f,34.5f,42.8f,p);break;
            default:
        }
    }

    private static int[] palette(int kind) {
        switch(kind) {
            case KEY:case SETTINGS:case TERMINAL:return LILAC;
            case UPDATE:case PACKAGE:case BATTERY:case ABOUT:
            case REFRESH:case CONNECT:case LOG:case DEVICE:case UNINSTALL:return PEACH;
            default:return ROSE;
        }
    }

    /** Coloured jelly square: soft contact shadow, vertical gradient, top gloss and a light rim. */
    private static void tile(Canvas c,Paint p,RectF box,int[] colors) {
        shadow(c,p,box,colors[1]);
        p.setStyle(Paint.Style.FILL);p.setColor(WHITE);
        p.setShader(new LinearGradient(0,box.top,0,box.bottom,colors[0],colors[1],Shader.TileMode.CLAMP));
        c.drawRoundRect(box,CORNER,CORNER,p);
        p.setShader(new LinearGradient(0,box.top,0,box.top+box.height()*.55f,0x59FFFFFF,0x00FFFFFF,Shader.TileMode.CLAMP));
        c.drawRoundRect(box,CORNER,CORNER,p);
        rim(c,p,box,0xB3FFFFFF);
    }

    private static void shadow(Canvas c,Paint p,RectF box,int color) {
        p.setShader(null);p.setStyle(Paint.Style.FILL);
        p.setColor(0x16000000|(color&0x00FFFFFF));c.drawRoundRect(box.left+1.2f,box.top+3.2f,box.right-1.2f,box.bottom+3.4f,CORNER,CORNER,p);
        p.setColor(0x24000000|(color&0x00FFFFFF));c.drawRoundRect(box.left+.5f,box.top+1.6f,box.right-.5f,box.bottom+1.7f,CORNER,CORNER,p);
    }

    private static void rim(Canvas c,Paint p,RectF box,int color) {
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.1f);p.setColor(WHITE);
        p.setShader(new LinearGradient(0,box.top,0,box.bottom,color,color&0x00FFFFFF|0x26000000,Shader.TileMode.CLAMP));
        c.drawRoundRect(box.left+.55f,box.top+.55f,box.right-.55f,box.bottom-.55f,CORNER-.55f,CORNER-.55f,p);
        p.setShader(null);
    }

    /** Official artwork is only masked: Kira loses its black JPEG corners, NapCat its uneven outer frame line. */
    private static void official(Canvas c,int kind,Paint p,Path path,RectF box) {
        Bitmap icon=kind==CORE?kiraIcon:napcatIcon;
        if(icon==null) return;
        shadow(c,p,box,kind==CORE?0xFFD0527A:ShellTheme.SHADE);
        int w=icon.getWidth(),h=icon.getHeight();
        Rect source=kind==CORE?new Rect(0,0,w,h)
            :new Rect(Math.round(w*.0674f),Math.round(h*.0664f),Math.round(w*.9326f),Math.round(h*.9316f));
        int saved=c.save();
        path.reset();path.addRoundRect(box,CORNER,CORNER,Path.Direction.CW);
        c.clipPath(path);
        p.setShader(null);p.setColorFilter(null);p.setStyle(Paint.Style.FILL);p.setColor(WHITE);
        c.drawBitmap(icon,source,box,p);
        c.restoreToCount(saved);
        rim(c,p,box,0x8CFFFFFF);
    }

    /** Large status piece: a tilted cloud tile floating in a rose glow with three small stars. */
    private static void hero(Canvas c,Paint p,Path path) {
        p.setStyle(Paint.Style.FILL);p.setColor(WHITE);
        p.setShader(new android.graphics.RadialGradient(32,35,29,new int[]{0x59F08DBB,0x26A98BEA,0x00A98BEA},
            new float[]{0f,.62f,1f},Shader.TileMode.CLAMP));
        c.drawCircle(32,35,29,p);p.setShader(null);
        c.save();c.rotate(-8,32,34);c.scale(.78f,.78f,32,34);
        RectF box=new RectF(EDGE,EDGE+2,64-EDGE,66-EDGE);
        tile(c,p,box,ROSE);
        c.translate(0,2);cloud(c,p,ROSE[1]);
        c.restore();
        sparkle(c,p,path,53.5f,13.5f,6.2f,GOLD);sparkle(c,p,path,9.5f,50.5f,4f,0xFFB79CF2);sparkle(c,p,path,57,45,2.6f,GOLD);
    }

    private static void cloud(Canvas c,Paint p,int lights) {
        fill(p,WHITE);
        c.drawCircle(27.5f,29.5f,8.3f,p);c.drawCircle(38.2f,31.6f,6.4f,p);c.drawRoundRect(17,31.6f,47.5f,43.2f,5.8f,5.8f,p);
        fill(p,lights);c.drawCircle(24.6f,37.6f,1.9f,p);c.drawCircle(30.4f,37.6f,1.9f,p);
        stroke(p,lights,2.5f);c.drawLine(36.6f,37.6f,41.4f,37.6f,p);
    }

    /** Arrow head for an arc drawn clockwise around (cx, cy), placed at the given end angle. */
    private static void arrow(Canvas c,Paint p,float cx,float cy,float radius,float angle) {
        double a=Math.toRadians(angle);
        float x=cx+radius*(float)Math.cos(a),y=cy+radius*(float)Math.sin(a);
        float tx=-(float)Math.sin(a),ty=(float)Math.cos(a);
        for(int side=-1;side<=1;side+=2) {
            double turn=Math.toRadians(side*38);
            float bx=(float)(tx*Math.cos(turn)-ty*Math.sin(turn)),by=(float)(tx*Math.sin(turn)+ty*Math.cos(turn));
            c.drawLine(x+tx*1.2f,y+ty*1.2f,x+tx*1.2f-bx*6.2f,y+ty*1.2f-by*6.2f,p);
        }
    }

    private static void sparkle(Canvas c,Paint p,Path path,float x,float y,float radius,int color) {
        path.reset();path.moveTo(x,y-radius);
        path.quadTo(x+radius*.16f,y-radius*.16f,x+radius,y);
        path.quadTo(x+radius*.16f,y+radius*.16f,x,y+radius);
        path.quadTo(x-radius*.16f,y+radius*.16f,x-radius,y);
        path.quadTo(x-radius*.16f,y-radius*.16f,x,y-radius);
        path.close();fill(p,color);c.drawPath(path,p);
        // 细描边把四个尖角磨圆，星形不再有针尖。
        stroke(p,color,radius*.16f);c.drawPath(path,p);
    }
    private static void fill(Paint p,int color) {p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(color);}
    private static void stroke(Paint p,int color,float width) {p.setShader(null);p.setStyle(Paint.Style.STROKE);p.setColor(color);p.setStrokeWidth(width);}
}
