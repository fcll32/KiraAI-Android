package top.kira.host;

import android.animation.ObjectAnimator;
import android.animation.StateListAnimator;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.ClipDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.LayerDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.view.animation.PathInterpolator;
import android.widget.Button;
import android.widget.ProgressBar;

/** Shared presentation tokens; component state stays with its existing owners. */
final class ShellTheme {
    static final int PAGE = 0xFFF6EAF2, SURFACE = 0xFFFFFBFE, INK = 0xFF4D3A55;
    static final int MUTED = 0xFF6E5A75, LINE = 0xFFEAD5E5, SOFT = 0xFFF8EDF5;
    static final int ACCENT = 0xFFA83877, TEAL = 0xFF7652A3, NIGHT = 0xFF46334E;
    static final int PEARL_END = 0xFFF7EDF6, PRIMARY = 0xFFB84484;
    static final int ROSE_SOFT = 0xFFF8E0EC, LILAC_SOFT = 0xFFEEE6F9, DISABLED = 0xFFA390A5;
    /** Jelly blooms and the violet-rose cast of every floating shadow. */
    static final int BLOOM_ROSE = 0xFFF08DBB, BLOOM_LILAC = 0xFFA98BEA, SHADE = 0xFF8A4F96, GLOW = 0xFFD9529A;
    /** 夜间外观：与注入面板样式（shell-theme.css 的 :root.dark）同一套色值，标题栏、底栏和系统栏跟随 KiraAI 面板切换。 */
    static final int NIGHT_PAGE = 0xFF302635, NIGHT_SURFACE = 0xFF3A2D40, NIGHT_RAISED = 0xFF44344B;
    static final int NIGHT_INK = 0xFFFFF6FC, NIGHT_MUTED = 0xFFCBB3CD, NIGHT_LINE = 0xFF5D4865;
    /** 日间系统栏底色（与 HostTheme 一致），夜间渐变的起点。 */
    static final int DAY_STATUS = 0xFFF2E7EE, DAY_NAVIGATION = 0xFFF3E7EF;
    static final PathInterpolator ENTER = new PathInterpolator(.22f, 1f, .36f, 1f);
    static final PathInterpolator EXIT = new PathInterpolator(.4f, 0f, 1f, 1f);
    static final PathInterpolator BOTH = new PathInterpolator(.4f, 0f, .2f, 1f);
    /** Settles a little past the target and eases back, like set jelly. */
    static final PathInterpolator JELLY = new PathInterpolator(.3f, 1.42f, .5f, 1f);
    static final long PANEL = 400, CLOSE = 300, ITEM = 220, STAGGER = 40, FLIP = 600;
    static int dp(Context context, float size) { return Math.round(size * context.getResources().getDisplayMetrics().density); }
    static GradientDrawable surface(Context context, int color, int radius, int border) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(context, radius));
        if (border != Color.TRANSPARENT) shape.setStroke(dp(context, 1), border);
        return shape;
    }
    static GradientDrawable gradient(Context context, int first, int second, int radius, int border) {
        GradientDrawable shape = new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[]{first,second});
        shape.setCornerRadius(dp(context,radius));
        if (border != Color.TRANSPARENT) shape.setStroke(dp(context,1),border);
        return shape;
    }
    /** Milky card that picks up the backdrop's blush. Fills stay opaque: an elevated see-through body would show its own shadow as a dark inner ring. */
    static ShellGlass glass(Context context, int radius) { return new ShellGlass(context, radius, 0xFFFFFCFE, 0xFFFBEFF7); }
    /** Whiter sheet for layered decks, bars and floating rails. */
    static ShellGlass frost(Context context, int radius) { return new ShellGlass(context, radius, 0xFFFFFFFF, 0xFFFDF5FA); }
    /** Night counterpart of frost: the panel's raised dark surface with a hairline, no gloss. */
    static ShellGlass nightFrost(Context context, int radius) { return new ShellGlass(context, radius, NIGHT_RAISED, NIGHT_SURFACE).edge(NIGHT_LINE).gloss(0); }
    static int blend(int from, int to, float fraction) {
        return Color.argb(Math.round(Color.alpha(from) + (Color.alpha(to) - Color.alpha(from)) * fraction),
            Math.round(Color.red(from) + (Color.red(to) - Color.red(from)) * fraction),
            Math.round(Color.green(from) + (Color.green(to) - Color.green(from)) * fraction),
            Math.round(Color.blue(from) + (Color.blue(to) - Color.blue(from)) * fraction));
    }
    /** Tinted elevation: a soft violet-rose halo instead of the default grey. */
    static void lift(View view, float height) {
        view.setElevation(dp(view.getContext(), height));
        view.setOutlineAmbientShadowColor(SHADE); view.setOutlineSpotShadowColor(SHADE);
    }
    static int tint(int accent) {
        return Color.rgb((Color.red(accent) + 255 * 9) / 10, (Color.green(accent) + 255 * 9) / 10, (Color.blue(accent) + 255 * 9) / 10);
    }
    static void button(Button button, int accent, boolean primary) {
        Context context = button.getContext(); button.setAllCaps(false); button.setTextSize(14);
        button.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        button.setMinWidth(0); button.setMinimumWidth(0);
        button.setMinHeight(dp(context, 48)); button.setMinimumHeight(dp(context, 48));
        button.setPadding(dp(context, 12), dp(context, 6), dp(context, 12), dp(context, 6));
        button.setBackgroundTintList(null);
        StateListDrawable states = new StateListDrawable();
        states.addState(new int[]{-android.R.attr.state_enabled}, surface(context, 0xFFF4E8F1, 16, 0xFFF9F0F6));
        // 主操作是一块纯色：整个按钮同一个颜色，没有渐变也没有高光；
        // 次操作是雾白果冻，细边把它从浅色卡片上分出来。
        int solid = accent == ACCENT ? PRIMARY : accent;
        states.addState(new int[]{}, primary
            ? new ShellGlass(context, 16, solid, solid).edge(0x33FFFFFF).gloss(0)
            : new ShellGlass(context, 16, 0xFFFFFFFF, 0xFFFDF4F9).edge(LINE).gloss(0));
        button.setBackground(new RippleDrawable(ColorStateList.valueOf(primary ? 0x35FFFFFF : 0x14A83877), states, null));
        button.setTextColor(new ColorStateList(new int[][]{new int[]{-android.R.attr.state_enabled}, new int[]{}}, new int[]{DISABLED, primary ? Color.WHITE : INK}));
        // 可用时浮起并带同色光晕，按下贴近表面，停用时落回平面；阴影只跟随真实可用状态。
        float rest = dp(context, primary ? 5 : 1.5f), pressed = dp(context, primary ? 1.5f : .5f);
        StateListAnimator lift = new StateListAnimator();
        lift.addState(new int[]{-android.R.attr.state_enabled}, ObjectAnimator.ofFloat(button, View.TRANSLATION_Z, 0f).setDuration(0));
        lift.addState(new int[]{android.R.attr.state_pressed}, ObjectAnimator.ofFloat(button, View.TRANSLATION_Z, pressed).setDuration(80));
        lift.addState(new int[]{}, ObjectAnimator.ofFloat(button, View.TRANSLATION_Z, rest).setDuration(160));
        button.setElevation(0); button.setStateListAnimator(lift);
        int halo = primary ? (accent == ACCENT ? GLOW : accent) : SHADE;
        button.setOutlineAmbientShadowColor(halo); button.setOutlineSpotShadowColor(halo);
        button.refreshDrawableState();
        button.getBackground().setState(button.getDrawableState());
        button.jumpDrawablesToCurrentState();
        ShellDialogs.pressFeedback(button);
    }
    /** Rounded track with a solid rose fill; the determinate bar keeps the platform clip behaviour. */
    static void progress(ProgressBar bar) {
        Context context = bar.getContext();
        GradientDrawable track = surface(context, 0x4DB07AA8, 8, Color.TRANSPARENT);
        GradientDrawable value = surface(context, PRIMARY, 8, Color.TRANSPARENT);
        LayerDrawable layers = new LayerDrawable(new Drawable[]{track, new ClipDrawable(value, Gravity.START, ClipDrawable.HORIZONTAL)});
        layers.setId(0, android.R.id.background); layers.setId(1, android.R.id.progress);
        bar.setProgressTintList(null); bar.setProgressBackgroundTintList(null);
        bar.setProgressDrawable(layers);
        bar.setIndeterminateTintList(ColorStateList.valueOf(PRIMARY));
    }
    static void reveal(View view, int index) {
        if (!android.animation.ValueAnimator.areAnimatorsEnabled()) return;
        view.setAlpha(0f); view.setTranslationY(dp(view.getContext(), 10));
        view.animate().alpha(1f).translationY(0).setStartDelay(index * STAGGER).setDuration(ITEM).setInterpolator(ENTER).start();
    }
}
