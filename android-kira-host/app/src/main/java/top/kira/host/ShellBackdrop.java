package top.kira.host;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.view.View;

/** Static generated shimmer. It never intercepts input or runs a decoration loop. */
final class ShellBackdrop extends View {
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Matrix imageMatrix=new Matrix();
    private final Bitmap wallpaper;
    ShellBackdrop(Context context) {
        super(context);setClickable(false);setFocusable(false);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        BitmapFactory.Options options=new BitmapFactory.Options();
        options.inScaled=false;
        wallpaper=BitmapFactory.decodeResource(getResources(),R.drawable.shell_shimmer_background,options);
    }
    @Override protected void onSizeChanged(int width,int height,int oldWidth,int oldHeight) {
        super.onSizeChanged(width,height,oldWidth,oldHeight);
        if(wallpaper==null||width<1||height<1)return;
        float scale=Math.max(width/(float)wallpaper.getWidth(),height/(float)wallpaper.getHeight());
        imageMatrix.setScale(scale,scale);
        imageMatrix.postTranslate((width-wallpaper.getWidth()*scale)*.5f,(height-wallpaper.getHeight()*scale)*.5f);
    }
    @Override protected void onDraw(Canvas canvas) {
        canvas.drawColor(0xFFF2E7EE);
        if(wallpaper!=null)canvas.drawBitmap(wallpaper,imageMatrix,paint);
    }
    /** Kept for deterministic screenshot probes; no perpetual decoration animator is created. */
    private void stopAnimator() {animate().cancel();setAlpha(1);}
    @Override protected void onDetachedFromWindow(){stopAnimator();super.onDetachedFromWindow();}
}
