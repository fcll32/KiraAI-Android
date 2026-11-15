package top.kira.host;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.view.Gravity;
import android.view.View;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Selectable logs and font-independent terminal QR modules. Put inside a vertical ScrollView. */
public final class ShellLogView extends LinearLayout {
    public ShellLogView(Context context) {
        super(context);
        setOrientation(VERTICAL);
        setPadding(dp(2), dp(10), dp(2), dp(10));
    }

    public void setLogText(CharSequence value) {
        removeAllViews();
        for (TerminalQr.Part part : TerminalQr.parse(value == null ? "" : value.toString())) {
            if (part.qr != null) {
                TextView label = new TextView(getContext());
                label.setText("日志中的二维码");
                label.setTextColor(ShellTheme.INK);
                label.setTextSize(12);
                label.setPadding(0, dp(8), 0, dp(8));
                addView(label, new LayoutParams(-1, -2));
                QrView image = new QrView(getContext(), part.qr);
                image.setContentDescription("日志中的二维码");
                LayoutParams imageParams = new LayoutParams(-1, -2);
                imageParams.bottomMargin = dp(12);
                addView(image, imageParams);
            } else if (!part.text.isEmpty()) {
                HorizontalScrollView horizontal = new HorizontalScrollView(getContext());
                horizontal.setFillViewport(true);
                TextView text = new TextView(getContext());
                text.setText(part.text);
                text.setTextSize(11);
                text.setTextColor(ShellTheme.INK);
                text.setTypeface(Typeface.MONOSPACE);
                text.setTextIsSelectable(true);
                text.setHorizontallyScrolling(true);
                text.setGravity(Gravity.START);
                text.setIncludeFontPadding(false);
                horizontal.addView(text, new HorizontalScrollView.LayoutParams(-2, -2));
                addView(horizontal, new LayoutParams(-1, -2));
            }
        }
    }

    /** Canvas has integer square modules and its own white quiet zone at every text/font scale. */
    private static final class QrView extends View {
        private final TerminalQr.Matrix matrix;
        private final Paint paint = new Paint();
        QrView(Context context, TerminalQr.Matrix matrix) {
            super(context);
            this.matrix = matrix;
            paint.setAntiAlias(false);
            paint.setColor(Color.BLACK);
            setBackgroundColor(Color.WHITE);
        }
        @Override protected void onMeasure(int widthSpec, int heightSpec) {
            int desired = Math.round(304 * getResources().getDisplayMetrics().density);
            int width = resolveSize(Math.max(matrix.size + 8, desired), widthSpec);
            int step = Math.max(1, width / (matrix.size + 8));
            int side = (matrix.size + 8) * step;
            setMeasuredDimension(width, side);
        }
        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            int step = Math.max(1, Math.min(getWidth(), getHeight()) / (matrix.size + 8));
            int side = (matrix.size + 8) * step;
            int startX = (getWidth() - side) / 2 + 4 * step;
            int startY = (getHeight() - side) / 2 + 4 * step;
            for (int y = 0; y < matrix.size; y++) for (int x = 0; x < matrix.size; x++) if (matrix.dark(x, y)) {
                int left = startX + x * step, top = startY + y * step;
                canvas.drawRect(left, top, left + step, top + step, paint);
            }
        }
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
