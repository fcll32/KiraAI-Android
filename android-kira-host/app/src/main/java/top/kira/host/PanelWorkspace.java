package top.kira.host;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.AnimatorSet;
import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.PathInterpolator;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.view.Gravity;
import java.util.ArrayList;

/** Keeps page instances separate and confines every transition to the actual content viewport. */
final class PanelWorkspace extends FrameLayout {
    private final ArrayList<View> pages = new ArrayList<>();
    private AnimatorSet transition;
    private int selected = -1;
    private boolean disposed;
    private final FrameLayout loadingRail;
    private final ProgressBar loadingBar;
    private final TextView loadingLabel;
    private int loadingTarget = -1, loadingEpoch;
    private boolean documentPending;
    private int documentProgress;
    private ValueAnimator progressAnimation;
    private Runnable loadingTick;
    private int visualProgress;
    private boolean completingLoading;
    /** Page waiting for its loading bar to finish before it slides in; -1 when none. */
    private int pendingIndex = -1;
    private static final long BAR_FINISH = 320;

    PanelWorkspace(Context context) {
        super(context);
        setClipChildren(true);
        setClipToPadding(true);
        setBackgroundColor(0x00000000);
        loadingRail = new FrameLayout(context);
        loadingRail.setVisibility(GONE);
        loadingRail.setClickable(false);
        loadingRail.setBackground(ShellTheme.frost(context,22));
        ShellTheme.lift(loadingRail,10);
        loadingLabel = ShellCards.text(context,"",11,ShellTheme.ACCENT);
        loadingLabel.setGravity(Gravity.CENTER);
        loadingLabel.setPadding(dp(12),dp(5),dp(12),dp(5));
        LayoutParams labelParams = new LayoutParams(-2,-2,Gravity.TOP|Gravity.CENTER_HORIZONTAL);
        labelParams.topMargin=dp(7); loadingRail.addView(loadingLabel,labelParams);
        loadingBar = new ProgressBar(context,null,android.R.attr.progressBarStyleHorizontal);
        loadingBar.setMax(100);ShellDialogs.styleProgress(loadingBar);
        LayoutParams progressParams=new LayoutParams(-1,dp(6),Gravity.BOTTOM);
        progressParams.leftMargin=dp(16); progressParams.rightMargin=dp(16); progressParams.bottomMargin=dp(12);
        loadingRail.addView(loadingBar,progressParams);
        // 页面切换进度居中悬浮；不拦截触摸，原页面保留可操作。
        LayoutParams railParams=new LayoutParams(-1,dp(64),Gravity.CENTER);
        railParams.leftMargin=dp(32); railParams.rightMargin=dp(32);
        addView(loadingRail,railParams);
    }

    void addPage(View page) {
        pages.add(page);
        addView(page, new LayoutParams(-1, -1));
        page.setVisibility(INVISIBLE);
        page.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
    }

    void showPage(int index, boolean animate) {
        if (disposed || index < 0 || index >= pages.size()) return;
        if (index == pendingIndex) return;
        if (index == selected) {
            // Back to the page already shown while another was waiting: drop the pending switch.
            if (pendingIndex >= 0) { pendingIndex = -1; cancelLoading(); } else finishIfReady();
            return;
        }
        // 首次文档尚未完成时保留原页可用，顶端进度仍显示；完成后再交换页面。
        if(loadingTarget==index&&documentPending){loadingRail.bringToFront();return;}
        int previous = selected;
        finishTransition();
        pendingIndex = -1;
        if(loadingTarget!=index) beginNavigation(index,false);
        if (previous < 0 || !animate || !isAttachedToWindow() || !ValueAnimator.areAnimatorsEnabled()) {
            selected = index; settle(); finishIfReady(); return;
        }
        // 进度条先走完，页面再滑入；进度条在页面滑入的同时淡出，不会出现条还没满就换页。
        pendingIndex = index;
        final int epoch = loadingEpoch;
        completeBar(epoch, () -> {
            if (disposed || epoch != loadingEpoch || pendingIndex != index) return;
            pendingIndex = -1;
            slide(previous, index);
            loadingRail.animate().alpha(0).setDuration(300).setInterpolator(ShellTheme.EXIT)
                .withEndAction(() -> { if (epoch == loadingEpoch) cancelLoading(); }).start();
        });
    }

