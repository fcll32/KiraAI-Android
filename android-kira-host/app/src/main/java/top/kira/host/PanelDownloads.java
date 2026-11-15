package top.kira.host;

import android.app.Activity;
import android.app.ProgressDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.SystemClock;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebView;
import android.widget.Toast;
import org.json.JSONObject;
import org.json.JSONTokener;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.Proxy;
import java.net.URL;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/** Saves user-requested official panel exports through Android's document picker. */
final class PanelDownloads {
    private final int createDocument;
    private static final long MAX_BYTES = 64L * 1024 * 1024;
    private final Activity activity;
    private final WebView web;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final String key = "__kira_download_" + UUID.randomUUID().toString().replace("-", "");
    private volatile boolean cancelled, destroyed, busy;
    private volatile HttpURLConnection connection;
    private ProgressDialog progress;
    private String pendingUrl, pendingCookie, pendingMime;
    private boolean pendingBlob;

    PanelDownloads(Activity activity, WebView web, int createDocument) {
        this.activity = activity; this.web = web; this.createDocument = createDocument;
    }

    void installHook() {
        if (!currentLocal()) return;
        // Retain the one clicked Blob before the official frontend revokes its URL.
        // The state is page-scoped, byte-bounded and never exposes a native interface.
        String script = "(()=>{const k=" + JSONObject.quote(key) + ";if(window[k])return;"
            + "const s={entry:null};Object.defineProperty(window,k,{value:s,configurable:true});"
            + "const click=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){"
            + "const u=this.href;if(u&&u.startsWith('blob:'+location.origin+'/')){"
            + "if(s.entry)return click.apply(this,arguments);"
            + "const e={url:u,name:this.download||'',blob:null,error:null,part:null};s.entry=e;"
            + "fetch(u).then(async r=>{if(!r.body)throw Error('WebView不支持导出');"
            + "const reader=r.body.getReader(),parts=[];let size=0;while(true){const p=await reader.read();"
            + "if(p.done)break;size+=p.value.byteLength;if(size>67108864){await reader.cancel();throw Error('文件超过64MB');}parts.push(p.value);}"
            + "return new Blob(parts,{type:r.headers.get('Content-Type')||'application/octet-stream'});}).then(b=>{if(s.entry!==e)return;"
            + "if(b.size>67108864)throw Error('文件超过64MB');e.blob=b;}).catch(()=>{e.error='无法读取导出文件或文件超过64MB';});}"
            + "return click.apply(this,arguments);};})()";
        web.evaluateJavascript(script, null);
    }

    void request(String url, String disposition, String mime) {
        Uri uri = Uri.parse(url);
        if ("https".equals(uri.getScheme())) {
            try { activity.startActivity(new Intent(Intent.ACTION_VIEW, uri)); }
            catch (Exception ignored) { message("无法打开下载链接"); }
            return;
        }
        boolean blob = MainActivity.localBlob(url);
        if (!currentLocal() || (!blob && !MainActivity.local(uri))) { message("下载地址不受支持"); return; }
        if (busy) { message("请等待当前文件导出完成"); return; }
        busy = true;
        cancelled = false;
        pendingUrl = url;
        pendingBlob = blob;
        pendingCookie = blob ? "" : CookieManager.getInstance().getCookie(url);
        pendingMime = mime != null && mime.matches("[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+") ? mime : "application/octet-stream";
        String filename = safeName(URLUtil.guessFileName(url, disposition, pendingMime));
        if (!blob) { choose(filename); return; }
        begin("正在准备导出文件");
        worker.execute(() -> {
            try {
                JSONObject entry = waitForBlob();
                String name = entry.optString("name");
                ui(() -> { dismiss(); choose(name.isEmpty() ? filename : safeName(name)); });
            } catch (Exception failure) { fail(failure); }
        });
    }

