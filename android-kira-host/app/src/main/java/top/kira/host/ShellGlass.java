package top.kira.host;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.ColorFilter;
import android.graphics.LinearGradient;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.RadialGradient;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.drawable.Drawable;

/** Iced-jelly surface: translucent fill, bright rim and top gloss over the shimmer backdrop. */
final class ShellGlass extends Drawable {
    private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint blush = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gloss = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint rim = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF box = new RectF(), inner = new RectF();
    private final float cornerSize, hairline;
    private final int top, bottom;
    private int tint, edge, glossAlpha = 0x42;
    private boolean diagonal, openLeft;
    private float radius;
    private int alpha = 255;

    /** Lets ripple and state-list containers copy this surface when a view mutates its background. */
    private final ConstantState state = new ConstantState() {
        @Override public Drawable newDrawable() {
            ShellGlass copy = new ShellGlass(cornerSize, hairline, top, bottom);
            copy.tint = tint; copy.edge = edge; copy.glossAlpha = glossAlpha;
            copy.diagonal = diagonal; copy.openLeft = openLeft; copy.alpha = alpha;
            return copy;
        }
        @Override public int getChangingConfigurations() { return 0; }
    };

    ShellGlass(Context context, float radiusDp, int top, int bottom) {
        this(radiusDp * context.getResources().getDisplayMetrics().density,
            Math.max(1f, context.getResources().getDisplayMetrics().density), top, bottom);
    }

    private ShellGlass(float cornerSize, float hairline, int top, int bottom) {
        this.cornerSize = cornerSize;
        this.hairline = hairline;
        this.top = top;
        this.bottom = bottom;
        rim.setStyle(Paint.Style.STROKE);
        rim.setStrokeWidth(hairline);
    }

    @Override public ConstantState getConstantState() { return state; }

    /** Soft colour bloom rising from the lower right corner. */
    ShellGlass tint(int color) { tint = color; return this; }
    /** Solid hairline instead of the white rim, for surfaces that sit on another light surface. */
    ShellGlass edge(int color) { edge = color; return this; }
    /** Fill runs from the top left to the bottom right, as on coloured jelly buttons. */
    ShellGlass diagonal() { diagonal = true; return this; }
    ShellGlass gloss(int strength) { glossAlpha = Math.max(0, Math.min(255, strength)); return this; }
    /** The left edge runs off-screen, so only the right corners are rounded and clipped. */
    ShellGlass openLeft() { openLeft = true; return this; }

    @Override protected void onBoundsChange(Rect bounds) {
        box.set(bounds);
        radius = Math.min(cornerSize, Math.min(bounds.width(), bounds.height()) / 2f);
        if (openLeft) box.left -= radius * 2f;
        inner.set(box);
        inner.inset(hairline / 2f, hairline / 2f);
        float l = bounds.left, t = bounds.top, r = bounds.right, b = bounds.bottom;
        fill.setShader(new LinearGradient(l, t, diagonal ? r : l, b, top, bottom, Shader.TileMode.CLAMP));
        float sheen = Math.max(1f, Math.min(bounds.height() * .56f, cornerSize * 3.4f));
        gloss.setShader(new LinearGradient(0, t, 0, t + sheen, glossAlpha << 24 | 0xFFFFFF, 0x00FFFFFF, Shader.TileMode.CLAMP));
        rim.setShader(edge != 0 ? null : new LinearGradient(0, t, 0, b,
            new int[]{0xF2FFFFFF, 0x99FFFFFF, 0x73FFFFFF}, new float[]{0f, .5f, 1f}, Shader.TileMode.CLAMP));
        if (edge != 0) rim.setColor(edge);
        blush.setShader(tint == 0 ? null : new RadialGradient(r, b, Math.max(1f, bounds.width() * .9f),
            (tint & 0x00FFFFFF) | 0x30000000, tint & 0x00FFFFFF, Shader.TileMode.CLAMP));
        applyAlpha();
    }

    @Override public void draw(Canvas canvas) {
        canvas.drawRoundRect(box, radius, radius, fill);
        if (tint != 0) canvas.drawRoundRect(box, radius, radius, blush);
        if (glossAlpha > 0) canvas.drawRoundRect(box, radius, radius, gloss);
        float corner = Math.max(0f, radius - hairline / 2f);
        canvas.drawRoundRect(inner, corner, corner, rim);
    }

    /** Opaque outline: the tinted elevation shadow is cast around the jelly, never seen through it. */
    @Override public void getOutline(Outline outline) {
        Rect bounds = getBounds();
        outline.setRoundRect(Math.round(box.left), bounds.top, bounds.right, bounds.bottom, radius);
        outline.setAlpha(1f);
    }

    private void applyAlpha() {
        fill.setAlpha(alpha);
        blush.setAlpha(alpha);
        gloss.setAlpha(alpha);
        rim.setAlpha(edge != 0 ? (edge >>> 24) * alpha / 255 : alpha);
    }

    @Override public void setAlpha(int value) {
        if (alpha == value) return;
        alpha = value;
        applyAlpha();
        invalidateSelf();
    }

    @Override public int getAlpha() { return alpha; }

    @Override public void setColorFilter(ColorFilter filter) {
        fill.setColorFilter(filter);
        blush.setColorFilter(filter);
        rim.setColorFilter(filter);
        invalidateSelf();
    }

    @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
}
