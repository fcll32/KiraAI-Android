package top.kira.host;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.AssetFileDescriptor;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.SystemClock;
import android.os.CancellationSignal;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.File;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.text.DateFormat;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/** Menu-only backup inventory, explicit export/delete/restore, and the existing host backup action. */
final class KiraBackupManager {
    private static final int EXPORT = 46;
    private final Activity activity;
    private final Runnable create, restore;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private volatile boolean destroyed, busy;
    private int generation;
    private int listingRevision;
    private Future<?> listing;
    private AlertDialog dialog, progress;
    private Entry pendingExport;
    private BackupExportTask exportTask;

    private static final class Entry {
        File file;
        long size, modified, created;
        boolean complete;
        String sha = "";
    }

    KiraBackupManager(Activity activity, Runnable create, Runnable restore) {
        this.activity = activity; this.create = create; this.restore = restore;
    }
    private File folder() { return new File(RuntimeStatus.directory(activity), "backups"); }
    private int dp(int value) { return Math.round(value * activity.getResources().getDisplayMetrics().density); }

    void show() {
        if (destroyed) return;
        if (busy) { message("正在处理备份，请稍候"); return; }
        if (dialog != null && dialog.isShowing()) return;
        LinearLayout content = ShellCards.column(activity, 20);
        TextView description = ShellCards.text(activity,
            "保存程序、配置、插件与聊天，随时导出到自己的存储位置。", 13, ShellTheme.MUTED);
        content.addView(description,new LinearLayout.LayoutParams(-1,-2));
        Button add = new Button(activity);
        add.setText("新建备份");
        ShellDialogs.tintButton(add, ShellTheme.ACCENT, true);
        LinearLayout.LayoutParams addParams = new LinearLayout.LayoutParams(-1, dp(48));
        addParams.topMargin = dp(13); addParams.bottomMargin = dp(12);
        content.addView(add, addParams);
        LinearLayout list = new LinearLayout(activity);
        list.setOrientation(LinearLayout.VERTICAL);
        content.addView(list, new LinearLayout.LayoutParams(-1, -2));
        dialog = ShellDialogs.builder(activity, ShellDialogs.Card.ARCHIVE).setTitle("KiraAI 备份管理")
            .setView(ShellDialogs.scroll(activity, content, .42f)).setPositiveButton("关闭", null)
            .setNeutralButton("刷新", null).create();
        int current = ++generation;
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_NEUTRAL)
            .setOnClickListener(view -> load(list, current)));
        dialog.setOnDismissListener(ignored -> {
            if (generation == current) {
                generation++;
                cancelListing();
                dialog = null;
            }
        });
        add.setOnClickListener(view -> { if (dialog != null) dialog.dismiss(); create.run(); });
        dialog.show();
        load(list, current);
    }

    private TextView text(String value, int size) {
        TextView text = new TextView(activity);
        text.setText(value); text.setTextSize(size); text.setTextColor(ShellTheme.INK);
        text.setPadding(0, dp(5), 0, dp(7));
        return text;
    }

    private void load(LinearLayout list, int current) {
        cancelListing();
        int revision = listingRevision;
        list.removeAllViews(); list.addView(text("正在读取并校验备份…", 13));
        listing = worker.submit(() -> {
            try {
                List<Entry> entries = entries();
                ui(() -> {
                    if (generation != current || listingRevision != revision || dialog == null) return;
                    list.removeAllViews();
                    long complete = entries.stream().filter(item -> item.complete).count();
                    inventory(list, complete, entries.size());
                    if (entries.isEmpty()) {
                        TextView empty = text("还没有备份\n新建一份，保存当前的 KiraAI。", 14);
                        empty.setGravity(Gravity.CENTER);
                        empty.setPadding(dp(16), dp(18), dp(16), dp(18));
                        list.addView(empty, new LinearLayout.LayoutParams(-1, -2));
                    }
                    for (Entry entry : entries) addEntry(list, entry, current);
                });
            } catch (Exception failure) {
                ui(() -> {
                    if (generation == current && listingRevision == revision) {
                        list.removeAllViews();
                        list.addView(text("备份目录暂时无法读取，请稍后刷新。", 13));
                    }
                });
            }
        });
    }

    private void cancelListing() {
        listingRevision++;
        if (listing != null) listing.cancel(true);
        listing = null;
    }

    private void inventory(LinearLayout list, long complete, int total) {
        LinearLayout summary = new LinearLayout(activity);
        summary.setGravity(Gravity.CENTER_VERTICAL);
        summary.setPadding(dp(16), dp(12), dp(16), dp(12));
        summary.setBackground(folderSurface(24, ShellTheme.SURFACE, ShellTheme.ROSE_SOFT));
        TextView count = text(complete + " / 2", 25);
        count.setTextColor(ShellTheme.ACCENT);
        count.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        summary.addView(count, new LinearLayout.LayoutParams(0, -2, 1));
        TextView caption = text("已完成备份\n共 " + total + " 份档案", 12);
        caption.setGravity(Gravity.END);
        caption.setTextColor(ShellTheme.MUTED);
        summary.addView(caption);
        list.addView(summary, new LinearLayout.LayoutParams(-1, -2));
        TextView policy = text("完整备份最多保留 2 份，新备份核验成功后替换最旧一份；中断和未核验文件保留。", 11);
        policy.setTextColor(ShellTheme.MUTED);
        policy.setPadding(dp(2), dp(10), dp(2), dp(10));
        list.addView(policy);
    }

    private GradientDrawable folderSurface(int radius, int top, int bottom) {
        GradientDrawable ticket=ShellTheme.gradient(activity,0xFF000000 | (top & 0x00FFFFFF),
            0xFF000000 | (bottom & 0x00FFFFFF),radius,ShellTheme.LINE);
        return ticket;
    }

    private void addEntry(LinearLayout parent, Entry entry, int current) {
        LinearLayout row = new LinearLayout(activity);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(dp(16), dp(14), dp(16), dp(12));
        row.setBackground(folderSurface(20, ShellTheme.SURFACE, ShellTheme.PEARL_END));
        LinearLayout top = new LinearLayout(activity);
        top.setGravity(Gravity.CENTER_VERTICAL);
        TextView kind = text("KiraAI 备份", 11);
        kind.setTypeface(Typeface.MONOSPACE);
        kind.setTextColor(ShellTheme.MUTED);
        top.addView(kind, new LinearLayout.LayoutParams(0, -2, 1));
        TextView size = text(RuntimeStatus.bytes(entry.size), 12);
        size.setTextColor(ShellTheme.ACCENT);
        top.addView(size);
        row.addView(top, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout facts = new LinearLayout(activity);
        facts.setOrientation(LinearLayout.VERTICAL);
        TextView date = text(DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(new Date(entry.created)), 17);
        date.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        facts.addView(date);
        TextView name = text(entry.file.getName(), 11); name.setTextColor(ShellTheme.MUTED); facts.addView(name);
        row.addView(facts);
        ShellCards.badge(row, entry.complete ? "已校验 · 可导出" : "待核验 · 请保留原文件", entry.complete ? ShellTheme.TEAL : ShellTheme.ACCENT);
        View divider = new View(activity);
        divider.setBackgroundColor(ShellTheme.LINE);
        LinearLayout.LayoutParams dividerParams = new LinearLayout.LayoutParams(-1, dp(1));
        dividerParams.bottomMargin = dp(8);
        row.addView(divider, dividerParams);
        LinearLayout actions = new LinearLayout(activity);
        Button recover = new Button(activity); recover.setText("恢复"); ShellDialogs.tintButton(recover, ShellTheme.ACCENT, false);
        Button export = new Button(activity); export.setText("导出档案"); ShellDialogs.tintButton(export, ShellTheme.ACCENT, false);
        Button delete = new Button(activity); delete.setText("删除"); ShellDialogs.tintButton(delete, ShellTheme.ACCENT, false);
        // 只有校验通过的备份可以恢复；待核验的档案仍可导出或删除。
        recover.setEnabled(entry.complete);
        actions.addView(recover, new LinearLayout.LayoutParams(0, dp(48), 1));
        LinearLayout.LayoutParams exportParams = new LinearLayout.LayoutParams(0, dp(48), 1.3f); exportParams.leftMargin = dp(8);
        actions.addView(export, exportParams);
        LinearLayout.LayoutParams deleteParams = new LinearLayout.LayoutParams(0, dp(48), 1); deleteParams.leftMargin = dp(8);
        actions.addView(delete, deleteParams); row.addView(actions);
        recover.setOnClickListener(view -> ShellDialogs.builder(activity, ShellDialogs.Card.ARCHIVE).setTitle("恢复这份备份？")
            .setMessage(entry.file.getName() + "\n当前的程序、配置、插件和聊天会被这份备份替换。恢复期间 KiraAI 会暂时停止，完成后回到此前的运行状态。"
                + "\n被替换的当前内容会在应用内保留一份；上一次恢复留下的那份会被清除。")
            .setNegativeButton("取消", null).setPositiveButton("恢复", (confirm, which) -> requestRestore(entry)).show());
        export.setOnClickListener(view -> export(entry));
        delete.setOnClickListener(view -> ShellDialogs.builder(activity, ShellDialogs.Card.ARCHIVE).setTitle("删除这份备份？")
            .setMessage(entry.file.getName() + "\n该备份将从应用私有目录删除。")
            .setNegativeButton("取消", null).setPositiveButton("删除", (confirm, which) -> {
                worker.execute(() -> {
                    try {
                        check(entry);
                        File metadata = new File(folder(), entry.file.getName() + ".json");
                        if (metadata.exists() && (!metadata.isFile() || Files.isSymbolicLink(metadata.toPath())))
                            throw new IOException("备份记录已变化，请刷新后重试");
                        Files.delete(entry.file.toPath());
                        Files.deleteIfExists(metadata.toPath());
                        ui(() -> { message("备份已删除"); if (generation == current) load(parent, current); });
                    } catch (Exception failure) { ui(() -> message("删除失败，请刷新后重试")); }
                });
            }).show());
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(-1, -2);
        rowParams.topMargin = dp(10);
        parent.addView(row, rowParams);
    }

    /** Hands the confirmed archive to the host runtime: one private request file authorises one restore. */
    private void requestRestore(Entry entry) {
        if (busy) { message("正在处理备份，请稍候"); return; }
        worker.execute(() -> {
            try {
                check(entry);
                if (!entry.complete || !entry.sha.matches("[0-9a-f]{64}")) throw new IOException("Backup is not verified");
                File directory = RuntimeStatus.directory(activity);
                File request = new File(directory, "restore-request.json");
                File temporary = new File(directory, "restore-request.json.tmp");
                if (Files.isSymbolicLink(directory.toPath()) || Files.isSymbolicLink(request.toPath())
                    || Files.isSymbolicLink(temporary.toPath())) throw new IOException("Invalid request location");
                String body = new JSONObject().put("schema", "kira-restore-request/v1")
                    .put("filename", entry.file.getName()).put("sha256", entry.sha).toString();
                Files.write(temporary.toPath(), body.getBytes(StandardCharsets.UTF_8));
                Files.move(temporary.toPath(), request.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                ui(() -> { if (dialog != null) dialog.dismiss(); restore.run(); });
            } catch (Exception failure) { ui(() -> message("无法提交恢复，请刷新后重试")); }
        });
    }

    private List<Entry> entries() throws Exception {
        List<Entry> entries = new ArrayList<>();
        File directory = folder();
        if (!directory.exists()) return entries;
        if (Files.isSymbolicLink(directory.toPath()) || !directory.isDirectory()) throw new IOException("Invalid backup directory");
        File[] files = directory.listFiles();
        if (files == null) throw new IOException("Cannot list backups");
        for (File file : files) {
            if (destroyed || Thread.currentThread().isInterrupted()) throw new IOException("Closed");
            if (!file.getName().matches("kira-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\\.tar")
                || !Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS)) continue;
            Entry entry = new Entry(); entry.file = file; entry.size = file.length(); entry.modified = file.lastModified(); entry.created = entry.modified;
            File metadata = new File(directory, file.getName() + ".json");
            if (!Files.isSymbolicLink(metadata.toPath()) && metadata.isFile() && metadata.length() <= 8192) {
                try {
                    JSONObject record = new JSONObject(new String(Files.readAllBytes(metadata.toPath()), StandardCharsets.UTF_8));
                    String expected = record.optString("sha256");
                    if ("kira-backup/v1".equals(record.optString("schema")) && "complete".equals(record.optString("state"))
                        && file.getName().equals(record.optString("filename")) && entry.size == record.optLong("size", -1)
                        && entry.modified == record.optLong("archive_mtime_ns", -1) / 1000000L && expected.matches("[0-9a-f]{64}")) {
                        entry.complete = expected.equals(hash(entry));
                        if (entry.complete) { entry.sha = expected; entry.created = record.optLong("created_at_unix_ms", entry.modified); }
                    }
                } catch (Exception invalid) {
                    if (destroyed || Thread.currentThread().isInterrupted()) throw invalid;
                    entry.complete = false;
                }
            }
            entries.add(entry);
        }
        entries.sort(Comparator.comparingLong((Entry entry) -> entry.created).reversed());
        return entries;
    }

    private void check(Entry entry) throws IOException {
        File directory = folder();
        if (Files.isSymbolicLink(directory.toPath()) || !directory.isDirectory()
            || !entry.file.getCanonicalFile().getParentFile().equals(directory.getCanonicalFile())
            || !Files.isRegularFile(entry.file.toPath(), LinkOption.NOFOLLOW_LINKS)
            || entry.file.length() != entry.size || entry.file.lastModified() != entry.modified)
            throw new IOException("备份已变化，请刷新后重试");
    }

    private String hash(Entry entry) throws Exception {
        check(entry);
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = Files.newInputStream(entry.file.toPath(), StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
            byte[] buffer = new byte[128 * 1024]; int read;
            while ((read = input.read(buffer)) != -1) {
                if (destroyed || Thread.currentThread().isInterrupted()) throw new IOException("Closed");
                digest.update(buffer, 0, read);
            }
        }
        check(entry);
        return hex(digest.digest());
    }

    private void export(Entry entry) {
        if (busy || pendingExport != null) { message("请先完成当前导出"); return; }
        if (!BackupExportTask.available()) { message("上次导出仍在释放保存位置，请稍后重试"); return; }
        pendingExport = entry;
        try {
            activity.startActivityForResult(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                .setType("application/x-tar").putExtra(Intent.EXTRA_TITLE, entry.file.getName()), EXPORT);
        } catch (RuntimeException failure) { pendingExport = null; message("无法打开保存位置选择器"); }
    }

    boolean result(int request, int result, Intent data) {
        if (request != EXPORT) return false;
        Entry entry = pendingExport; pendingExport = null;
        if (destroyed || entry == null || result != Activity.RESULT_OK || data == null || data.getData() == null) return true;
        Uri uri = data.getData();
        if (!"content".equals(uri.getScheme())) { message("请选择系统文件保存位置"); return true; }
        CancellationSignal signal = new CancellationSignal();
        BackupExportTask task = new BackupExportTask(signal::cancel);
        exportTask = task;
        busy = true;
        LinearLayout content = new LinearLayout(activity); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(dp(20), dp(12), dp(20), dp(12));
        TextView label = text("正在导出备份…", 13); content.addView(label);
        ProgressBar bar = new ProgressBar(activity, null, android.R.attr.progressBarStyleHorizontal); bar.setMax(100); ShellDialogs.styleProgress(bar); content.addView(bar);
        progress = ShellDialogs.builder(activity, ShellDialogs.Card.ARCHIVE).setTitle("导出 KiraAI 备份").setView(content)
            .setNegativeButton("取消", (dialog, which) -> cancelExport(task)).create();
        progress.setOnCancelListener(dialog -> cancelExport(task)); progress.show();
        boolean started = task.start(() -> {
            try {
                task.checkpoint();
                check(entry);
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                try (InputStream input = Files.newInputStream(entry.file.toPath(), StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
                     AssetFileDescriptor descriptor = activity.getContentResolver().openAssetFileDescriptor(uri, "wt", signal)) {
                    if (descriptor == null) throw new IOException("Cannot open document");
                    task.checkpoint();
                    try (OutputStream output = descriptor.createOutputStream()) {
                        task.attach(output);
                        long done = 0, last = 0; byte[] bytes = new byte[128 * 1024]; int read;
                        while (true) {
                            task.checkpoint();
                            read = input.read(bytes);
                            task.checkpoint();
                            if (read == -1) break;
                            output.write(bytes, 0, read);
                            task.checkpoint();
                            digest.update(bytes, 0, read); done += read;
                            if (SystemClock.elapsedRealtime() - last > 150) {
                                final long count = done; last = SystemClock.elapsedRealtime();
                                ui(() -> {
                                    if (exportTask != task) return;
                                    bar.setProgress((int) (count * 100 / Math.max(1, entry.size)));
                                    label.setText(RuntimeStatus.bytes(count) + " / " + RuntimeStatus.bytes(entry.size));
                                });
                            }
                        }
                        check(entry);
                        if (done != entry.size || (entry.complete && !entry.sha.equals(hex(digest.digest())))) throw new IOException("Backup changed during export");
                        output.flush();
                        task.checkpoint();
                    }
                }
                task.commit();
            } catch (Exception failure) {
                try { android.provider.DocumentsContract.deleteDocument(activity.getContentResolver(), uri); } catch (Exception ignored) { }
            }
        }, () -> ui(() -> {
            if (exportTask != task) return;
            finishExport();
            message(task.committed() ? "备份已导出" : "备份未完成导出，目标位置可能有残留文件");
        }));
        if (!started) {
            finishExport();
            message("上次导出仍在释放保存位置，请稍后重试；刚选的位置可能留下空文件。");
        }
        return true;
    }

    private void cancelExport(BackupExportTask task) {
        if (exportTask != task || !task.cancel()) return;
        finishExport();
        if (!destroyed) message("已请求取消导出，保存位置释放后可重试；目标位置可能有未完成文件。");
    }

    private void finishExport() {
        exportTask = null;
        busy = false;
        if (progress != null) { progress.dismiss(); progress = null; }
    }

    void destroy() {
        destroyed = true;
        BackupExportTask task = exportTask;
        if (task != null) task.cancel();
        finishExport();
        generation++;
        cancelListing();
        if (dialog != null) dialog.dismiss();
        if (progress != null) progress.dismiss();
        worker.shutdownNow();
    }
    private void ui(Runnable action) { activity.runOnUiThread(() -> { if (!destroyed && !activity.isFinishing()) action.run(); }); }
    private void message(String value) { Toast.makeText(activity, value, Toast.LENGTH_SHORT).show(); }
    private static String hex(byte[] data) { StringBuilder value = new StringBuilder(); for (byte part : data) value.append(String.format(Locale.ROOT, "%02x", part & 255)); return value.toString(); }
}
