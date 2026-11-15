package top.kira.host;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.content.Context;
import android.os.PowerManager;
import android.util.SparseArray;
import org.json.JSONObject;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.animation.DecelerateInterpolator;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Native shell tools. The activity owns the selected page and every business action. */
public final class ShellDrawer extends FrameLayout {
    public interface Listener {
        void onAction(int action);
        void onOpenChanged(boolean open);
    }

    private static final int INK = ShellTheme.INK;
    private static final int MUTED = ShellTheme.MUTED;
    private static final int BLUE = ShellTheme.ACCENT;
    private final Listener listener;
    private final View scrim;
    private final LinearLayout panel;
    private final View closeButton;
    private ValueAnimator animator;
    private float progress;
    private int panelWidth;
    private boolean opened;
    private boolean disposed;
    private Runnable afterClose;
    private final java.util.ArrayList<View> rows = new java.util.ArrayList<>();
    private final SparseArray<StatusDot> statusDots = new SparseArray<>();
    private final SparseArray<View> statusRows = new SparseArray<>();
    private final SparseArray<String> titles = new SparseArray<>();

    public ShellDrawer(Context context, Listener listener) {
        super(context);
        this.listener = listener;
        setVisibility(GONE);
        setFocusableInTouchMode(true);
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        setAccessibilityPaneTitle("菜单");
        setClipChildren(false);

        scrim = new View(context);
        scrim.setBackgroundColor(INK);
        scrim.setAlpha(0f);
        scrim.setContentDescription("关闭菜单");
        scrim.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        scrim.setOnClickListener(view -> close());
        addView(scrim, new LayoutParams(-1, -1));

        panel = new LinearLayout(context);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setClickable(true);
        panel.setPadding(dp(18), dp(18), dp(18), dp(14));
        // 抽屉是一整块淡粉雾面，左缘伸出屏幕，只露出右侧两个圆角。
        panel.setBackground(new ShellGlass(context, 30, 0xFFFFF8FC, 0xFFF8ECF6).openLeft());
        panel.setClipToOutline(true);
        ShellTheme.lift(panel, 16);
        addView(panel, new LayoutParams(dp(300), -1, Gravity.LEFT));

        LinearLayout header = new LinearLayout(context);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setPadding(0, 0, 0, dp(2));
        header.setBackgroundColor(Color.TRANSPARENT);
        LinearLayout brandRow = new LinearLayout(context);
        brandRow.setGravity(Gravity.CENTER_VERTICAL);
        // 菜单左上角用 KiraAI 官方图标，与底栏、关于页和桌面图标一致。
        View badge = ShellIcons.view(context, ShellIcons.CORE);
        brandRow.addView(badge, new LinearLayout.LayoutParams(dp(48), dp(48)));
        LinearLayout brandCopy = new LinearLayout(context);
        brandCopy.setOrientation(LinearLayout.VERTICAL);
        brandCopy.setPadding(dp(10), 0, 0, 0);
        TextView brand = text("KiraAI", 17, INK);
        brand.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        brandCopy.addView(brand);
        TextView brandSub = text("本机运行 · 组件管理", 11, MUTED);
        LinearLayout.LayoutParams brandSubParams = new LinearLayout.LayoutParams(-1, -2);
        brandSubParams.topMargin = dp(3);
        brandCopy.addView(brandSub, brandSubParams);
        brandRow.addView(brandCopy, new LinearLayout.LayoutParams(0, -2, 1));
        header.addView(brandRow, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout heading = new LinearLayout(context);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        TextView title = text("工作台菜单", 12, MUTED);
        title.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        title.setAccessibilityHeading(true);
        heading.addView(title, new LinearLayout.LayoutParams(0, -2, 1));
        closeButton = ShellIcons.view(context, ShellIcons.CLOSE);
        closeButton.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_YES);
        closeButton.setContentDescription("关闭菜单");
        closeButton.setFocusable(true);
        closeButton.setOnClickListener(view -> close());
        closeButton.setBackground(ripple(ShellTheme.SOFT, 24));
        ShellDialogs.pressFeedback(closeButton);
        heading.addView(closeButton, new LinearLayout.LayoutParams(dp(44), dp(44)));
        header.addView(heading);
        View headerRule = new View(context);
        headerRule.setBackgroundColor(ShellTheme.LINE);
        LinearLayout.LayoutParams headerRuleParams = new LinearLayout.LayoutParams(-1, dp(1));
        headerRuleParams.topMargin = dp(12);
        headerRuleParams.bottomMargin = dp(4);
        header.addView(headerRule, headerRuleParams);
        panel.addView(header, new LinearLayout.LayoutParams(-1, -2));

        ScrollView scroll = new ScrollView(context);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setClipToPadding(false);
        scroll.setPadding(0, 0, 0, dp(8));
        LinearLayout tools = new LinearLayout(context);
        tools.setOrientation(LinearLayout.VERTICAL);
        section(tools, "面板工具");
        tools.addView(action("KiraAI 首次默认令牌", 0, false));
        tools.addView(action("NapCat 首次默认令牌", 1, false));
        tools.addView(action("刷新 KiraAI 面板", 2, false));
        tools.addView(action("刷新 NapCat 面板", 12, false));
        section(tools, "更新与备份");
        tools.addView(action("KiraAI 备份管理", 3, false));
        tools.addView(action("更新 NapCat", 4, false));
        tools.addView(action("卸载本机 NapCat", 13, false));
        tools.addView(action("更新 APK", 5, false));
        section(tools, "日志与后台");
        tools.addView(action("环境安装日志", 6, false));
        tools.addView(action("NapCat 日志", 8, false));
        tools.addView(action("后台与电池设置", 9, false));
        section(tools, "运行设置");
        tools.addView(action("环境变量", 11, false));
        section(tools, "帮助");
        tools.addView(action("新手引导", 14, false));
        scroll.addView(tools, new ScrollView.LayoutParams(-1, -2));
        panel.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));

        LinearLayout footer = new LinearLayout(context);
        footer.setOrientation(LinearLayout.VERTICAL);
        footer.setPadding(dp(14), dp(9), dp(14), 0);
        View divider = new View(context);
        divider.setBackgroundColor(ShellTheme.LINE);
        footer.addView(divider, new LinearLayout.LayoutParams(-1, dp(1)));
        LinearLayout.LayoutParams aboutParams = new LinearLayout.LayoutParams(-1, -2);
        aboutParams.topMargin = dp(9);
        footer.addView(action("关于", 10, true), aboutParams);
        panel.addView(footer, new LinearLayout.LayoutParams(-1, -2));
        applyProgress(0f);
    }

    public void open() { setOpen(true, true); }
    public void close() { setOpen(false, true); }
    public boolean isOpen() { return opened; }

    public void dispose() {
        if (disposed) return;
        cancelAnimation();
        boolean wasOpen = opened;
        opened = false;
        disposed = true;
        applyProgress(0f);
        setVisibility(GONE);
        if (wasOpen) listener.onOpenChanged(false);
    }

    private void setOpen(boolean value, boolean animate) {
        if (disposed || (opened == value && animate)) return;
        cancelAnimation();
        boolean changed = opened != value;
        opened = value;
        if (value) setVisibility(VISIBLE);
        if (changed) listener.onOpenChanged(value);
        if (value) {
            for (int i=0; i<rows.size(); i++) ShellTheme.reveal(rows.get(i), i);
            closeButton.requestFocus();
            panel.sendAccessibilityEvent(AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED);
        }
        float target = value ? 1f : 0f;
        if (!animate || !isAttachedToWindow() || !ValueAnimator.areAnimatorsEnabled() || progress == target) {
            applyProgress(target);
            if (!value) { setVisibility(GONE); completeClose(); }
            return;
        }
        ValueAnimator next = ValueAnimator.ofFloat(progress, target);
        animator = next;
        next.setDuration(Math.max(1L, Math.round((value ? ShellTheme.PANEL : ShellTheme.CLOSE) * Math.abs(target - progress))));
        next.setInterpolator(value ? ShellTheme.ENTER : ShellTheme.EXIT);
        next.addUpdateListener(animation -> applyProgress((float) animation.getAnimatedValue()));
        next.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator animation) {
                if (animator != animation) return;
                animator = null;
                applyProgress(opened ? 1f : 0f);
                if (!opened) { setVisibility(GONE); completeClose(); }
            }
        });
        next.start();
    }

    private void completeClose() {
        Runnable action = afterClose; afterClose = null;
        if (action != null && !disposed) action.run();
    }

    private void cancelAnimation() {
        if (animator == null) return;
        animator.removeAllUpdateListeners();
        animator.removeAllListeners();
        animator.cancel();
        animator = null;
    }

    private void applyProgress(float value) {
        progress = Math.max(0f, Math.min(1f, value));
        panel.setTranslationX(-panelWidth * (1f - progress));
        scrim.setAlpha(0.35f * progress);
        if (getParent() instanceof android.view.ViewGroup) {
            android.view.ViewGroup host=(android.view.ViewGroup)getParent();
            int contentIndex=host.indexOfChild(this)-1;
            if (contentIndex>=0) {
                View content=host.getChildAt(contentIndex);
                content.setScaleX(1f-.04f*progress); content.setScaleY(1f-.04f*progress);
            }
        }
    }

    @Override protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int width = MeasureSpec.getSize(widthMeasureSpec);
        if (MeasureSpec.getMode(widthMeasureSpec) == MeasureSpec.UNSPECIFIED)
            width = getResources().getDisplayMetrics().widthPixels;
        panelWidth = Math.max(0, Math.min(dp(300), width - dp(32)));
        ((LayoutParams) panel.getLayoutParams()).width = panelWidth;
        super.onMeasure(widthMeasureSpec, heightMeasureSpec);
        applyProgress(progress);
    }

    @Override protected void onDetachedFromWindow() {
        cancelAnimation();
        applyProgress(opened ? 1f : 0f);
        if (!opened) setVisibility(GONE);
        super.onDetachedFromWindow();
    }

    private void section(LinearLayout parent, String title) {
        TextView heading = text(title, 11, MUTED);
        heading.setPadding(dp(13), dp(16), dp(10), dp(5));
        heading.setLetterSpacing(0);
        heading.setAccessibilityHeading(true);
        parent.addView(heading, new LinearLayout.LayoutParams(-1, -2));
    }

    private View action(String title, int index, boolean about) {
        ActionRow row = new ActionRow(getContext());
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(10), dp(5), dp(9), dp(5));
        row.setMinimumHeight(dp(about ? 58 : 48));
        // 每个入口是一张白色小果冻卡；“关于”用同一色相的淡玫瑰区分。
        ShellGlass face = about ? new ShellGlass(getContext(), 20, 0xFFFDEAF2, 0xFFFADDEA).edge(0xCCFFFFFF)
            : new ShellGlass(getContext(), 18, 0xFFFFFFFF, 0xFFFFFAFD).edge(0xFFF0DFEB).gloss(0);
        row.setBackground(new RippleDrawable(ColorStateList.valueOf(0x1A9D5F86), face, surface(Color.WHITE, Color.WHITE, about ? 20 : 18)));
        ShellTheme.lift(row, about ? 2 : 1);
        LinearLayout.LayoutParams spacing = new LinearLayout.LayoutParams(-1, -2);
        spacing.bottomMargin = dp(8);
        row.setLayoutParams(spacing);
        rows.add(row);
        ShellDialogs.pressFeedback(row);
        row.setContentDescription(about ? title + "，版本 " + BuildConfig.VERSION_NAME : title);
        StatusDot status = new StatusDot(getContext());
        status.setTag("status-dot-"+index);
        statusDots.put(index,status);statusRows.put(index,row);titles.put(index,title);
        row.addView(status,new LinearLayout.LayoutParams(dp(12),dp(28)));
        View icon = ShellIcons.view(getContext(),ShellIcons.actionKind(index));
        LinearLayout.LayoutParams iconParams=new LinearLayout.LayoutParams(dp(38),dp(38));iconParams.leftMargin=dp(3);
        row.addView(icon,iconParams);
        LinearLayout words = new LinearLayout(getContext());
        words.setOrientation(LinearLayout.VERTICAL);
        words.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        LinearLayout.LayoutParams wordParams = new LinearLayout.LayoutParams(0, -2, 1);
        wordParams.leftMargin = dp(9);
        row.addView(words, wordParams);
        TextView label = text(title, 14, INK);
        label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        words.addView(label, new LinearLayout.LayoutParams(-1, -2));
        if (about) {
            TextView version = text("版本 " + BuildConfig.VERSION_NAME, 11, MUTED);
            version.setPadding(0, dp(3), 0, 0);
            words.addView(version, new LinearLayout.LayoutParams(-1, -2));
        }
        TextView arrow = text("›", 24, MUTED);
        arrow.setGravity(Gravity.CENTER);
        arrow.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(arrow, new LinearLayout.LayoutParams(dp(22), dp(34)));
        row.setOnClickListener(view -> {
            if (!opened || disposed) return;
            afterClose = () -> listener.onAction(index);
            setOpen(false, true);
        });
        return row;
    }

    private TextView text(String value, int size, int color) {
        TextView text = new TextView(getContext());
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setIncludeFontPadding(false);
        return text;
    }

    private GradientDrawable surface(int top, int bottom, int radius) {
        return ShellTheme.surface(getContext(), top, radius, Color.TRANSPARENT);
    }

    private RippleDrawable ripple(int fill, int radius) {
        return new RippleDrawable(ColorStateList.valueOf(0x1A9D5F86), surface(fill, fill, radius),
            surface(Color.WHITE, Color.WHITE, radius));
    }

    private int dp(float value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    private final class ActionRow extends LinearLayout {
        ActionRow(Context context) {
            super(context);
            setClickable(true);
            setFocusable(true);
        }
        @Override public void onInitializeAccessibilityNodeInfo(AccessibilityNodeInfo info) {
            super.onInitializeAccessibilityNodeInfo(info);
            info.setClassName(Button.class.getName());
        }
    }

    /** 白点对应所关联组件/系统设置的真实状态；停用时降低透明度，不伪装成已就绪。 */
    public void setRuntimeStatus(JSONObject value,boolean live) {
        if(value==null)value=new JSONObject();
        JSONObject kira=RuntimeStatus.component(value,"kira"),napcat=RuntimeStatus.component(value,"napcat");
        int core=componentDot(kira,live),bridge=componentDot(napcat,live);
        String coreLabel="KiraAI："+RuntimeStatus.phase(live || "error".equals(kira.optString("state")) ? kira.optString("state","stopped") : "stopped");
        String bridgeLabel="NapCat："+RuntimeStatus.napcatPhase(napcat);
        for(int i=0;i<2;i++) {
            String component=i==0?"kira":"napcat";
            JSONObject history=RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(getContext()),"initial-"+component+"-token.json"));
            boolean recorded="recorded".equals(history.optString("state"));
            setDot(i,recorded?2:0,recorded?"已记录首次默认令牌":"首次默认令牌未记录");
        }
        setDot(3,core,coreLabel);setDot(4,bridge,bridgeLabel);
        setPanelDot(2,kira,live,core,coreLabel);setPanelDot(12,napcat,live,bridge,bridgeLabel);
        setDot(5,2,"当前应用版本 "+BuildConfig.VERSION_NAME);
        boolean environmentLog=RuntimeStatus.componentLog(getContext(),value,"environment").isFile();
        boolean napcatLog=RuntimeStatus.componentLog(getContext(),value,"napcat").isFile();
        setDot(6,environmentLog?2:0,environmentLog?"已有安装日志":"安装日志尚未生成");
        setDot(8,napcatLog?2:0,napcatLog?"已有 NapCat 日志":"NapCat 日志尚未生成");
        PowerManager power=getContext().getSystemService(PowerManager.class);
        boolean allowed=power!=null&&power.isIgnoringBatteryOptimizations(getContext().getPackageName());
        setDot(9,allowed?2:0,allowed?"已允许忽略电池优化":"仍受系统电池优化限制");
        boolean custom=new java.io.File(RuntimeStatus.directory(getContext()),"environment.json").isFile();
        setDot(11,custom?2:0,custom?"已有自定义环境变量":"正在使用默认环境变量");
        setDot(10,2,"版本 "+BuildConfig.VERSION_NAME);
    }

    private static int componentDot(JSONObject component,boolean live) {
        String state=component.optString("state","stopped");
        if("error".equals(state))return -1;
        if(!live || "stopped".equals(state))return 0;
        return "ready".equals(state)||"running".equals(state)?2:1;
    }

    private void setPanelDot(int action, JSONObject component, boolean live, int lifecycleDot, String lifecycleLabel) {
        View row = statusRows.get(action);
        if (!live || !"ready".equals(component.optString("state"))) {
            row.setEnabled(true);
            setDot(action, lifecycleDot, lifecycleLabel);
            return;
        }
        String health = RuntimeStatus.panelHealth(getContext(), component);
        boolean available = RuntimeStatus.panelAvailable(getContext(), component, live);
        row.setEnabled(available);
        int dot = "reachable".equals(health) ? 2 : "unreachable".equals(health) ? -1 : 1;
        setDot(action, dot, RuntimeStatus.panelHealthLabel(health));
    }

    private void setDot(int action,int state,String description) {
        StatusDot dot=statusDots.get(action);if(dot==null)return;
        dot.update(state);dot.setContentDescription(description);
        View row=statusRows.get(action);String content=titles.get(action)+"，"+description;
        if(!content.contentEquals(row.getContentDescription()==null?"":row.getContentDescription()))row.setContentDescription(content);
    }
    private final class StatusDot extends View {
        private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        private int state=Integer.MIN_VALUE;
        StatusDot(Context context){super(context);setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);setAlpha(.3f);}
        void update(int value){if(state==value)return;state=value;setAlpha(state==2?1f:state==1?.7f:state==-1?.55f:.3f);invalidate();}
        @Override protected void onDraw(Canvas canvas) {
            float x=getWidth()/2f,y=getHeight()/2f;
            paint.setStyle(Paint.Style.FILL);paint.setColor(0xFFBB8CA9);canvas.drawCircle(x,y,dp(4.5f),paint);
            paint.setColor(Color.WHITE);
            canvas.drawCircle(x,y,dp(state==2?2.6f:2.1f),paint);paint.setAlpha(255);
            if(state==-1){paint.setStyle(Paint.Style.STROKE);paint.setColor(ShellTheme.TEAL);paint.setStrokeWidth(dp(.8f));canvas.drawCircle(x,y,dp(5.3f),paint);}
        }
    }
}