    private void choose(String filename) {
        try {
            activity.startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType(pendingMime).putExtra(Intent.EXTRA_TITLE, filename), createDocument);
        } catch (RuntimeException failure) { cleanup(); message("无法打开文件保存位置选择器"); }
    }

    boolean result(int request, int result, Intent data) {
        if (request != createDocument) return false;
        if (!busy) return true;
        if (result != Activity.RESULT_OK || data == null || data.getData() == null) { cleanup(); return true; }
        Uri destination = data.getData();
        if (!"content".equals(destination.getScheme())) { cleanup(); message("请选择系统文件保存位置"); return true; }
        begin("正在保存导出文件");
        worker.execute(() -> {
            try (OutputStream output = activity.getContentResolver().openOutputStream(destination, "w")) {
                if (output == null) throw new IOException("无法写入所选位置");
                if (pendingBlob) saveBlob(output);
                else saveHttp(output);
                output.flush();
                ui(() -> { cleanup(); message("文件已保存"); });
            } catch (Exception failure) { fail(failure); }
        });
        return true;
    }

    private void saveHttp(OutputStream output) throws Exception {
        HttpURLConnection request = (HttpURLConnection) new URL(pendingUrl).openConnection(Proxy.NO_PROXY);
        connection = request;
        request.setInstanceFollowRedirects(false);
        request.setConnectTimeout(15000);
        request.setReadTimeout(15000);
        if (pendingCookie != null && !pendingCookie.isEmpty()) request.setRequestProperty("Cookie", pendingCookie);
        request.setRequestProperty("Accept-Encoding", "identity");
        try {
            if (request.getResponseCode() != 200) throw new IOException("面板导出返回 HTTP " + request.getResponseCode());
            long total = request.getContentLengthLong();
            if (total > MAX_BYTES) throw new IOException("文件超过64MB，请通过电脑浏览器导出");
            try (InputStream input = request.getInputStream()) {
                byte[] buffer = new byte[65536];
                long done = 0, started = SystemClock.elapsedRealtime(), last = 0;
                int count;
                while ((count = input.read(buffer)) != -1) {
                    checkpoint();
                    done += count;
                    if (done > MAX_BYTES) throw new IOException("文件超过64MB");
                    output.write(buffer, 0, count);
                    long now = SystemClock.elapsedRealtime();
                    if (now - last >= 200) { update(done, total, started); last = now; }
                }
                if (total >= 0 && done != total) throw new IOException("导出文件未传输完整");
            }
        } finally { request.disconnect(); connection = null; }
    }

    private JSONObject waitForBlob() throws Exception {
        long deadline = SystemClock.elapsedRealtime() + 15000;
        while (SystemClock.elapsedRealtime() < deadline) {
            checkpoint();
            JSONObject entry = evaluateObject("(()=>{const s=window[" + JSONObject.quote(key) + "];const e=s&&s.entry;"
                + "if(!e||e.url!==" + JSONObject.quote(pendingUrl) + ")return {error:'导出文件已失效，请重新导出'};"
                + "return {ready:!!e.blob,error:e.error,name:e.name,size:e.blob?e.blob.size:0};})()");
            if (!entry.isNull("error") && !entry.optString("error").isEmpty()) throw new IOException(entry.getString("error"));
            if (entry.optBoolean("ready")) return entry;
            Thread.sleep(100);
        }
        throw new IOException("读取导出文件超时，请重新导出");
    }

    private void saveBlob(OutputStream output) throws Exception {
        JSONObject entry = waitForBlob();
        long total = entry.getLong("size");
        if (total < 0 || total > MAX_BYTES) throw new IOException("导出文件超过64MB");
        long started = SystemClock.elapsedRealtime();
        for (long offset = 0; offset < total; offset += 49152) {
            checkpoint();
            long end = Math.min(total, offset + 49152);
            String begin = "(()=>{const s=window[" + JSONObject.quote(key) + "];const e=s&&s.entry;"
                + "if(!e||e.url!==" + JSONObject.quote(pendingUrl) + "||!e.blob)return {error:'文件已失效'};"
                + "e.part=null;const r=new FileReader();r.onload=()=>{e.part={data:String(r.result).split(',')[1]};};"
                + "r.onerror=()=>{e.part={error:'文件读取失败'};};r.readAsDataURL(e.blob.slice(" + offset + "," + end + "));return {ok:true};})()";
            JSONObject launched = evaluateObject(begin);
            if (launched.has("error")) throw new IOException(launched.getString("error"));
            JSONObject part = null;
            long deadline = SystemClock.elapsedRealtime() + 10000;
            while (SystemClock.elapsedRealtime() < deadline) {
                checkpoint();
                part = evaluateObject("(()=>{const s=window[" + JSONObject.quote(key) + "];return s&&s.entry&&s.entry.part||{};})()");
                if (part.has("data") || part.has("error")) break;
                Thread.sleep(30);
            }
            if (part == null || !part.has("data")) throw new IOException("读取导出内容失败");
            String encoded = part.getString("data");
            if (encoded.length() > 65536) throw new IOException("导出数据块过大");
            byte[] bytes = android.util.Base64.decode(encoded, android.util.Base64.DEFAULT);
            if (bytes.length != end - offset) throw new IOException("导出数据块不完整");
            output.write(bytes);
            update(end, total, started);
        }
    }

    private JSONObject evaluateObject(String script) throws Exception {
        ArrayBlockingQueue<String> result = new ArrayBlockingQueue<>(1);
        activity.runOnUiThread(() -> {
            if (destroyed || !currentLocal()) { result.offer("null"); return; }
            web.evaluateJavascript(script, value -> result.offer(value == null ? "null" : value));
        });
        String value = result.poll(10, TimeUnit.SECONDS);
        checkpoint();
        if (value == null) throw new IOException("面板响应超时");
        Object object = new JSONTokener(value).nextValue();
        if (!(object instanceof JSONObject)) throw new IOException("面板导出状态已失效");
        return (JSONObject) object;
    }

    private boolean currentLocal() { return MainActivity.local(Uri.parse(web.getUrl() == null ? "" : web.getUrl())); }
    private static String safeName(String name) {
        String result = name.replaceAll("[\\\\/\\x00-\\x1f:*?\"<>|]", "_");
        if (result.isEmpty() || result.equals(".") || result.equals("..")) result = "kira-export.bin";
        return result.length() > 140 ? result.substring(result.length() - 140) : result;
    }
    private void begin(String title) {
        progress = new ProgressDialog(activity);
        progress.setTitle(title);
        progress.setProgressStyle(ProgressDialog.STYLE_HORIZONTAL);
        progress.setIndeterminate(true);
        progress.setMax(100);
        progress.setCancelable(true);
        progress.setOnCancelListener(dialog -> { cancelled = true; HttpURLConnection request = connection; if (request != null) request.disconnect(); });
        progress.show();
    }
    private void update(long done, long total, long started) {
        double speed = done * 1000d / Math.max(1, SystemClock.elapsedRealtime() - started);
        ui(() -> {
            if (progress == null) return;
            progress.setIndeterminate(total <= 0);
            if (total > 0) progress.setProgress((int) (done * 100 / total));
            progress.setMessage(RuntimeStatus.bytes(done) + (total > 0 ? " / " + RuntimeStatus.bytes(total) : " · 总大小未知") + " · " + RuntimeStatus.bytes(speed) + "/s");
        });
    }
    private void checkpoint() throws IOException { if (cancelled || destroyed) throw new IOException("已取消保存，目标位置可能有未完成的文件"); }
    private void fail(Exception failure) { ui(() -> { cleanup(); message(failure.getMessage() == null ? "文件导出失败" : failure.getMessage()); }); }
    private void dismiss() { if (progress != null) { progress.dismiss(); progress = null; } }
    private void cleanup() {
        busy = false;
        dismiss();
        pendingCookie = null;
        if (currentLocal()) web.evaluateJavascript("(()=>{const s=window[" + JSONObject.quote(key) + "];if(s)s.entry=null;})()", null);
    }
    private void message(String text) { Toast.makeText(activity, text, Toast.LENGTH_LONG).show(); }
    private void ui(Runnable action) { activity.runOnUiThread(() -> { if (!destroyed && !activity.isFinishing()) action.run(); }); }
    void destroy() {
        cancelled = true;
        HttpURLConnection request = connection;
        if (request != null) request.disconnect();
        cleanup();
        destroyed = true;
        worker.shutdownNow();
    }
}
