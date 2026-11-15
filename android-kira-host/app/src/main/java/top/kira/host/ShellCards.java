package top.kira.host;

import android.content.Context;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Content blocks for the shell's archive, editor, update and identity cards. */
final class ShellCards {
    private static final int INK = ShellTheme.INK;
    private static final int MUTED = ShellTheme.MUTED;

    static LinearLayout column(Context context, int padding) {
        LinearLayout column = new LinearLayout(context);
        column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(context, padding), dp(context, 10), dp(context, padding), dp(context, 12));
        return column;
    }

    static TextView text(Context context, String value, int size, int color) {
        TextView text = new TextView(context);
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setLineSpacing(dp(context, 3), 1);
        return text;
    }

    static void badge(LinearLayout parent, String label, int color) {
        Context context = parent.getContext();
        TextView badge = text(context, label, 11, color);
        badge.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        badge.setPadding(dp(context, 11), dp(context, 5), dp(context, 11), dp(context, 5));
        badge.setBackground(surface(context, color, 100, 0x15));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-2, -2);
        params.topMargin = dp(context, 5);
        params.bottomMargin = dp(context, 9);
        parent.addView(badge, params);
    }

    static void section(LinearLayout parent, String eyebrow, String title, String body, int accent) {
        Context context = parent.getContext();
        if (!eyebrow.isEmpty()) badge(parent, eyebrow, accent);
        TextView heading = text(context, title, 20, INK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        heading.setAccessibilityHeading(true);
        parent.addView(heading, new LinearLayout.LayoutParams(-1, -2));
        TextView detail = text(context, body, 13, MUTED);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(context, 7);
        params.bottomMargin = dp(context, 13);
        parent.addView(detail, params);
    }

    static void step(LinearLayout parent, String number, String title, String body, int accent) {
        Context context = parent.getContext();
        LinearLayout row = new LinearLayout(context);
        row.setGravity(Gravity.TOP);
        row.setPadding(0, dp(context, 10), 0, dp(context, 10));
        TextView marker = text(context, number, 13, accent);
        marker.setGravity(Gravity.CENTER);
        marker.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        marker.setBackground(surface(context, accent, 12, 0x1C));
        marker.setImportantForAccessibility(android.view.View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        row.addView(marker, new LinearLayout.LayoutParams(dp(context, 32), dp(context, 32)));
        LinearLayout copy = column(context, 0);
        copy.setPadding(dp(context, 12), 0, 0, 0);
        TextView heading = text(context, title, 14, INK);
        heading.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        copy.addView(heading);
        TextView detail = text(context, body, 12, MUTED);
        LinearLayout.LayoutParams detailParams = new LinearLayout.LayoutParams(-1, -2);
        detailParams.topMargin = dp(context, 4);
        copy.addView(detail, detailParams);
        row.addView(copy, new LinearLayout.LayoutParams(0, -2, 1));
        parent.addView(row, new LinearLayout.LayoutParams(-1, -2));
    }

    static void metric(LinearLayout parent, String value, String caption, int accent) {
        Context context = parent.getContext();
        LinearLayout block = column(context, 16);
        block.setPadding(0, dp(context, 14), 0, dp(context, 14));
        TextView number = text(context, value, 26, accent);
        number.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        block.addView(number);
        TextView description = text(context, caption, 12, MUTED);
        LinearLayout.LayoutParams copy = new LinearLayout.LayoutParams(-1, -2);
        copy.topMargin = dp(context, 4);
        block.addView(description, copy);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(context, 7);
        params.bottomMargin = dp(context, 13);
        parent.addView(block, params);
    }

    static void pair(LinearLayout parent, String label, String value, int accent) {
        Context context = parent.getContext();
        LinearLayout row = new LinearLayout(context);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(context, 14), dp(context, 12), dp(context, 14), dp(context, 12));
        row.setBackground(ShellTheme.surface(context,ShellTheme.SOFT,14,android.graphics.Color.TRANSPARENT));
        TextView name = text(context, label, 14, accent);
        name.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        row.addView(name, new LinearLayout.LayoutParams(0, -2, 1));
        TextView detail = text(context, value, 12, MUTED);
        detail.setGravity(Gravity.END);
        row.addView(detail, new LinearLayout.LayoutParams(0, -2, 2));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(context, 7);
        parent.addView(row, params);
    }

    private static GradientDrawable surface(Context context, int accent, int radius, int alpha) {
        return ShellTheme.surface(context, ShellTheme.tint(accent), radius, android.graphics.Color.TRANSPARENT);
    }

    private static int dp(Context context, int value) {
        return Math.round(value * context.getResources().getDisplayMetrics().density);
    }
}
