package top.kira.host;

import android.webkit.WebView;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Presentation-only stylesheet for the embedded official panel. */
final class ShellWebTheme {
    /** JavaScript name of the one-method bridge that reports the panel's day/night state. */
    static final String BRIDGE = "KiraShellTheme";
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
                + "s.textContent=" + JSONObject.quote(stylesheet) + ";"
                // 官方面板切换日间/夜间只改 html 的 dark 类；变化时告诉原生端，让标题栏、底栏和系统栏同步变色。
                + "var r=document.documentElement;if(r.__kiraNight)return;r.__kiraNight=1;var last=null;"
                + "function report(){var d=r.classList.contains('dark');if(d===last)return;last=d;"
                + "try{" + BRIDGE + ".dark(d);}catch(e){}}"
                + "new MutationObserver(report).observe(r,{attributes:true,attributeFilter:['class']});report();})()", null);
        } catch (java.io.IOException ignored) { }
    }
}
