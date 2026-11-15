package top.kira.host;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.content.pm.Signature;
import android.content.res.AssetFileDescriptor;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.CancellationSignal;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.widget.EditText;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.ProgressBar;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.HashSet;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;

final class ApkUpdater {
    private static final long MAX_APK = 256L * 1024 * 1024;
    private static final Semaphore LOCAL_IMPORT_SLOT = new Semaphore(1);
    private final Activity activity;
    private final SharedPreferences preferences;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final Object transferCommitLock = new Object();
    private final UpdateManifestFetch manifestFetch = new UpdateManifestFetch();
    private final UpdateCheckCache<Release> checkCache = new UpdateCheckCache<>(60000, android.os.SystemClock::elapsedRealtime);
    private volatile boolean cancelled, busy, destroyed;
    private volatile HttpURLConnection connection;
    private AlertDialog progress;
    private ProgressBar progressBar;
    private TextView progressLabel;
    private AlertDialog formDialog;
    private int generation;
    private LocalImport localImport;

    private static final class LocalImport {
        final int generation;
        final CancellationSignal signal = new CancellationSignal();
        volatile InputStream input;
        volatile boolean cancelled;
        boolean workerDone;
        int pendingAborts;

        LocalImport(int generation) { this.generation = generation; }

        void checkpoint() throws IOException {
            if (cancelled) throw new IOException("操作已取消");
        }
    }

    private static final class Release {
        long code, size;
        String version, url, sha, notes;
    }

    ApkUpdater(Activity activity) {
        this.activity = activity;
        preferences = activity.getSharedPreferences("host_updates", Context.MODE_PRIVATE);
    }

    /** R5C3 rule for the downloaded update: gone once installed (or superseded), otherwise after seven days unused. */
    static final long STALE_UPDATE_MS = 7L * 24 * 60 * 60 * 1000;

    void cleanStaleDownloads() {
        worker.execute(() -> {
            if (busy || destroyed) return;
            File directory = new File(activity.getFilesDir(), "host-updates");
            if (!directory.isDirectory() || java.nio.file.Files.isSymbolicLink(directory.toPath())) return;
            long now = System.currentTimeMillis();
            File apk = new File(directory, "host-update.apk");
            try {
                if (apk.isFile() && !java.nio.file.Files.isSymbolicLink(apk.toPath())) {
                    PackageManager manager = activity.getPackageManager();
                    PackageInfo downloaded = manager.getPackageArchiveInfo(apk.getAbsolutePath(), 0);
                    long installed = manager.getPackageInfo(activity.getPackageName(), 0).getLongVersionCode();
                    // Running a version at least as new proves the install finished; an unreadable file is not an update.
                    if (downloaded == null || downloaded.getLongVersionCode() <= installed || now - apk.lastModified() > STALE_UPDATE_MS) {
                        if (!busy) java.nio.file.Files.deleteIfExists(apk.toPath());
                    }
                }
                for (String name : new String[]{"host-update.part", "host-local.part"}) {
                    File partial = new File(directory, name);
                    if (partial.isFile() && !java.nio.file.Files.isSymbolicLink(partial.toPath())
                            && now - partial.lastModified() > STALE_UPDATE_MS && !busy)
                        java.nio.file.Files.deleteIfExists(partial.toPath());
                }
            } catch (Exception ignored) {
                // Clean-up is best effort; a file that cannot be checked is left for the next start.
            }
        });
    }

