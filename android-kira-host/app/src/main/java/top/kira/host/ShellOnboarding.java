package top.kira.host;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Rect;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

/**
 * 新手开始页：首次启动时盖在主界面上，菜单“新手引导”可再次打开。
 * 页面是 assets/onboarding 下的本地网页：开场“点亮数字生命灵魂”动画，之后由 cursor-swarm.js 的三个光标把引导内容搭出来。
 * 第一次点“开始使用”后，同一批光标再把主界面搭一遍（只有一次）：网页变透明，原生端把屏幕上可见的主界面各块
 * 的位置交给网页并先隐藏它们，光标在相同位置画框、点击，每搭完一块就把那块真实控件淡入。
 * 这个 WebView 只加载这一页：不联网、禁止任何跳转，网页只能调用 done、battery、layout、reveal。
 */
final class ShellOnboarding {
    static final String SEEN = "onboarding_seen_v1";
    static final String BUILT = "main_built_v1";

    /** 主界面上要被光标搭出来的控件，按从上到下的顺序。 */
    interface Stage { List<View> buildTargets(); }
    private static final String PAGE = "file:///android_asset/onboarding/index.html";

    private final Activity activity;
    private final FrameLayout host;
    private final SharedPreferences preferences;
    private final Runnable battery;
    private final View[] covered;
    private final Stage stage;
    private final List<View> hidden = new ArrayList<>();
    private WebView view;
    private boolean closing;

    ShellOnboarding(Activity activity, FrameLayout host, SharedPreferences preferences, Runnable battery, Stage stage, View... covered) {
        this.activity = activity;
        this.host = host;
        this.preferences = preferences;
        this.battery = battery;
        this.stage = stage;
        this.covered = covered;
    }

    boolean firstRun() { return !preferences.getBoolean(SEEN, false); }

    boolean isShowing() { return view != null; }

    void show() {
        if (view != null || activity.isFinishing() || activity.isDestroyed()) return;
        closing = false;
        WebView web = new WebView(activity);
        web.setBackgroundColor(ShellTheme.PAGE);
        web.setOverScrollMode(View.OVER_SCROLL_NEVER);
        web.setVerticalScrollBarEnabled(false);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setAllowFileAccess(false);          // file:///android_asset 不受此项限制
        settings.setAllowContentAccess(false);
        settings.setDomStorageEnabled(false);
        settings.setGeolocationEnabled(false);
        settings.setSupportZoom(false);
        settings.setBlockNetworkLoads(true);
        settings.setMediaPlaybackRequiresUserGesture(true);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) { return true; }
            @Override public boolean onRenderProcessGone(WebView v, RenderProcessGoneDetail detail) {
                // 渲染进程崩溃时只关掉引导页，不影响主界面。
                if (v == view) remove(false);
                return true;
            }
        });
        web.addJavascriptInterface(new Bridge(), "KiraOnboarding");
        web.setAlpha(0f);
        host.addView(web, new FrameLayout.LayoutParams(-1, -1));
        view = web;
        for (View item : covered) item.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS);
        web.loadUrl(PAGE);
        web.animate().alpha(1f).setDuration(220).start();
        web.requestFocus();
    }

    /** 关闭并记住已看过；返回键、“开始使用”和渲染异常都会走到这里。 */
    void close() {
        if (view == null || closing) return;
        closing = true;
        preferences.edit().putBoolean(SEEN, true).putBoolean(BUILT, true).apply();
        revealAll();
        WebView web = view;
        web.animate().alpha(0f).setDuration(240).withEndAction(() -> { if (web == view) remove(true); }).start();
    }

    void dispose() { if (view != null) remove(true); }

    private void remove(boolean seen) {
        WebView web = view;
        view = null;
        closing = false;
        if (seen) preferences.edit().putBoolean(SEEN, true).putBoolean(BUILT, true).apply();
        revealAll();
        for (View item : covered) item.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_AUTO);
        if (web == null) return;
        web.animate().cancel();
        host.removeView(web);
        web.removeJavascriptInterface("KiraOnboarding");
        web.stopLoading();
        web.destroy();
    }

    /** 主线程：记下屏幕上可见的主界面各块（WebView 内的 CSS 像素），先把它们隐藏，网页底色改透明。 */
    private String prepareStage() throws Exception {
        if (view == null || closing || preferences.getBoolean(BUILT, false)) return "";
        preferences.edit().putBoolean(BUILT, true).apply(); // 一次性：中途退出也不再重播
        float density = activity.getResources().getDisplayMetrics().density;
        int[] origin = new int[2], at = new int[2];
        view.getLocationOnScreen(origin);
        Rect visible = new Rect();
        JSONArray items = new JSONArray();
        for (View target : stage.buildTargets()) {
            if (target == null || target.getVisibility() != View.VISIBLE || target.getWidth() == 0 || target.getHeight() == 0
                    || !target.getGlobalVisibleRect(visible) || hidden.contains(target)) continue;
            target.getLocationOnScreen(at);
            float x = (at[0] - origin[0]) / density, y = (at[1] - origin[1]) / density;
            float w = target.getWidth() / density, h = target.getHeight() / density;
            if (y >= view.getHeight() / density || y + h <= 0) continue;
            boolean small = target instanceof TextView || (w < 120 && h < 80);
            items.put(new JSONObject().put("id", hidden.size()).put("type", small ? "pop" : "frame")
                .put("x", Math.round(x)).put("y", Math.round(y)).put("w", Math.round(w)).put("h", Math.round(h)));
            hidden.add(target);
        }
        if (hidden.isEmpty()) return "";
        for (View target : hidden) target.setAlpha(0f);
        view.setBackgroundColor(Color.TRANSPARENT);
        return new JSONObject().put("items", items).toString();
    }

    private void revealAt(int index) {
        if (index < 0 || index >= hidden.size()) return;
        View target = hidden.get(index);
        if (target == null) return;
        hidden.set(index, null);
        target.setScaleX(.97f); target.setScaleY(.97f);
        target.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(280).start();
    }

    /** 关闭、跳过或出错时，所有还没淡入的控件立即显示，主界面不会留下空白。 */
    private void revealAll() {
        for (View target : hidden) {
            if (target == null) continue;
            target.animate().cancel();
            target.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(180).start();
        }
        hidden.clear();
    }

    /** 网页按钮的回调在 WebView 自己的线程上，全部转回主线程，且只在这一页仍在显示时生效。 */
    private final class Bridge {
        @JavascriptInterface public void done() { activity.runOnUiThread(() -> { if (view != null) close(); }); }
        @JavascriptInterface public void battery() { activity.runOnUiThread(() -> { if (view != null) battery.run(); }); }
        @JavascriptInterface public String layout() {
            FutureTask<String> task = new FutureTask<>(ShellOnboarding.this::prepareStage);
            activity.runOnUiThread(task);
            try { return task.get(1500, TimeUnit.MILLISECONDS); }
            catch (Exception error) { activity.runOnUiThread(() -> { if (view != null) close(); }); return ""; }
        }
        @JavascriptInterface public void reveal(String id) {
            int index;
            try { index = Integer.parseInt(id); } catch (NumberFormatException error) { return; }
            activity.runOnUiThread(() -> { if (view != null) revealAt(index); });
        }
    }
}
