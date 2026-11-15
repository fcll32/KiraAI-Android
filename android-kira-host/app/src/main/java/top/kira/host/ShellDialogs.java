package top.kira.host;

import android.animation.ValueAnimator;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Looper;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.view.animation.OvershootInterpolator;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.WeakHashMap;

/** Opaque, purpose-built surfaces. Business listeners retain native AlertDialog semantics. */
final class ShellDialogs {
    enum Card { STANDARD, ARCHIVE, NAPCAT_UPDATE, APK_UPDATE, ENVIRONMENT_LOG, NAPCAT_LOG, EDITOR, BATTERY, ABOUT,
        KIRA_TOKEN, NAPCAT_TOKEN, DEVICE, REMOTE }
    private static final WeakHashMap<Activity, ArrayList<MotionDialog>> OPEN = new WeakHashMap<>();
    private static final float DIM = .24f, BACKGROUND_SCALE = .96f;
    static AlertDialog.Builder builder(Context context) { return builder(context, Card.STANDARD); }
    static AlertDialog.Builder builder(Context context, Card card) { return new Builder(context, card); }

    /** Activity 销毁时同步关闭自有窗口；不等待退出动画，也不依赖旋转时为 false 的 isFinishing。 */
    static void dispose(Activity activity) {
        if (activity == null) return;
        if (Looper.myLooper() != Looper.getMainLooper()) throw new IllegalStateException("Dialog disposal must run on the main thread");
        ArrayList<MotionDialog> owned = OPEN.remove(activity);
        if (owned != null) {
            for (int i=owned.size()-1;i>=0;i--) {
                MotionDialog dialog=owned.get(i);
                dialog.held=false; dialog.finishing=true; dialog.closing=true; dialog.ready=false;
                if (dialog.decor != null) cancelViewAnimations(dialog.decor);
                dialog.dismiss();
            }
        }
        OPEN.remove(activity);
        View root=activity.findViewById(android.R.id.content);
        if (root != null) { cancelViewAnimations(root); root.setScaleX(1); root.setScaleY(1); }
    }

    private static void cancelViewAnimations(View view) {
        view.animate().cancel(); view.clearAnimation();
        if (view instanceof ViewGroup) {
            ViewGroup group=(ViewGroup)view;
            for (int i=0;i<group.getChildCount();i++) cancelViewAnimations(group.getChildAt(i));
        }
    }

    private static final class Builder extends AlertDialog.Builder {
        final Context owner;
        final Card card;
        CharSequence title, message;
        View body;
        boolean cancelable = true;
        final CharSequence[] labels = new CharSequence[3];
        final DialogInterface.OnClickListener[] actions = new DialogInterface.OnClickListener[3];
        Builder(Context context, Card card) { super(context, R.style.HostDialogTheme); owner = context; this.card = card; }
        @Override public AlertDialog.Builder setTitle(CharSequence value) { title = value; return this; }
        @Override public AlertDialog.Builder setTitle(int value) { return setTitle(owner.getText(value)); }
        @Override public AlertDialog.Builder setMessage(CharSequence value) { message = value; return this; }
        @Override public AlertDialog.Builder setMessage(int value) { return setMessage(owner.getText(value)); }
        @Override public AlertDialog.Builder setView(View value) { body = value; return this; }
        @Override public AlertDialog.Builder setCancelable(boolean value) { cancelable = value; return this; }
        private AlertDialog.Builder action(int slot, CharSequence text, DialogInterface.OnClickListener action) {
            labels[slot] = text; actions[slot] = action; return this;
        }
        @Override public AlertDialog.Builder setPositiveButton(CharSequence text, DialogInterface.OnClickListener action) { return action(0, text, action); }
        @Override public AlertDialog.Builder setPositiveButton(int text, DialogInterface.OnClickListener action) { return setPositiveButton(owner.getText(text), action); }
        @Override public AlertDialog.Builder setNegativeButton(CharSequence text, DialogInterface.OnClickListener action) { return action(1, text, action); }
        @Override public AlertDialog.Builder setNegativeButton(int text, DialogInterface.OnClickListener action) { return setNegativeButton(owner.getText(text), action); }
        @Override public AlertDialog.Builder setNeutralButton(CharSequence text, DialogInterface.OnClickListener action) { return action(2, text, action); }
        @Override public AlertDialog.Builder setNeutralButton(int text, DialogInterface.OnClickListener action) { return setNeutralButton(owner.getText(text), action); }
        @Override public AlertDialog create() {
            MotionDialog dialog = new MotionDialog(owner, card, title, cancelable);
            if (message != null) dialog.setMessage(message);
            if (body != null) dialog.setView(body);
            int[] kinds = {AlertDialog.BUTTON_POSITIVE, AlertDialog.BUTTON_NEGATIVE, AlertDialog.BUTTON_NEUTRAL};
            for (int i = 0; i < kinds.length; i++) if (labels[i] != null) dialog.setButton(kinds[i], labels[i], actions[i]);
            return dialog;
        }
        @Override public AlertDialog show() { AlertDialog dialog = create(); dialog.show(); return dialog; }
    }

