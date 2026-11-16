package top.kira.host;

import android.animation.ValueAnimator;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.RippleDrawable;
import android.text.TextPaint;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.View;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import android.widget.LinearLayout;

/** Fixed touch targets; only the selection indicator changes shape in transit. */
public final class ShellNavigation extends LinearLayout {
    public interface Listener { void onSelect(int index); }
    private static final int INK = ShellTheme.INK, ACCENT = ShellTheme.ACCENT;
    private static final String[] TITLES = {"状态", "KiraAI", "NapCat"};
    private final Item[] items = new Item[TITLES.length];
    private final Paint indicatorPaint = new Paint(Paint.ANTI_ALIAS_FLAG), indicatorRim = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF indicator = new RectF();
    private int indicatorHeight = -1;
    /** 胶囊到容器四边的间距处处相等，内外两条圆弧才同心：内圆角 = 外圆角 - GAP。 */
    private static final int GAP = 5;
    private ValueAnimator animator;
    private float leading, trailing = 1f;
    private int selected;
    private boolean disposed;
    /** 0 = 日间，1 = 夜间（跟随 KiraAI 面板）；中间值是切换途中的渐变。 */
    private float night, indicatorNight = -1;
    private final ShellGlass nightSheet;

    public ShellNavigation(Context context, Listener listener) {
        super(context);
        ShellIcons.initialize(context);
        setOrientation(HORIZONTAL);
        setGravity(Gravity.CENTER_VERTICAL);
        setPadding(dp(GAP), dp(GAP), dp(GAP), dp(GAP));
        setWillNotDraw(false);
        setMinimumHeight(dp(68));
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        // 夜间底板叠在日间底板上，只改它的透明度，两套外观之间平滑渐变。
        nightSheet = ShellTheme.nightFrost(context, 34);
        nightSheet.setAlpha(0);
        setBackground(new LayerDrawable(new Drawable[]{ShellTheme.frost(context, 34), nightSheet}));
        ShellTheme.lift(this, 10);
        indicatorRim.setStyle(Paint.Style.STROKE); indicatorRim.setStrokeWidth(dp(1));
        for (int i = 0; i < items.length; i++) {
            final int target = i;
            Item item = new Item(context, i);
            items[i] = item;
            item.setSelected(i == 0);
            item.amount = i == 0 ? 1f : 0f;
            item.setOnClickListener(view -> { if (!disposed) listener.onSelect(target); });
            addView(item, new LayoutParams(0, -1, 1));
        }
    }

    public void select(int index, boolean animate) {
        if (disposed || index < 0 || index >= items.length || index == selected) return;
        cancelAnimation();
        selected = index;
        for (int i = 0; i < items.length; i++) items[i].setSelected(i == index);
        if (!animate || !isAttachedToWindow() || !ValueAnimator.areAnimatorsEnabled()) { settle(); return; }
        float fromLeft = leading, fromRight = trailing;
        float[] amount = new float[items.length];
        for (int i = 0; i < items.length; i++) amount[i] = items[i].amount;
        boolean forward = index + .5f > (leading + trailing) / 2f;
        ValueAnimator next = ValueAnimator.ofFloat(0, 1);
        animator = next;
        next.setDuration(ShellTheme.PANEL);
        next.setInterpolator(ShellTheme.BOTH);
        // 水滴导航：前沿先到，后沿延迟跟随；点击区域始终三等分。
        next.addUpdateListener(value -> {
            float t = value.getAnimatedFraction();
            float front = ShellTheme.ENTER.getInterpolation(t);
            float back = ShellTheme.ENTER.getInterpolation(Math.max(0, (t - .15f) / .85f));
            leading = fromLeft + (index - fromLeft) * (forward ? back : front);
            trailing = fromRight + (index + 1 - fromRight) * (forward ? front : back);
            float phase = (float) value.getAnimatedValue();
            for (int i = 0; i < items.length; i++) {
                items[i].amount = amount[i] + ((i == selected ? 1f : 0f) - amount[i]) * phase;
                items[i].invalidate();
            }
            invalidate();
        });
        next.start();
    }

