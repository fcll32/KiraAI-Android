package top.kira.host;

import android.webkit.WebView;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Presentation-only stylesheet for the embedded official panel. */
final class ShellWebTheme {
    private static String stylesheet;
    static void install(WebView view) {
        try {
            if (stylesheet == null) {
                try (InputStream input = view.getContext().getAssets().open("shell-theme.css");
                     ByteArrayOutputStream buffer = new ByteArrayOutputStream()) {
                    byte[] bytes = new byte[4096];
                    int count;
                    while ((count = input.read(bytes)) != -1) buffer.write(bytes, 0, count);
                    stylesheet = new String(buffer.toByteArray(), StandardCharsets.UTF_8);
                }
            }
            view.evaluateJavascript("(function(){var s=document.getElementById('kira-shell-theme');"
                + "if(!s){s=document.createElement('style');s.id='kira-shell-theme';document.head.appendChild(s);}"
                + "s.textContent=" + JSONObject.quote(stylesheet) + ";})()", null);
        } catch (java.io.IOException ignored) { }
    }
}