    void show() {
        if (destroyed || activity.isFinishing() || busy || (formDialog != null && formDialog.isShowing())) return;
        LinearLayout form = ShellCards.column(activity, 20);
        versionTicket(form);
        TextView description = ShellCards.text(activity,
            "检查应用的新版本，也可以选择本地安装包。", 13, ShellTheme.MUTED);
        LinearLayout.LayoutParams copy = new LinearLayout.LayoutParams(-1, -2);
        copy.topMargin = dp(12); copy.bottomMargin = dp(12);
        form.addView(description, copy);
        TextView sourceLabel = ShellCards.text(activity, "更新来源", 12, ShellTheme.ACCENT);
        sourceLabel.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        sourceLabel.setAccessibilityHeading(true);
        form.addView(sourceLabel);
        EditText manifest = field(form, "APK 更新清单（HTTPS）", preferences.getString("manifest", BuildConfig.APK_UPDATE_MANIFEST));
        EditText mirror = field(form, "国内镜像前缀", preferences.getString("mirror", BuildConfig.APK_UPDATE_MIRROR));
        TextView scope = ShellCards.text(activity, "这里只更新手机应用本身。KiraAI 核心请在官方面板内更新。", 11, ShellTheme.MUTED);
        scope.setPadding(0, dp(8), 0, dp(8));
        form.addView(scope);
        File downloaded = new File(activity.getFilesDir(), "host-updates/host-update.apk");
        if (downloaded.isFile()) {
            Button install = new Button(activity);
            install.setText("安装已下载的 APK");
            ShellDialogs.styleButton(install, true);
            install.setOnClickListener(view -> install());
            form.addView(install);
        }
        AlertDialog dialog = ShellDialogs.builder(activity, ShellDialogs.Card.APK_UPDATE).setTitle("应用版本更新").setView(ShellDialogs.scroll(activity, form, .42f))
            .setNegativeButton("关闭", null).setNeutralButton("选择本地 APK", (selection, which) -> chooseLocal())
            .setPositiveButton("检查更新", null).create();
        formDialog = dialog;
        dialog.setOnDismissListener(ignored -> { if (formDialog == dialog) formDialog = null; });
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
                String source;
                try {
                    source = https(manifest.getText().toString().trim());
                } catch (Exception error) { ShellFields.error(manifest, "需要无凭据的 HTTPS 更新清单地址"); return; }
                String prefix = mirror.getText().toString().trim();
                try { validateMirror(prefix); }
                catch (Exception error) { ShellFields.error(mirror, "镜像前缀须使用 HTTPS 并以 / 结尾"); return; }
                preferences.edit().putString("manifest", source).putString("mirror", prefix).apply();
                dialog.dismiss();
                check(source, prefix);
            }));
        dialog.show();
    }

    private int dp(int value) { return Math.round(value * activity.getResources().getDisplayMetrics().density); }

    private void versionTicket(LinearLayout form) {
        LinearLayout ticket = new LinearLayout(activity);
        ticket.setOrientation(LinearLayout.VERTICAL);
        ticket.setPadding(dp(18), dp(16), dp(18), dp(16));
        GradientDrawable background = ShellTheme.gradient(activity,ShellTheme.SURFACE,ShellTheme.LILAC_SOFT,24,ShellTheme.LINE);
        ticket.setBackground(background);
        LinearLayout details = new LinearLayout(activity);
        details.setGravity(Gravity.CENTER_VERTICAL);
        View emblem = ShellIcons.view(activity,ShellIcons.PACKAGE);
        emblem.setBackground(ShellTheme.surface(activity,ShellTheme.SURFACE,18,android.graphics.Color.TRANSPARENT));
        emblem.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        LinearLayout.LayoutParams emblemParams=new LinearLayout.LayoutParams(dp(50),dp(50));
        emblemParams.rightMargin=dp(14); details.addView(emblem,emblemParams);
        LinearLayout version = new LinearLayout(activity);
        version.setOrientation(LinearLayout.VERTICAL);
        TextView caption = ShellCards.text(activity, "当前应用版本", 11, ShellTheme.MUTED);
        version.addView(caption);
        TextView number = ShellCards.text(activity, BuildConfig.VERSION_NAME, 25, ShellTheme.ACCENT);
        number.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        version.addView(number);
        details.addView(version, new LinearLayout.LayoutParams(0, -2, 1));
        TextView build = ShellCards.text(activity, "构建\n" + BuildConfig.VERSION_CODE, 12, ShellTheme.MUTED);
        build.setTypeface(Typeface.MONOSPACE);
        build.setGravity(Gravity.END);
        details.addView(build);
        ticket.addView(details, new LinearLayout.LayoutParams(-1, -2));
        View perforation = new View(activity);
        GradientDrawable line = new GradientDrawable();
        line.setColor(0x00FFFFFF);
        line.setStroke(dp(1), ShellTheme.LINE);
        perforation.setBackground(line);
        LinearLayout.LayoutParams divider = new LinearLayout.LayoutParams(-1, dp(1));
        divider.topMargin = dp(12); divider.bottomMargin = dp(10);
        ticket.addView(perforation, divider);
        TextView footer = ShellCards.text(activity, "KiraAI · Android", 10, ShellTheme.MUTED);
        footer.setLetterSpacing(0);
        ticket.addView(footer);
        form.addView(ticket, new LinearLayout.LayoutParams(-1, -2));
    }

    private EditText field(LinearLayout form, String hint, String text) {
        EditText field = new EditText(activity);
        field.setText(text);
        field.setTextSize(13);
        field.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        ShellDialogs.styleField(field);
        form.addView(ShellFields.wrap(activity, hint, field));
        return field;
    }

    private void check(String source, String mirror) {
        check(source, mirror, false);
    }

    private void check(String source, String mirror, boolean force) {
        if (destroyed || busy) return;
        UpdateCheckCache.Hit<Release> cached = force ? null : checkCache.find(source, mirror);
        if (cached != null) { present(cached.value, source, mirror, cached.caption()); return; }
        begin("检查 APK 更新", false);
        int currentGeneration = generation;
        progressLabel.setText("正在检查更新清单，最多等待6秒");
        worker.execute(() -> {
            try {
                List<String> addresses = new ArrayList<>();
                addresses.add(source);
                // The default repository may temporarily be private. Do not let a mirror's stale public copy mask that fact.
                if (!source.equals(BuildConfig.APK_UPDATE_MANIFEST)) {
                    String alternative = mirrored(source, mirror);
                    if (!alternative.equals(source)) addresses.add(alternative);
                }
                Release release = manifestFetch.fetch(addresses, this::parseRelease);
                ui(() -> {
                    if (currentGeneration != generation || cancelled) { if (currentGeneration == generation) end(); return; }
                    checkCache.put(source, mirror, release);
                    end();
                    present(release, source, mirror, "本次联网检查结果。\n");
                });
            } catch (Exception error) {
                ui(() -> {
                    if (currentGeneration != generation) return;
                    end();
                    if (!cancelled) message(error instanceof java.io.FileNotFoundException
                        ? "更新清单尚未发布、仓库暂为私有或无公开访问权限。可选择本地 APK，或填写可访问的 HTTPS 更新清单。"
                        : "检查更新未完成。请重试或更换更新清单。\n" + error.getMessage());
                });
            }
        });
    }

    private Release parseRelease(byte[] raw) throws Exception {
        JSONObject metadata = new JSONObject(new String(raw, StandardCharsets.UTF_8));
        if (metadata.getInt("schema") != 1 || !"android-linux-host".equals(metadata.getString("channel"))
            || !activity.getPackageName().equals(metadata.getString("application_id")))
            throw new IOException("APK 更新清单的应用身份不匹配");
        if (metadata.getInt("min_sdk") > android.os.Build.VERSION.SDK_INT)
            throw new IOException("新版 APK 需要更高的 Android 版本");
        JSONObject apk = metadata.getJSONObject("apk");
        Release release = new Release();
        release.code = metadata.getLong("version_code");
        release.version = metadata.getString("version_name");
        release.url = https(apk.getString("url")); release.sha = apk.getString("sha256"); release.size = apk.getLong("size");
        if (release.code < 1 || !release.sha.matches("[0-9a-f]{64}") || release.size < 1 || release.size > MAX_APK)
            throw new IOException("更新清单缺少有效的版本、大小或 SHA256");
        release.notes = metadata.optString("notes");
        if (release.notes.length() > 4000) release.notes = release.notes.substring(0, 4000);
        return release;
    }

    private void present(Release release, String source, String mirror, String caption) {
        try {
            long current = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0).getLongVersionCode();
            AlertDialog.Builder result = ShellDialogs.builder(activity, ShellDialogs.Card.APK_UPDATE).setTitle("APK " + release.version)
                .setNeutralButton("重新检查", (dialog, which) -> check(source, mirror, true));
            if (release.code <= current) result.setMessage(caption + "当前安装版本不低于清单中的已发布版本。")
                .setPositiveButton("知道了", null).show();
            else result.setMessage(caption + "下载大小 " + RuntimeStatus.bytes(release.size) + "\n" + release.notes)
                .setNegativeButton("暂不更新", null)
                .setPositiveButton("通过国内镜像下载", (dialog, which) -> download(release.url, mirror, release.sha, release.size, release.code)).show();
        } catch (Exception error) { message("无法读取当前 APK 版本，请稍后重试"); }
    }

    private void download(String url, String mirror, String expected, long size, long code) {
        if (destroyed || busy) return;
        begin("正在下载 APK", true);
        int currentGeneration = generation;
        worker.execute(() -> {
            File partial = new File(activity.getFilesDir(), "host-updates/host-update.part");
            try {
                File folder = partial.getParentFile();
                if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("无法创建更新目录");
                if (Files.isSymbolicLink(partial.toPath())) throw new IOException("更新暂存文件无效");
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long done = 0, started = android.os.SystemClock.elapsedRealtime(), last = 0;
                HttpURLConnection request = open(mirrored(url, mirror));
                try (InputStream input = request.getInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    byte[] buffer = new byte[65536];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        checkpoint();
                        done += count;
                        if (done > size) throw new IOException("下载文件超过清单大小");
                        digest.update(buffer, 0, count);
                        output.write(buffer, 0, count);
                        long now = android.os.SystemClock.elapsedRealtime();
                        if (now - last >= 200) {
                            update(done, size, done * 1000d / Math.max(1, now - started));
                            last = now;
                        }
                    }
                    output.getFD().sync();
                } finally { request.disconnect(); }
                checkpoint();
                if (done != size || !expected.equals(hex(digest.digest()))) throw new IOException("APK 完整性校验失败，请重新下载");
                verifyIdentity(partial, code);
                commitTransfer(partial, currentGeneration);
                completeTransfer(currentGeneration, null);
            } catch (Exception error) {
                partial.delete();
                completeTransfer(currentGeneration, "APK 下载失败。" + error.getMessage());
            }
        });
    }

    private void verifyIdentity(File file, long expectedCode) throws Exception {
        PackageManager manager = activity.getPackageManager();
        PackageInfo next = manager.getPackageArchiveInfo(file.getAbsolutePath(), PackageManager.GET_SIGNING_CERTIFICATES);
        PackageInfo current = manager.getPackageInfo(activity.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
        if (next == null || !activity.getPackageName().equals(next.packageName) || next.signingInfo == null
                || next.getLongVersionCode() <= current.getLongVersionCode()
                || (expectedCode > 0 && next.getLongVersionCode() != expectedCode)
                || next.applicationInfo == null || next.applicationInfo.minSdkVersion > android.os.Build.VERSION.SDK_INT)
            throw new IOException("APK 的应用、版本或 Android 要求不匹配");
        if (!signatures(current.signingInfo.getApkContentsSigners()).equals(signatures(next.signingInfo.getApkContentsSigners())))
            throw new IOException("APK 签名与已安装版本不同");
    }

    private static Set<String> signatures(Signature[] signatures) throws Exception {
        Set<String> result = new HashSet<>();
        if (signatures != null) for (Signature signature : signatures)
            result.add(hex(MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
        if (result.isEmpty()) throw new IOException("APK 缺少签名");
        return result;
    }

    private void offerInstall() {
        ShellDialogs.builder(activity, ShellDialogs.Card.APK_UPDATE).setTitle("APK 已校验").setMessage("通过 Android 安装器更新应用。KiraAI 核心保留在手机运行环境中。")
            .setNegativeButton("稍后", null).setPositiveButton("安装 APK", (dialog, which) -> install()).show();
    }

    private void install() {
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + activity.getPackageName())));
            message("授权后，请再次打开 APK 更新并选择安装已下载的 APK");
            return;
        }
        try {
            File file = new File(activity.getFilesDir(), "host-updates/host-update.apk");
            verifyIdentity(file, -1);
            Uri uri = Uri.parse("content://" + activity.getPackageName() + ".hostupdates/host-update.apk");
            Intent intent = new Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.setClipData(ClipData.newRawUri("APK 更新", uri));
            activity.startActivity(intent);
        } catch (Exception error) { message("无法安装 APK。" + error.getMessage()); }
    }

    private void chooseLocal() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
            .setType("application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        activity.startActivityForResult(intent, 42);
    }

    boolean result(int request, int result, Intent data) {
        if (request != 42) return false;
        if (destroyed || busy || result != Activity.RESULT_OK || data == null || data.getData() == null) return true;
        Uri selected = data.getData();
        if (!"content".equals(selected.getScheme())) { message("请选择系统文件选择器中的 APK"); return true; }
        if (!LOCAL_IMPORT_SLOT.tryAcquire()) {
            message("上一个文件读取尚未结束。请稍后重试，或使用在线检查。");
            return true;
        }
        begin("正在校验本地 APK", false);
        LocalImport task = new LocalImport(generation);
        synchronized (transferCommitLock) { localImport = task; }
        startLocalThread("kira-apk-local", () -> importLocal(selected, task));
        return true;
    }

    private void importLocal(Uri selected, LocalImport task) {
        File partial = new File(activity.getFilesDir(), "host-updates/host-local.part");
        try {
            task.checkpoint();
            File folder = partial.getParentFile();
            if (!folder.isDirectory() && !folder.mkdirs()) throw new IOException("无法创建更新目录");
            if (Files.isSymbolicLink(partial.toPath())) throw new IOException("更新暂存文件无效");
            try (AssetFileDescriptor descriptor = activity.getContentResolver().openAssetFileDescriptor(selected, "r", task.signal)) {
                if (descriptor == null) throw new IOException("无法读取文件");
                try (InputStream input = descriptor.createInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    task.input = input;
                    byte[] buffer = new byte[65536];
                    long size = 0;
                    while (true) {
                        task.checkpoint();
                        int count = input.read(buffer);
                        task.checkpoint();
                        if (count == -1) break;
                        size += count;
                        if (size > MAX_APK) throw new IOException("APK 文件过大");
                        output.write(buffer, 0, count);
                    }
                    output.getFD().sync();
                } finally {
                    task.input = null;
                }
            }
            task.checkpoint();
            verifyIdentity(partial, -1);
            commitTransfer(partial, task.generation);
            completeTransfer(task.generation, null);
        } catch (Exception error) {
            partial.delete();
            completeTransfer(task.generation, "APK 校验失败。" + error.getMessage());
        } finally {
            synchronized (transferCommitLock) {
                task.workerDone = true;
                releaseLocalImport(task);
            }
        }
    }

    private static void startLocalThread(String name, Runnable action) {
        Thread thread = new Thread(action, name);
        thread.setDaemon(true);
        thread.start();
    }

    private void releaseLocalImport(LocalImport task) {
        if (localImport == task && task.workerDone && task.pendingAborts == 0) {
            localImport = null;
            LOCAL_IMPORT_SLOT.release();
        }
    }

    private void abortLocalImport(LocalImport task, String name, Runnable action) {
        startLocalThread(name, () -> {
            try { action.run(); }
            finally {
                synchronized (transferCommitLock) {
                    task.pendingAborts--;
                    releaseLocalImport(task);
                }
            }
        });
    }

    private void commitTransfer(File partial, int currentGeneration) throws IOException {
        File target = new File(partial.getParentFile(), "host-update.apk");
        // Serialize cancellation and publication. Verification stays outside this lock.
        synchronized (transferCommitLock) {
            if (currentGeneration != generation) throw new IOException("操作已取消");
            checkpoint();
            Files.move(partial.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        }
    }

    private void completeTransfer(int currentGeneration, String failure) {
        ui(() -> {
            if (currentGeneration != generation) return;
            end();
            // Cancellation after publication retains the verified APK and suppresses the prompt.
            if (cancelled) return;
            if (failure == null) offerInstall();
            else message(failure);
        });
    }

    private HttpURLConnection open(String value) throws Exception {
        URL url = new URL(https(value));
        for (int redirects = 0; redirects < 6; redirects++) {
            checkpoint();
            HttpURLConnection request = (HttpURLConnection) url.openConnection();
            connection = request;
            request.setConnectTimeout(15000);
            request.setReadTimeout(15000);
            request.setInstanceFollowRedirects(false);
            request.setRequestProperty("User-Agent", "Kira-Android-Linux-Host/" + BuildConfig.VERSION_NAME);
            request.setRequestProperty("Accept-Encoding", "identity");
            int code = request.getResponseCode();
            if (code >= 300 && code <= 399) {
                String location = request.getHeaderField("Location");
                request.disconnect();
                if (location == null) throw new IOException("更新地址重定向无效");
                url = new URL(https(new URL(url, location).toString()));
                continue;
            }
            if (code == 404) { request.disconnect(); throw new java.io.FileNotFoundException("更新清单尚未发布"); }
            if (code != 200) { request.disconnect(); throw new IOException("服务器返回 HTTP " + code); }
            return request;
        }
        throw new IOException("更新地址重定向过多");
    }

    private static String https(String value) throws Exception {
        Uri uri = Uri.parse(value);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null || uri.getFragment() != null)
            throw new IOException("需要无凭据的 HTTPS 地址");
        return value;
    }

    private static void validateMirror(String mirror) throws Exception {
        if (mirror.isEmpty()) throw new IOException("请填写国内镜像前缀");
        Uri uri = Uri.parse(https(mirror));
        if (!mirror.endsWith("/") || uri.getQuery() != null) throw new IOException("镜像前缀需以 / 结尾");
    }

    private static String mirrored(String source, String mirror) throws Exception {
        validateMirror(mirror);
        String host = Uri.parse(https(source)).getHost();
        if ("github.com".equals(host) || "raw.githubusercontent.com".equals(host) || "api.github.com".equals(host))
            return mirror + source;
        return source;
    }

    private void begin(String title, boolean measured) {
        synchronized (transferCommitLock) {
            generation++;
            busy = true;
            cancelled = false;
        }
        LinearLayout content = new LinearLayout(activity);
        content.setOrientation(LinearLayout.VERTICAL);
        int padding = Math.round(20 * activity.getResources().getDisplayMetrics().density);
        content.setPadding(padding, padding / 2, padding, padding);
        progressLabel = new TextView(activity);
        progressLabel.setText("正在连接国内镜像");
        progressLabel.setTextColor(ShellTheme.MUTED);
        progressLabel.setTextSize(13);
        content.addView(progressLabel);
        progressBar = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100); progressBar.setIndeterminate(!measured); ShellDialogs.styleProgress(progressBar);
        content.addView(progressBar);
        progress = ShellDialogs.builder(activity, ShellDialogs.Card.APK_UPDATE).setTitle(title).setView(content)
            .setNegativeButton("取消", (dialog, which) -> cancel()).create();
        progress.setOnCancelListener(dialog -> cancel());
        progress.show();
    }

    private void update(long done, long total, double speed) {
        ui(() -> {
            if (progress == null) return;
            progressBar.setProgress((int) (done * 100 / total));
            progressLabel.setText(RuntimeStatus.bytes(done) + " / " + RuntimeStatus.bytes(total) + " · " + RuntimeStatus.bytes(speed) + "/s");
        });
    }
    private void checkpoint() throws IOException { if (cancelled || destroyed) throw new IOException("操作已取消"); }
    private void cancel() {
        LocalImport task;
        synchronized (transferCommitLock) {
            cancelled = true;
            task = localImport;
            if (task != null && task.generation == generation) {
                generation++;
                task.cancelled = true;
                task.pendingAborts = 2;
            } else task = null;
        }
        if (task != null) {
            LocalImport cancelledImport = task;
            end();
            // A provider can ignore or block cancellation. Keep that work off the UI and online worker.
            abortLocalImport(cancelledImport, "kira-apk-provider-cancel", cancelledImport.signal::cancel);
            abortLocalImport(cancelledImport, "kira-apk-provider-close", () -> {
                InputStream input = cancelledImport.input;
                if (input != null) try { input.close(); } catch (IOException ignored) { }
            });
            return;
        }
        manifestFetch.cancel();
        HttpURLConnection request = connection;
        if (request != null) request.disconnect();
    }
    private void end() { busy = false; if (progress != null) { progress.dismiss(); progress = null; progressBar = null; progressLabel = null; } }
    private void message(String message) { if (!destroyed && !activity.isFinishing()) ShellDialogs.builder(activity, ShellDialogs.Card.APK_UPDATE).setTitle("APK 更新").setMessage(message).setPositiveButton("知道了", null).show(); }
    private void ui(Runnable action) { activity.runOnUiThread(() -> { if (!destroyed && !activity.isFinishing()) action.run(); }); }
    void destroy() {
        synchronized (transferCommitLock) { destroyed = true; }
        cancel();
        end();
        if (formDialog != null) formDialog.dismiss();
        manifestFetch.close();
        worker.shutdownNow();
    }
    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format(Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }
}