    public void setPanelStatus(int index, boolean ready, boolean available, String health) {
        if (index == 1 || index == 2) items[index].setPanelStatus(ready, available, health);
    }
    public void setCompact(boolean compact) {
        setPadding(dp(GAP), dp(compact ? 0 : GAP), dp(GAP), dp(compact ? 0 : GAP));
        setMinimumHeight(dp(compact ? 48 : 56));
    }
    public void setNight(float value) {
        value = Math.max(0f, Math.min(1f, value));
        if (value == night) return;
        night = value;
        nightSheet.setAlpha(Math.round(255 * value));
        for (Item item : items) item.invalidate();
        invalidate();
    }
    public void dispose() { disposed = true; cancelAnimation(); settle(); }
    private void cancelAnimation() {
        if (animator != null) { animator.removeAllUpdateListeners(); animator.cancel(); animator = null; }
    }
    private void settle() {
        leading = selected; trailing = selected + 1f;
        for (int i = 0; i < items.length; i++) { items[i].amount = i == selected ? 1f : 0f; items[i].invalidate(); }
        invalidate();
    }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float unit = (getWidth() - getPaddingLeft() - getPaddingRight()) / 3f;
        float gap = dp(GAP);
        indicator.set(getPaddingLeft() + leading * unit, gap, getPaddingLeft() + trailing * unit, getHeight() - gap);
        if (indicatorHeight != getHeight() || indicatorNight != night) {
            // 果冻胶囊：同一色相的淡玫瑰、同色柔光和一圈亮边；着色器只在高度变化时重建。
            indicatorHeight = getHeight(); indicatorNight = night;
            indicatorPaint.setShader(new LinearGradient(0, gap, 0, getHeight() - gap,
                blend(0xFFFFE9F2, 0xFF5E3F5E, night), blend(0xFFFBDBE9, 0xFF553955, night), Shader.TileMode.CLAMP));
            indicatorPaint.setShadowLayer(dp(5), 0, dp(1.5f), 0x38D9529A);
            indicatorRim.setShader(new LinearGradient(0, gap, 0, getHeight() - gap,
                blendArgb(0xFFFFFFFF, 0x40FFFFFF, night), blendArgb(0x80FFFFFF, 0x14FFFFFF, night), Shader.TileMode.CLAMP));
        }
        float corner = (indicator.height()) / 2f, half = dp(1) / 2f;
        canvas.drawRoundRect(indicator, corner, corner, indicatorPaint);
        canvas.drawRoundRect(indicator.left + half, indicator.top + half, indicator.right - half, indicator.bottom - half,
            corner - half, corner - half, indicatorRim);
    }
    @Override protected void onDetachedFromWindow() { cancelAnimation(); settle(); super.onDetachedFromWindow(); }
    private int dp(float size) { return ShellTheme.dp(getContext(), size); }

    private final class Item extends View {
        final int index;
        final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        final TextPaint textPaint = new TextPaint(Paint.ANTI_ALIAS_FLAG);
        final Path icon = new Path();
        float amount;
        boolean ready;
        String health = "unknown";
        Item(Context context, int index) {
            super(context); this.index = index;
            setClickable(true); setFocusable(true); setMinimumHeight(dp(48));
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
            setTooltipText(TITLES[index]); updateDescription();
            textPaint.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            setForeground(new RippleDrawable(ColorStateList.valueOf(0x1A9D5F86), null,
                ShellTheme.surface(context, Color.WHITE, 6, Color.TRANSPARENT)));
            ShellDialogs.pressFeedback(this);
        }
        void setPanelStatus(boolean ready, boolean available, String health) {
            boolean enabled = !ready || available;
            if (this.ready == ready && this.health.equals(health) && isEnabled() == enabled) return;
            this.ready = ready;
            this.health = health;
            setEnabled(enabled);
            updateDescription();
            invalidate();
        }
        void updateDescription() {
            String state = ready ? RuntimeStatus.panelHealthLabel(health) : "官方面板尚未就绪";
            setContentDescription(index > 0 ? TITLES[index] + "，" + state : TITLES[index]);
        }
        @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
            super.onInitializeAccessibilityNodeInfo(info); info.setClassName(Button.class.getName()); info.setSelected(isSelected());
        }
        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int color = blend(ShellTheme.MUTED, ACCENT, amount);
            float center = getWidth() / 2f;
            float iconY = getHeight() * .36f;
            // 选中项的图标略微放大并上浮，和胶囊一起到位。
            int saved = canvas.save();
            canvas.translate(0, -dp(1) * amount);
            canvas.scale(1f + .06f * amount, 1f + .06f * amount, center, iconY);
            drawIcon(canvas, center, iconY, color);
            canvas.restoreToCount(saved);
            if (index > 0) {
                int colorForHealth = ShellTheme.DISABLED;
                if (ready && "reachable".equals(health)) colorForHealth = ShellTheme.ACCENT;
                else if (ready && "unknown".equals(health)) colorForHealth = ShellTheme.TEAL;
                // 面板状态点压在图标右下角，白色描边把它从图案上分开。
                paint.setStyle(Paint.Style.FILL); paint.setColor(blend(Color.WHITE, ShellTheme.NIGHT_RAISED, night));
                canvas.drawCircle(center + dp(13), iconY + dp(12), dp(4.5f), paint);
                paint.setColor(colorForHealth);
                canvas.drawCircle(center + dp(13), iconY + dp(12), dp(3), paint);
            }
            textPaint.setColor(blend(blend(ShellTheme.MUTED, ShellTheme.NIGHT_MUTED, night), blend(INK, ShellTheme.NIGHT_INK, night), amount));
            textPaint.setTextSize(11 * getResources().getDisplayMetrics().scaledDensity);
            float room = Math.max(1, getWidth() - dp(8));
            if (textPaint.measureText(TITLES[index]) > room) textPaint.setTextSize(textPaint.getTextSize() * room / textPaint.measureText(TITLES[index]));
            Paint.FontMetrics metrics = textPaint.getFontMetrics();
            canvas.drawText(TITLES[index], center - textPaint.measureText(TITLES[index]) / 2,
                getHeight() * .78f - (metrics.ascent + metrics.descent) / 2, textPaint);
            if (hasFocus()) {
                paint.setStyle(Paint.Style.STROKE); paint.setColor(ACCENT); paint.setStrokeWidth(dp(1));
                canvas.drawRoundRect(dp(2), dp(2), getWidth()-dp(2), getHeight()-dp(2), dp(6), dp(6), paint);
            }
        }
        private void drawIcon(Canvas canvas,float centerX,float centerY,int color) {
            // 状态使用玫瑰果冻图标；Kira/NapCat保留官方原图，三者同一圆角轮廓，选中反馈由胶囊承担。
            int kind=index==0?ShellIcons.ENVIRONMENT:index==1?ShellIcons.CORE:ShellIcons.BRIDGE;
            ShellIcons.draw(canvas,kind,centerX-dp(18),centerY-dp(18),dp(36));
        }
    }

    private static int blendArgb(int from, int to, float fraction) {
        int alpha = Math.round(Color.alpha(from) + (Color.alpha(to) - Color.alpha(from)) * fraction);
        return (alpha << 24) | (blend(from, to, fraction) & 0xFFFFFF);
    }

    private static int blend(int from, int to, float fraction) {
        return Color.rgb(Math.round(Color.red(from) + (Color.red(to) - Color.red(from)) * fraction),
            Math.round(Color.green(from) + (Color.green(to) - Color.green(from)) * fraction),
            Math.round(Color.blue(from) + (Color.blue(to) - Color.blue(from)) * fraction));
    }
}
