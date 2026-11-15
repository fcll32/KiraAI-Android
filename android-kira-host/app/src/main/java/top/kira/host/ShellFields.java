package top.kira.host;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Floating labels and local validation feedback for native shell forms. */
final class ShellFields {
    private static final int BLUE = ShellTheme.ACCENT, ERROR = 0xFFBA433C;

    static LinearLayout wrap(Context context, String title, EditText editor) {
        Binding binding = new Binding(context, title, editor);
        editor.setTag(binding);
        return binding.group;
    }

    static void error(EditText editor, String message) {
        if (editor.getTag() instanceof Binding) ((Binding) editor.getTag()).showError(message);
        else editor.setError(message);
    }

    private static final class Binding {
        final LinearLayout group;
        final FrameLayout frame;
        final View rule;
        final TextView label, error;
        final EditText editor;
        ObjectAnimator shake;
        boolean failed;

        Binding(Context context, String title, EditText editor) {
            this.editor = editor;
            group = new LinearLayout(context);
            group.setOrientation(LinearLayout.VERTICAL);
            group.setPadding(0, dp(8), 0, dp(7));
            frame = new FrameLayout(context);
            frame.setMinimumHeight(dp(58));
            editor.setHint("");
            editor.setBackground(null);
            editor.setTextColor(ShellTheme.INK);
            editor.setPadding(dp(14), dp(26), dp(14), dp(12));
            frame.addView(editor, new FrameLayout.LayoutParams(-1, -2));
            label = new TextView(context);
            label.setText(title);
            label.setTextSize(13);
            label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            label.setPivotX(0);
            label.setPivotY(0);
            label.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
            FrameLayout.LayoutParams labelParams = new FrameLayout.LayoutParams(-2, -2);
            labelParams.leftMargin = dp(14);
            labelParams.topMargin = dp(17);
            frame.addView(label, labelParams);
            rule = new View(context);
            FrameLayout.LayoutParams ruleParams = new FrameLayout.LayoutParams(-1, dp(1), android.view.Gravity.BOTTOM);
            ruleParams.leftMargin = dp(14); ruleParams.rightMargin = dp(14);
            frame.addView(rule, ruleParams);
            group.addView(frame, new LinearLayout.LayoutParams(-1, -2));
            error = new TextView(context);
            error.setTextSize(12);
            error.setTextColor(ERROR);
            error.setPadding(dp(9), dp(6), dp(9), 0);
            error.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
            error.setMinLines(1);
            error.setVisibility(View.INVISIBLE);
            group.addView(error, new LinearLayout.LayoutParams(-1, -2));
            editor.setAccessibilityDelegate(new View.AccessibilityDelegate() {
                @Override public void onInitializeAccessibilityNodeInfo(View host, AccessibilityNodeInfo info) {
                    super.onInitializeAccessibilityNodeInfo(host, info);
                    info.setHintText(title);
                    info.setContentInvalid(failed);
                    if (failed) info.setError(error.getText());
                }
            });
            editor.setOnFocusChangeListener((view, focus) -> update(true));
            editor.addTextChangedListener(new TextWatcher() {
                @Override public void beforeTextChanged(CharSequence text, int start, int count, int after) { }
                @Override public void onTextChanged(CharSequence text, int start, int before, int count) {
                    if (failed) {
                        failed = false;
                        error.setText("");
                        error.setVisibility(View.INVISIBLE);
                        if (shake != null) { shake.cancel(); shake = null; }
                        frame.setTranslationX(0);
                    }
                    update(true);
                }
                @Override public void afterTextChanged(Editable text) { }
            });
            group.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
                @Override public void onViewAttachedToWindow(View view) { update(false); }
                @Override public void onViewDetachedFromWindow(View view) {
                    label.animate().cancel();
                    if (shake != null) { shake.cancel(); shake = null; }
                    frame.setTranslationX(0);
                }
            });
            update(false);
        }

        private int dp(float value) { return Math.round(value * editor.getResources().getDisplayMetrics().density); }

        private void update(boolean animate) {
            boolean floating = editor.hasFocus() || editor.length() > 0;
            label.setTextColor(failed ? ERROR : editor.hasFocus() ? BLUE : ShellTheme.MUTED);
            frame.setBackground(ShellTheme.surface(editor.getContext(), editor.hasFocus() ? ShellTheme.SURFACE : ShellTheme.SOFT,
                16, failed ? ERROR : editor.hasFocus() ? BLUE : ShellTheme.LINE));
            rule.setBackgroundColor(android.graphics.Color.TRANSPARENT);
            label.animate().cancel();
            float y = floating ? -dp(10) : 0;
            float scale = floating ? 0.82f : 1f;
            if (animate && ValueAnimator.areAnimatorsEnabled())
                label.animate().translationY(y).scaleX(scale).scaleY(scale).setDuration(200).setInterpolator(ShellTheme.ENTER).start();
            else { label.setTranslationY(y); label.setScaleX(scale); label.setScaleY(scale); }
        }

        void showError(String message) {
            failed = true;
            error.setText(message);
            error.setVisibility(View.VISIBLE);
            update(false);
            if (shake != null) shake.cancel();
            if (ValueAnimator.areAnimatorsEnabled()) {
                shake = ObjectAnimator.ofFloat(frame, View.TRANSLATION_X,
                    0, dp(-10), dp(10), dp(-6), dp(6), dp(-3), dp(3), 0);
                shake.setDuration(400);
                shake.setInterpolator(ShellTheme.BOTH);
                shake.start();
            }
        }
    }
}
