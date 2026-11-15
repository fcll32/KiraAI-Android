package top.kira.host;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Typeface;
import android.transition.ChangeBounds;
import android.transition.Fade;
import android.transition.TransitionManager;
import android.transition.TransitionSet;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.ArrayList;

/** A real card deck: reachable title rails when stacked, separate action cards when spread. */
final class ShellAccordion extends LinearLayout {
    private final ArrayList<LinearLayout> cards = new ArrayList<>();
    private final ArrayList<LinearLayout> headers = new ArrayList<>();
    private final ArrayList<View> bodies = new ArrayList<>();
    private final ArrayList<TextView> summaries = new ArrayList<>();
    private final ArrayList<TextView> actions = new ArrayList<>();
    private final ArrayList<View> arrows = new ArrayList<>();
    private final ArrayList<String> titles = new ArrayList<>();
    private ViewGroup transitionScene;
    private boolean spread;
    private int expanded = -1;

    ShellAccordion(Context context) {
        super(context);
        setOrientation(VERTICAL);
        setClipChildren(false);
        setClipToPadding(false);
    }

    void addItem(String mark, String title, String summary, int accent, View body) {
        Context context = getContext();
        LinearLayout card = new LinearLayout(context);
        card.setOrientation(VERTICAL);
        card.setClipToOutline(true);
        card.setBackground(ShellTheme.frost(context, 24));
        card.setOutlineAmbientShadowColor(ShellTheme.SHADE); card.setOutlineSpotShadowColor(ShellTheme.SHADE);
        LinearLayout header = new LinearLayout(context);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setMinimumHeight(dp(76));
        int iconKind = "01".equals(mark) ? ShellIcons.ENVIRONMENT : "◎".equals(mark) ? ShellIcons.DEVICE
            : "02".equals(mark) ? ShellIcons.CORE : "03".equals(mark) ? ShellIcons.CONNECT
            : "▢".equals(mark) ? ShellIcons.BATTERY : "≡".equals(mark) ? ShellIcons.LOG : ShellIcons.SETTINGS;
        View marker = ShellIcons.view(context, iconKind);
        header.addView(marker, new LayoutParams(dp(42), dp(42)));
        LinearLayout copy = new LinearLayout(context); copy.setOrientation(VERTICAL);
        TextView heading = ShellCards.text(context, title, 15, ShellTheme.INK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        copy.addView(heading, new LayoutParams(-1, -2));
        TextView note = ShellCards.text(context, summary, 12, ShellTheme.MUTED);
        LayoutParams noteParams = new LayoutParams(-1, -2); noteParams.topMargin = dp(3);
        copy.addView(note, noteParams);
        LayoutParams copyParams = new LayoutParams(0, -2, 1); copyParams.leftMargin = dp(10);
        header.addView(copy, copyParams);
        // 展开提示收进一枚同色果冻胶囊；箭头是圆头折线，翻转时不依赖字体里的符号。
        LinearLayout affordance = new LinearLayout(context); affordance.setGravity(Gravity.CENTER_VERTICAL);
        affordance.setPadding(dp(11), 0, dp(10), 0);
        affordance.setBackground(ShellTheme.surface(context, accent & 0x00FFFFFF | 0x1A000000, 100, 0xB3FFFFFF));
        TextView command = ShellCards.text(context, "展开", 11, accent);
        command.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        command.setGravity(Gravity.CENTER); command.setIncludeFontPadding(false); command.setLineSpacing(0, 1);
        affordance.addView(command, new LayoutParams(-2, -2));
        View arrow = new Chevron(context, accent);
        LayoutParams arrowParams = new LayoutParams(dp(11), dp(11)); arrowParams.leftMargin = dp(5);
        affordance.addView(arrow, arrowParams);
        LayoutParams commandParams = new LayoutParams(-2, dp(30)); commandParams.leftMargin = dp(8);
        header.addView(affordance, commandParams);
        card.addView(header, new LayoutParams(-1, -2));
        body.setVisibility(GONE);
        card.addView(body, new LayoutParams(-1, -2));
        final int index = bodies.size();
        cards.add(card); headers.add(header); bodies.add(body); summaries.add(note);
        actions.add(command); arrows.add(arrow); titles.add(title);
        header.setFocusable(true);
        header.setOnClickListener(view -> toggle(index));
        ShellDialogs.pressFeedback(header);
        // 卡片露出的整条标题都能点；编号、文字和箭头仅装饰，不截获触摸或重复读屏。
        header.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        copy.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        marker.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        affordance.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        addView(card, new LayoutParams(-1, -2));
        applyState(false);
    }

    boolean isSpread() { return spread; }
    int getExpandedItem() { return expanded; }

    /** Safe explicit entry point for parent navigation and instrumentation. */
    void collapse() {
        if (!spread) return;
        beginMotion(); spread = false; expanded = -1; applyState(ValueAnimator.areAnimatorsEnabled());
    }

    private void toggle(int index) {
        beginMotion();
        // 点任何露出的卡片先摊开整叠；再次点当前卡片沿原路收回，其他标题切换操作内容。
        if (spread && expanded == index) { spread = false; expanded = -1; }
        else { spread = true; expanded = index; }
        applyState(ValueAnimator.areAnimatorsEnabled());
    }

    private void beginMotion() {
        ViewGroup scene = getParent() instanceof ViewGroup ? (ViewGroup)getParent() : this;
        if (transitionScene != null) TransitionManager.endTransitions(transitionScene);
        TransitionManager.endTransitions(scene);
        transitionScene = scene;
        if (!ValueAnimator.areAnimatorsEnabled() || !isLaidOut()) return;
        // 原生测量后的边界过渡负责摊开与让位，文字不缩放，父布局高度同步变化。
        TransitionSet motion = new TransitionSet().setOrdering(TransitionSet.ORDERING_TOGETHER);
        motion.addTransition(new ChangeBounds()).addTransition(new Fade().setDuration(ShellTheme.ITEM));
        motion.setDuration(ShellTheme.PANEL).setInterpolator(ShellTheme.ENTER);
        TransitionManager.beginDelayedTransition(scene, motion);
    }

    private void applyState(boolean animate) {
        int count = cards.size();
        for (int i = 0; i < count; i++) {
            LinearLayout card = cards.get(i), header = headers.get(i);
            boolean open = spread && expanded == i;
            LayoutParams params = (LayoutParams)card.getLayoutParams();
            // 收起时尽量叠紧：上一层只露出图标和标题所在的 54dp（仍高于 48dp 最小触摸高度），绝不覆盖文字或按钮。
            int inset = spread ? 0 : (count - i - 1) * dp(6);
            params.leftMargin = inset; params.rightMargin = inset;
            params.topMargin = i == 0 ? dp(2) : spread ? dp(10) : -dp(22);
            params.bottomMargin = i == count - 1 ? dp(3) : 0;
            card.setLayoutParams(params);
            card.setElevation(dp(spread ? 3 : 3 + i * 2));
            header.setPadding(dp(14), dp(10), dp(12), dp(spread ? 10 : 24));
            summaries.get(i).setVisibility(spread ? VISIBLE : GONE);
            bodies.get(i).setVisibility(open ? VISIBLE : GONE);
            header.setSelected(open);
            String action = open ? "收起" : spread ? "查看" : "展开";
            actions.get(i).setText(action);
            header.setContentDescription(titles.get(i) + (open ? "，收起叠卡" : spread ? "，查看操作" : "，展开叠卡"));
            View arrow = arrows.get(i); arrow.animate().cancel();
            if (animate) arrow.animate().rotation(open ? 180 : 0).setDuration(ShellTheme.PANEL).setInterpolator(ShellTheme.ENTER).start();
            else arrow.setRotation(open ? 180 : 0);
        }
    }

    @Override protected void onDetachedFromWindow() {
        if (transitionScene != null) TransitionManager.endTransitions(transitionScene);
        transitionScene = null;
        for (View arrow : arrows) arrow.animate().cancel();
        super.onDetachedFromWindow();
    }

    private int dp(int value) { return ShellTheme.dp(getContext(), value); }

    /** Decorative round-capped chevron; the header owns the touch target and the spoken label. */
    private static final class Chevron extends View {
        private final android.graphics.Paint paint = new android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        private final android.graphics.Path path = new android.graphics.Path();
        Chevron(Context context, int color) {
            super(context);
            paint.setStyle(android.graphics.Paint.Style.STROKE); paint.setColor(color);
            paint.setStrokeCap(android.graphics.Paint.Cap.ROUND); paint.setStrokeJoin(android.graphics.Paint.Join.ROUND);
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        }
        @Override protected void onDraw(android.graphics.Canvas canvas) {
            float w = getWidth(), h = getHeight(), stroke = Math.max(1f, w * .17f);
            paint.setStrokeWidth(stroke);
            path.reset(); path.moveTo(stroke, h * .34f); path.lineTo(w / 2f, h * .70f); path.lineTo(w - stroke, h * .34f);
            canvas.drawPath(path, paint);
        }
    }
}