    private static final class MotionDialog extends AlertDialog {
        final Context owner;
        final Activity activity;
        final Card card;
        final boolean canCancel;
        final LinearLayout heading;
        boolean ready, closing, finishing, held;
        View decor;
        MotionDialog(Context owner, Card card, CharSequence title, boolean cancelable) {
            super(owner, R.style.HostDialogTheme);
            this.owner = owner; activity = owner instanceof Activity ? (Activity) owner : null;
            this.card = card; canCancel = cancelable;
            setCancelable(cancelable); setCanceledOnTouchOutside(cancelable);
            heading = heading(this, title == null ? "kira" : title.toString());
            setCustomTitle(heading);
        }
        boolean side() { return card == Card.EDITOR; }
        boolean log() { return card == Card.ENVIRONMENT_LOG || card == Card.NAPCAT_LOG; }
        boolean sheet() {
            return card == Card.STANDARD || card == Card.ARCHIVE || card == Card.KIRA_TOKEN || card == Card.NAPCAT_TOKEN
                || card == Card.DEVICE || card == Card.REMOTE;
        }
        boolean dark() { return card == Card.NAPCAT_LOG; }
        int accent() {
            return card == Card.NAPCAT_UPDATE || card == Card.BATTERY || card == Card.NAPCAT_TOKEN || card == Card.REMOTE
                ? ShellTheme.TEAL : ShellTheme.ACCENT;
        }
        int surfaceStart() {
            switch (card) {
                case NAPCAT_UPDATE: return 0xFFFFFCFF;
                case BATTERY: return 0xFFFFFCFE;
                case APK_UPDATE: case ABOUT: return 0xFFFFFCFF;
                case NAPCAT_LOG: return ShellTheme.NIGHT;
                default: return ShellTheme.SURFACE;
            }
        }
        int surfaceEnd() {
            switch (card) {
                case ARCHIVE: return 0xFFF7E8F1;
                case NAPCAT_UPDATE: return 0xFFF0E8F7;
                case BATTERY: return 0xFFF5EAF7;
                case APK_UPDATE: case ABOUT: return 0xFFF3EAF6;
                case NAPCAT_LOG: return ShellTheme.NIGHT;
                case EDITOR: return 0xFFF8EFF6;
                default: return ShellTheme.PEARL_END;
            }
        }
        int gravity() { return side() ? Gravity.RIGHT | Gravity.CENTER_VERTICAL :
            card == Card.APK_UPDATE ? Gravity.TOP | Gravity.CENTER_HORIZONTAL :
            sheet() || card == Card.BATTERY ? Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL : Gravity.CENTER; }
        @Override protected void onStart() {
            super.onStart();
            Window window = getWindow();
            if (window == null) return;
            decor = window.getDecorView();
            decor.setAlpha(0);
            window.setWindowAnimations(0);
            window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            GradientDrawable surface = ShellTheme.gradient(owner,surfaceStart(),surfaceEnd(),0,dark() ? 0xFF6C506F : ShellTheme.LINE);
            int r = dp(owner, 28);
            surface.setCornerRadii(corners(this,r));
            window.setBackgroundDrawable(surface);
            decor.setPadding(0,0,0,0); decor.setElevation(dp(owner,18)); decor.setClipToOutline(true);
            decor.setOutlineAmbientShadowColor(0x405D3E5F); decor.setOutlineSpotShadowColor(0x405D3E5F);
            WindowManager.LayoutParams attrs = window.getAttributes();
            attrs.dimAmount = DIM; attrs.gravity = gravity(); attrs.x = 0;
            attrs.y = card == Card.APK_UPDATE || card == Card.BATTERY || sheet() ? dp(owner,16) : 0;
            window.addFlags(WindowManager.LayoutParams.FLAG_DIM_BEHIND); window.setAttributes(attrs);
            if (!held) { held = true; depth(this,true); }
            decor.post(() -> {
                if (!isShowing()) return;
                android.graphics.Rect frame = new android.graphics.Rect(); decor.getWindowVisibleDisplayFrame(frame);
                int width = frame.width() > 0 ? frame.width() : owner.getResources().getDisplayMetrics().widthPixels;
                int height = frame.height() > 0 ? frame.height() : owner.getResources().getDisplayMetrics().heightPixels;
                int targetWidth = Math.min(width - dp(owner,32),dp(owner,side() || log() ? 640 : 480));
                window.setLayout(targetWidth,side() ? height - dp(owner,32) : card == Card.ENVIRONMENT_LOG ? (int)(height * .60f)
                    : card == Card.NAPCAT_LOG ? (int)(height * .82f) : ViewGroup.LayoutParams.WRAP_CONTENT);
                arrangeActions(this);
                TextView message = findViewById(android.R.id.message);
                if (message != null) { message.setTextSize(14); message.setLineSpacing(dp(owner,5),1); message.setTextColor(ShellTheme.INK); }
                ready = true;
                decor.post(() -> { if (isShowing() && !closing) enter(this); });
            });
        }
        @Override public void dismiss() {
            if (Looper.myLooper() != Looper.getMainLooper()) { new android.os.Handler(Looper.getMainLooper()).post(this::dismiss); return; }
            if (finishing || !ready || !isShowing() || !ValueAnimator.areAnimatorsEnabled()
                || activity != null && (activity.isFinishing() || activity.isDestroyed())) { super.dismiss(); return; }
            if (closing) return;
            closing = true;
            // 遮罩、返回键、关闭按钮及业务回调共用可逆退场，不改变按钮回调。
            exit(this,() -> { finishing = true; super.dismiss(); });
        }
        @Override protected void onStop() {
            if (decor != null) decor.animate().cancel();
            if (held) { held = false; depth(this,false); }
            ready = false; super.onStop();
        }
    }