    /** Runs the bar from where it is to full, then calls next. */
    private void completeBar(int epoch, Runnable next) {
        stopProgressMotion();
        completingLoading = true;
        progressAnimation = ValueAnimator.ofInt(visualProgress, 100);
        progressAnimation.setDuration(BAR_FINISH);
        progressAnimation.setInterpolator(ShellTheme.BOTH);
        progressAnimation.addUpdateListener(value -> { if (epoch == loadingEpoch) setVisualProgress((int) value.getAnimatedValue()); });
        progressAnimation.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator value) { if (!disposed && epoch == loadingEpoch) next.run(); }
        });
        progressAnimation.start();
    }

    private void slide(int previous, int index) {
        selected = index;
        View next = pages.get(index);
        next.setVisibility(VISIBLE);
        next.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_AUTO);
        next.bringToFront();
        loadingRail.bringToFront();
        View old = pages.get(previous);
        old.setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        float offset = Math.min(getWidth() * .08f, 24 * getResources().getDisplayMetrics().density) * (index > previous ? 1 : -1);
        next.setAlpha(0f); next.setTranslationX(offset);
        AnimatorSet motion = new AnimatorSet();
        transition = motion;
        motion.playTogether(ObjectAnimator.ofFloat(old, View.ALPHA, 0f),
            ObjectAnimator.ofFloat(old, View.TRANSLATION_X, -offset * .5f),
            ObjectAnimator.ofFloat(next, View.ALPHA, 1f), ObjectAnimator.ofFloat(next, View.TRANSLATION_X, 0f));
        motion.setDuration(ShellTheme.PANEL);
        motion.setInterpolator(ShellTheme.ENTER);
        motion.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator animation) {
                if (transition != motion) return;
                transition = null;
                settle();
            }
        });
        motion.start();
    }

    /** 居中进度先绘制，再切换页面；网页进度只来自对应 WebView，不重新创建页面。 */
    void beginNavigation(int index,boolean loadingDocument) {
        if(disposed)return;
        if(index==selected&&!loadingDocument){cancelLoading();return;}
        stopProgressMotion();
        loadingEpoch++;loadingTarget=index;documentPending=loadingDocument;documentProgress=0;
        visualProgress=0;completingLoading=false;
        loadingRail.animate().cancel();loadingRail.setAlpha(1);loadingRail.setVisibility(VISIBLE);
        loadingLabel.setText(loadingDocument?"正在加载 "+pageName(index):"正在切换到 "+pageName(index));
        loadingRail.setContentDescription(loadingLabel.getText());loadingRail.bringToFront();
        loadingBar.setIndeterminate(false);loadingBar.setProgress(0);
        final int epoch=loadingEpoch;
        if(ValueAnimator.areAnimatorsEnabled()) {
            progressAnimation=ValueAnimator.ofInt(0,30);progressAnimation.setDuration(250);
            progressAnimation.setInterpolator(ShellTheme.ENTER);
            progressAnimation.addUpdateListener(value->{if(epoch==loadingEpoch)setVisualProgress((int)value.getAnimatedValue());});
            progressAnimation.start();
        } else setVisualProgress(30);
        long start=android.os.SystemClock.uptimeMillis();
        loadingTick=new Runnable(){@Override public void run(){
            if(disposed||epoch!=loadingEpoch)return;
            long elapsed=Math.max(0,android.os.SystemClock.uptimeMillis()-start-250);
            setVisualProgress(30+(int)(60*(1-Math.exp(-elapsed/2200d))));
            loadingRail.postDelayed(this,80);
        }};
        loadingRail.postDelayed(loadingTick,250);
    }

    void documentProgress(int index,int value) {
        if(disposed||loadingTarget!=index||!documentPending)return;
        documentProgress=Math.max(documentProgress,Math.max(0,Math.min(100,value)));
        setVisualProgress(Math.min(90,documentProgress));
    }

    void documentFinished(int index) {
        if(disposed||loadingTarget!=index)return;
        documentPending=false;
        if(selected!=index)showPage(index,true);else finishIfReady();
    }

    void documentFailed(int index) { if(loadingTarget==index) cancelLoading(); }

    private void finishIfReady() {
        if(loadingTarget<0||selected!=loadingTarget||documentPending||transition!=null||completingLoading||pendingIndex>=0)return;
        completingLoading=true;
        final int epoch=loadingEpoch;
        stopProgressMotion();
        if(!ValueAnimator.areAnimatorsEnabled()){loadingBar.setProgress(100);cancelLoading();return;}
        // 技能顶部进度条：250ms 到约30%，等待封顶90%；真实完成后200ms冲线、300ms淡出。
        progressAnimation=ValueAnimator.ofInt(visualProgress,100);progressAnimation.setDuration(200);
        progressAnimation.setInterpolator(ShellTheme.ENTER);
        progressAnimation.addUpdateListener(value->{if(epoch==loadingEpoch)setVisualProgress((int)value.getAnimatedValue());});
        progressAnimation.addListener(new AnimatorListenerAdapter(){@Override public void onAnimationEnd(Animator value){
            if(disposed||epoch!=loadingEpoch)return;
            loadingRail.animate().alpha(0).setDuration(300).setInterpolator(ShellTheme.EXIT)
                .withEndAction(()->{if(epoch==loadingEpoch)cancelLoading();}).start();
        }});
        progressAnimation.start();
    }

    private void setVisualProgress(int value) {visualProgress=Math.max(visualProgress,value);loadingBar.setProgress(visualProgress);}
    private void stopProgressMotion() {
        if(loadingTick!=null){loadingRail.removeCallbacks(loadingTick);loadingTick=null;}
        if(progressAnimation!=null){progressAnimation.removeAllListeners();progressAnimation.removeAllUpdateListeners();progressAnimation.cancel();progressAnimation=null;}
    }

    void cancelLoading() {
        loadingEpoch++;loadingTarget=-1;documentPending=false;completingLoading=false;pendingIndex=-1;
        stopProgressMotion();
        loadingRail.animate().cancel();loadingRail.setVisibility(GONE);loadingRail.setAlpha(1);
    }

    private String pageName(int index) { return index==1?"KiraAI 面板":index==2?"NapCat 面板":"首页"; }
    private int dp(float value) { return ShellTheme.dp(getContext(),value); }

    private void settle() {
        for (int i = 0; i < pages.size(); i++) {
            View page = pages.get(i);
            page.setAlpha(1f); page.setTranslationX(0f);
            page.setVisibility(i == selected ? VISIBLE : INVISIBLE);
            page.setImportantForAccessibility(i == selected ? IMPORTANT_FOR_ACCESSIBILITY_AUTO : IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        }
    }

    void finishTransition() {
        if (transition != null) {
            AnimatorSet previous = transition; transition = null;
            previous.removeAllListeners(); previous.cancel();
        }
        settle();
    }

    @Override public boolean onInterceptTouchEvent(MotionEvent event) { return transition != null || super.onInterceptTouchEvent(event); }
    @Override protected void onDetachedFromWindow() { finishTransition();cancelLoading();super.onDetachedFromWindow(); }
    void dispose() { disposed = true;finishTransition();cancelLoading(); }
}