    private static LinearLayout heading(MotionDialog dialog, String title) {
        Context context = dialog.owner;
        LinearLayout heading = new LinearLayout(context); heading.setOrientation(LinearLayout.VERTICAL);
        heading.setPadding(dp(context,20),dp(context,dialog.sheet() ? 0 : 12),dp(context,18),dp(context,12));
        if (dialog.sheet()) {
            android.widget.FrameLayout handle = new android.widget.FrameLayout(context);
            handle.setContentDescription("下拉关闭面板");
            handle.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
            View grip = new View(context); grip.setBackground(ShellTheme.surface(context,0xFFD3B4CB,3,Color.TRANSPARENT));
            grip.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            handle.addView(grip,new android.widget.FrameLayout.LayoutParams(dp(context,32),dp(context,4),Gravity.CENTER));
            heading.addView(handle,new LinearLayout.LayoutParams(-1,dp(context,44)));
            drag(handle,dialog);
        }
        LinearLayout top = new LinearLayout(context); top.setGravity(Gravity.CENTER_VERTICAL);
        String caption;
        switch (dialog.card) {
            case ARCHIVE: caption = "本地档案"; break;
            case NAPCAT_UPDATE: caption = "组件维护"; break;
            case APK_UPDATE: caption = "应用版本"; break;
            case ENVIRONMENT_LOG: caption = "运行记录  /  INSTALL"; break;
            case NAPCAT_LOG: caption = ">_  NAPCAT CONSOLE"; break;
            case EDITOR: caption = "工作区设置  /  CONFIG"; break;
            case BATTERY: caption = "后台运行保障"; break;
            case ABOUT: caption = "关于 KiraAI"; break;
            case KIRA_TOKEN: caption = "KiraAI"; break;
            case NAPCAT_TOKEN: caption = "NapCat"; break;
            case DEVICE: caption = "手机与运行环境"; break;
            case REMOTE: caption = "远程连接"; break;
            default: caption = "KiraAI";
        }
        TextView label = ShellCards.text(context,caption,10,dialog.dark() ? 0xFFDBC7E4 : dialog.accent());
        label.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        label.setLetterSpacing(.08f);
        View emblem = ShellIcons.view(context,dialogIcon(dialog.card));
        emblem.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        top.addView(emblem,new LinearLayout.LayoutParams(dp(context,40),dp(context,40)));
        LinearLayout.LayoutParams labelParams = new LinearLayout.LayoutParams(0,-2,1);
        labelParams.leftMargin = dp(context,8);
        top.addView(label,labelParams);
        if (dialog.canCancel) {
            View close = ShellIcons.view(context,ShellIcons.CLOSE);
            close.setContentDescription("关闭"+title); close.setFocusable(true);
            close.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
            close.setBackground(new android.graphics.drawable.RippleDrawable(ColorStateList.valueOf(0x229D5F86),
                ShellTheme.surface(context,dialog.dark() ? 0xFFE5D3E8 : ShellTheme.SOFT,22,Color.TRANSPARENT),null));
            close.setOnClickListener(view -> dialog.cancel()); pressFeedback(close);
            top.addView(close,new LinearLayout.LayoutParams(dp(context,44),dp(context,44)));
        }
        heading.addView(top,new LinearLayout.LayoutParams(-1,-2));
        TextView name = ShellCards.text(context,title,dialog.log() ? 20 : 22,dialog.dark() ? 0xFFFFF5FC : ShellTheme.INK);
        name.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL)); name.setAccessibilityHeading(true);
        heading.addView(name,new LinearLayout.LayoutParams(-1,-2));
        if (dialog.log() || dialog.side() || dialog.card == Card.APK_UPDATE) {
            View rule = new View(context); rule.setBackgroundColor(dialog.dark() ? 0xFF6A506F : ShellTheme.LINE);
            LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1,dp(context,1)); p.topMargin = dp(context,16); heading.addView(rule,p);
        }
        return heading;
    }

    private static int dialogIcon(Card card) {
        switch (card) {
            case ARCHIVE: return ShellIcons.BACKUP;
            case NAPCAT_UPDATE: return ShellIcons.BRIDGE;
            case APK_UPDATE: return ShellIcons.PACKAGE;
            case BATTERY: return ShellIcons.BATTERY;
            // 每类弹窗用自己的图标；KiraAI 与 NapCat 相关的一律用官方原图。
            case ABOUT: case KIRA_TOKEN: return ShellIcons.CORE;
            case NAPCAT_TOKEN: return ShellIcons.BRIDGE;
            case DEVICE: return ShellIcons.DEVICE;
            case REMOTE: return ShellIcons.CONNECT;
            case ENVIRONMENT_LOG: return ShellIcons.LOG;
            case NAPCAT_LOG: return ShellIcons.TERMINAL;
            case EDITOR: return ShellIcons.SETTINGS;
            default: return ShellIcons.ENVIRONMENT;
        }
    }

    private static boolean quietAction(Button button) {
        String text = button.getText().toString();
        return "关闭".equals(text) || "取消".equals(text) || "返回".equals(text);
    }

    private static void arrangeActions(MotionDialog dialog) {
        int[] kinds = {AlertDialog.BUTTON_NEGATIVE,AlertDialog.BUTTON_NEUTRAL,AlertDialog.BUTTON_POSITIVE};
        Button first = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
        if (first == null || !(first.getParent() instanceof ViewGroup)) return;
        ViewGroup old = (ViewGroup)first.getParent();
        if (!(old.getParent() instanceof ViewGroup)) return;
        ViewGroup parent = (ViewGroup)old.getParent(); int position = parent.indexOfChild(old);
        ViewGroup.LayoutParams params = old.getLayoutParams();
        int visible = 0;
        for (int kind:kinds) { Button button=dialog.getButton(kind); if (button!=null && button.getVisibility()==View.VISIBLE) visible++; }
        LinearLayout footer = new LinearLayout(dialog.owner); footer.setOrientation(LinearLayout.VERTICAL);
        footer.setPadding(dp(dialog.owner,18),dp(dialog.owner,10),dp(dialog.owner,18),dp(dialog.owner,18));
        // 主按钮的光晕铺进页脚留白，不被内边距或次级按钮行切成直边。
        footer.setClipToPadding(false); footer.setClipChildren(false);
        LinearLayout secondary = new LinearLayout(dialog.owner);
        for (int kind : kinds) {
            Button button = dialog.getButton(kind);
            if (button == null || button.getVisibility() != View.VISIBLE) continue;
            if (button.getParent() instanceof ViewGroup) ((ViewGroup)button.getParent()).removeView(button);
            boolean primary = kind == AlertDialog.BUTTON_POSITIVE && !quietAction(button);
            ShellTheme.button(button,dialog.accent(),primary);
            if (primary && visible>2) {
                LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(-1,-2); p.bottomMargin = dp(dialog.owner,9); footer.addView(button,p);
            } else {
                LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(0,-2,primary ? 1.4f : 1);
                if (secondary.getChildCount() > 0) p.leftMargin = dp(dialog.owner,9); secondary.addView(button,p);
            }
        }
        if (secondary.getChildCount() > 0) footer.addView(secondary,new LinearLayout.LayoutParams(-1,-2));
        footer.measure(View.MeasureSpec.makeMeasureSpec(dialog.getWindow().getAttributes().width,View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(0,View.MeasureSpec.UNSPECIFIED));
        footer.setMinimumHeight(footer.getMeasuredHeight());
        parent.removeViewAt(position); parent.addView(footer,position,params);
    }

    private static void depth(MotionDialog dialog, boolean acquire) {
        Activity activity = dialog.activity; if (activity == null) return;
        ArrayList<MotionDialog> stack = OPEN.get(activity);
        if (stack == null) { stack = new ArrayList<>(); OPEN.put(activity,stack); }
        if (acquire) stack.add(dialog); else stack.remove(dialog);
        View root = activity.findViewById(android.R.id.content); if (root == null) return;
        float target = stack.isEmpty() || !ValueAnimator.areAnimatorsEnabled() ? 1 : BACKGROUND_SCALE; root.animate().cancel();
        if (ValueAnimator.areAnimatorsEnabled()) root.animate().scaleX(target).scaleY(target).setDuration(ShellTheme.PANEL).setInterpolator(ShellTheme.ENTER).start();
        else { root.setScaleX(target); root.setScaleY(target); }
        if (stack.isEmpty()) OPEN.remove(activity);
    }

    private static void enter(MotionDialog dialog) {
        View view = dialog.decor;
        if (!ValueAnimator.areAnimatorsEnabled()) { view.setAlpha(1); return; }
        switch (dialog.card) {
            case ARCHIVE: view.setTranslationY(dp(dialog.owner,24)); view.setScaleX(.98f); view.setScaleY(.98f); view.setAlpha(0); break;
            case EDITOR: view.setTranslationX(view.getWidth()); break;
            case ENVIRONMENT_LOG: view.setTranslationY(dp(dialog.owner,64)); view.setAlpha(0); break;
            case NAPCAT_LOG: view.setTranslationX(dp(dialog.owner,48)); view.setAlpha(0); break;
            case APK_UPDATE: view.setTranslationY(-dp(dialog.owner,24)); view.setAlpha(0); break;
            case ABOUT: view.setScaleX(.97f); view.setScaleY(.97f); view.setAlpha(0); break;
            case BATTERY: view.setTranslationY(dp(dialog.owner,48)); view.setScaleX(.96f); view.setScaleY(.96f); view.setAlpha(0); break;
            case NAPCAT_UPDATE: view.setTranslationY(dp(dialog.owner,24)); view.setScaleX(.97f); view.setScaleY(.97f); view.setAlpha(0); break;
            default: view.setTranslationY(view.getHeight());
        }
        if (dialog.sheet() || dialog.side()) view.setAlpha(1);
        // 底部弹窗约 400ms，内容以 40ms 错峰进入。
        view.animate().translationX(0).translationY(0).scaleX(1).scaleY(1).rotation(0).alpha(1)
            .setDuration(dialog.sheet() ? ShellTheme.PANEL : dialog.side() ? 450 : 360)
            .setInterpolator(dialog.sheet() ? new OvershootInterpolator(.45f) : ShellTheme.ENTER).start();
        cascade(dialog,true);
    }

    private static void exit(MotionDialog dialog, Runnable finish) {
        View view = dialog.decor; view.animate().cancel(); cascade(dialog,false);
        float x = 0,y = 0,scale = 1,rotation = 0;
        switch (dialog.card) {
            case ARCHIVE: y = dp(dialog.owner,24); scale = .98f; break;
            case EDITOR: x = view.getWidth(); break;
            case ENVIRONMENT_LOG: y = dp(dialog.owner,64); break;
            case NAPCAT_LOG: x = dp(dialog.owner,48); break;
            case APK_UPDATE: y = -dp(dialog.owner,24); break;
            case ABOUT: scale = .97f; break;
            case BATTERY: y = dp(dialog.owner,48); scale = .96f; break;
            case NAPCAT_UPDATE: y = dp(dialog.owner,24); scale = .97f; break;
            default: y = Math.max(view.getHeight(),view.getTranslationY()+dp(dialog.owner,40));
        }
        if (dialog.activity != null) {
            ArrayList<MotionDialog> stack = OPEN.get(dialog.activity);
            if (stack != null && stack.size() == 1) {
                View root = dialog.activity.findViewById(android.R.id.content);
                root.animate().scaleX(1).scaleY(1).setDuration(ShellTheme.CLOSE).setInterpolator(ShellTheme.EXIT).start();
            }
        }
        view.animate().translationX(x).translationY(y).scaleX(scale).scaleY(scale).rotation(rotation)
            .alpha(dialog.sheet() || dialog.side() ? 1 : 0).setDuration(ShellTheme.CLOSE).setInterpolator(ShellTheme.EXIT).withEndAction(finish).start();
    }

    private static LinearLayout contentColumn(View view) {
        if (view instanceof LinearLayout && ((LinearLayout)view).getOrientation() == LinearLayout.VERTICAL && ((LinearLayout)view).getChildCount() > 1) return (LinearLayout)view;
        if (view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup)view;
            for (int i=0;i<group.getChildCount();i++) { LinearLayout found = contentColumn(group.getChildAt(i)); if (found != null) return found; }
        }
        return null;
    }
    private static void cascade(MotionDialog dialog, boolean enter) {
        ArrayList<View> targets = new ArrayList<>();
        for (int i=0;i<dialog.heading.getChildCount();i++) targets.add(dialog.heading.getChildAt(i));
        View content = dialog.findViewById(android.R.id.custom);
        LinearLayout column = content == null ? null : contentColumn(content);
        if (column != null) for (int i=0;i<column.getChildCount() && targets.size()<7;i++) targets.add(column.getChildAt(i));
        for (int i=0;i<targets.size();i++) {
            View target = targets.get(i); target.animate().cancel();
            if (enter) ShellTheme.reveal(target,i);
            else target.animate().alpha(0).translationY(dp(dialog.owner,8)).setStartDelay((targets.size()-i-1)*20L)
                .setDuration(150).setInterpolator(ShellTheme.EXIT).start();
        }
    }
    private static void drag(View handle, MotionDialog dialog) {
        handle.setOnTouchListener(new View.OnTouchListener() {
            float start; VelocityTracker tracker;
            @Override public boolean onTouch(View view, MotionEvent event) {
                if (!dialog.canCancel || dialog.decor == null || dialog.closing) return false;
                if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                    dialog.decor.animate().cancel(); start=event.getRawY();
                    tracker=VelocityTracker.obtain(); tracker.addMovement(event); return true;
                }
                if (tracker==null) return false;
                tracker.addMovement(event); float delta=event.getRawY()-start;
                if (event.getActionMasked()==MotionEvent.ACTION_MOVE) {
                    dialog.decor.setTranslationY(delta>0 ? delta : delta*.15f);
                    if (dialog.activity!=null) {
                        float p=Math.min(1,Math.max(0,delta/Math.max(1,dialog.decor.getHeight())));
                        View root=dialog.activity.findViewById(android.R.id.content); root.animate().cancel();
                        root.setScaleX(BACKGROUND_SCALE+(1-BACKGROUND_SCALE)*p); root.setScaleY(BACKGROUND_SCALE+(1-BACKGROUND_SCALE)*p);
                        WindowManager.LayoutParams attrs=dialog.getWindow().getAttributes(); attrs.dimAmount=DIM*(1-p); dialog.getWindow().setAttributes(attrs);
                    }
                    return true;
                }
                if (event.getActionMasked()==MotionEvent.ACTION_UP || event.getActionMasked()==MotionEvent.ACTION_CANCEL) {
                    tracker.computeCurrentVelocity(1000);
                    boolean close=event.getActionMasked()==MotionEvent.ACTION_UP && (delta>dialog.decor.getHeight()/3f || delta>dp(dialog.owner,18) && tracker.getYVelocity()>1000);
                    tracker.recycle(); tracker=null;
                    if (close) dialog.cancel();
                    else {
                        dialog.decor.animate().translationY(0).setDuration(300).setInterpolator(ShellTheme.ENTER).start();
                        if (dialog.activity!=null) dialog.activity.findViewById(android.R.id.content).animate().scaleX(BACKGROUND_SCALE).scaleY(BACKGROUND_SCALE).setDuration(300).setInterpolator(ShellTheme.ENTER).start();
                        WindowManager.LayoutParams attrs=dialog.getWindow().getAttributes(); attrs.dimAmount=DIM; dialog.getWindow().setAttributes(attrs);
                    }
                    return true;
                }
                return true;
            }
        });
    }
    static GradientDrawable glass(Context context,int radius) { return ShellTheme.surface(context,ShellTheme.SURFACE,radius,ShellTheme.LINE); }
    static GradientDrawable readingSurface(Context context) { return ShellTheme.surface(context,ShellTheme.SOFT,16,Color.TRANSPARENT); }
    static ScrollView scroll(Context context,View child) { return scroll(context,child,.52f); }
    static ScrollView scroll(Context context,View child,float screenFraction) {
        ScrollView view=new CappedScrollView(context,screenFraction); view.setFillViewport(false); view.setClipToPadding(false); view.addView(child); return view;
    }
    // 内容短时按内容收缩，超出时只让卡片内部滚动，保留居中浮卡比例。
    private static final class CappedScrollView extends ScrollView {
        private final float screenFraction;
        CappedScrollView(Context context,float screenFraction) { super(context); this.screenFraction=screenFraction; }
        @Override protected void onMeasure(int widthSpec,int heightSpec) {
            int cap=(int)(getResources().getDisplayMetrics().heightPixels*screenFraction);
            int mode=MeasureSpec.getMode(heightSpec), size=MeasureSpec.getSize(heightSpec);
            int limit=mode==MeasureSpec.UNSPECIFIED ? cap : Math.min(size,cap);
            int constrained=mode==MeasureSpec.EXACTLY && size<=cap ? MeasureSpec.makeMeasureSpec(size,MeasureSpec.EXACTLY)
                : MeasureSpec.makeMeasureSpec(limit,MeasureSpec.AT_MOST);
            super.onMeasure(widthSpec,constrained);
        }
    }
    /** 所有外角使用同一圆弧，浮卡留出屏幕边距，不再出现右上角的尖角。 */
    private static float[] corners(MotionDialog dialog,int radius) {
        float r=radius;
        return new float[]{r,r,r,r,r,r,r,r};
    }
    static void styleField(EditText field) { field.setTextColor(ShellTheme.INK); field.setHintTextColor(ShellTheme.MUTED); field.setTextSize(14); }
    static void styleProgress(ProgressBar progress) { ShellTheme.progress(progress); }
    static void styleButton(Button button,boolean primary) { ShellTheme.button(button,ShellTheme.ACCENT,primary); }
    static void tintButton(Button button,int accent,boolean primary) { if (button!=null) ShellTheme.button(button,accent,primary); }
    static void pressFeedback(View view) {
        view.setOnTouchListener((target,event)->{
            if (!target.isEnabled() || !ValueAnimator.areAnimatorsEnabled()) return false;
            int action=event.getActionMasked();
            if (action==MotionEvent.ACTION_DOWN || action==MotionEvent.ACTION_UP || action==MotionEvent.ACTION_CANCEL) {
                target.animate().cancel(); boolean down=action==MotionEvent.ACTION_DOWN;
                target.animate().scaleX(down ? .98f : 1).scaleY(down ? .98f : 1).setStartDelay(0).setDuration(down ? 60 : 150).setInterpolator(ShellTheme.BOTH).start();
            }
            return false;
        });
    }
    private static int dp(Context context,float value) { return ShellTheme.dp(context,value); }
}
