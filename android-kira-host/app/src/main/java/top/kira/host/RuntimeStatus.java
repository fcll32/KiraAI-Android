package top.kira.host;

import android.app.ActivityManager;
import android.content.Context;
import android.os.SystemClock;
import android.provider.Settings;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.List;
import java.util.Locale;

final class RuntimeStatus {
    private static final long HEALTH_FRESH_MS = 30_000;
    private static volatile int knownBootCount = -1;

    static int bootCount(Context context) {
        int count = knownBootCount;
        if (count >= 0) return count;
        try { count = Settings.Global.getInt(context.getContentResolver(), Settings.Global.BOOT_COUNT, -1); }
        catch (RuntimeException unavailable) { return -1; }
        if (count >= 0) knownBootCount = count;
        return count;
    }
    static File directory(Context context) { return RuntimePaths.directory(context); }

    static boolean manuallyStopped(Context context) { return new File(directory(context), "manual-stop").isFile(); }

    static boolean requiresCleanup(Context context, JSONObject status) {
        if (hasUnsafeStop(status)) return true;
        JSONObject intent = readJson(new File(directory(context), "runtime-intent.json")).optJSONObject("components");
        if (intent == null) return false;
        for (String name : new String[]{"kira", "napcat"}) {
            JSONObject value = intent.optJSONObject(name);
            if (value != null && "cleanup_unconfirmed".equals(value.optString("blocked"))) return true;
        }
        return false;
    }

    static boolean automaticStartPaused(Context context) {
        return manuallyStopped(context) || Boolean.TRUE.equals(context.getSharedPreferences("linux_host", Context.MODE_PRIVATE)
            .getAll().get("manually_stopped"));
    }

    static boolean napcatInstalled(Context context, JSONObject value) {
        JSONObject napcat = component(value, "napcat");
        return napcat.has("installed") ? napcat.optBoolean("installed") : ComponentSelection.legacyNapcat(context, true);
    }

    static void setManuallyStopped(Context context, boolean stopped) {
        try {
            File folder = directory(context);
            if (!folder.isDirectory() && !folder.mkdirs()) return;
            File marker = new File(folder, "manual-stop");
            if (stopped) Files.write(marker.toPath(), new byte[]{1});
            else Files.deleteIfExists(marker.toPath());
        } catch (Exception failure) { android.util.Log.w("KiraHost", "Cannot save automatic start preference", failure); }
    }

    static void recordExplicitStop(Context context) {
        File temporary = null;
        try {
            File folder = directory(context);
            if (!folder.isDirectory() && !folder.mkdirs()) throw new java.io.IOException("Runtime directory unavailable");
            temporary = File.createTempFile(".manual-stop-", ".tmp", folder);
            try (FileOutputStream output = new FileOutputStream(temporary)) {
                output.write(1);
                output.getFD().sync();
            }
            Files.move(temporary.toPath(), new File(folder, "manual-stop-pending").toPath(),
                StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (java.io.IOException failure) {
            android.util.Log.w("KiraHost", "Cannot journal explicit stop", failure);
        } finally {
            if (temporary != null) temporary.delete();
        }
    }

    static JSONObject read(Context context) {
        JSONObject value = readJson(new File(directory(context), "status.json"));
        String state = value.optString("state", "stopped");
        boolean live = alive(context, value);
        for (String name : new String[]{"kira", "napcat"}) {
            JSONObject component = component(value, name);
            JSONObject health = component.optJSONObject("health");
            if (health != null && (!live || integer(health, "pid") != integer(value, "pid"))) component.remove("health");
        }
        if (!live) {
            try {
                if (!"error".equals(state) && !"stopped".equals(state))
                    value.put("state", "stopped").put("detail", "运行服务已结束，可重新启动组件");
                value.put("manager_ready", false);
                for (String name : new String[]{"kira", "napcat"}) {
                    JSONObject component = component(value, name);
                    component.put("running", false).put("installing", false);
                    if (!component.optBoolean("unsafe_stop") && active(component)) component.put("state", "stopped").put("pid", 0);
                }
            } catch (Exception ignored) { }
        }
        preserveUnsafeStop(value);
        return value;
    }

    static boolean alive(Context context) {
        return alive(context, readJson(new File(directory(context), "status.json")));
    }

    static boolean alive(Context context, JSONObject state) {
        ActivityManager manager = context.getSystemService(ActivityManager.class);
        List<ActivityManager.RunningAppProcessInfo> processes = manager.getRunningAppProcesses();
        if (processes != null) for (ActivityManager.RunningAppProcessInfo item : processes) {
            if ((context.getPackageName() + ":host_runtime").equals(item.processName)
                    && item.uid == android.os.Process.myUid()) {
                if (state.optInt("pid", -1) == item.pid) {
                    String phase = state.optString("state");
                    return (!"stopped".equals(phase) && !"error".equals(phase))
                        || active(component(state, "kira")) || active(component(state, "napcat"));
                }
            }
        }
        return false;
    }

    static JSONObject readJson(File file) {
        try {
            if (!file.isFile() || file.length() > 65536) return new JSONObject();
            return new JSONObject(new String(Files.readAllBytes(file.toPath()), StandardCharsets.UTF_8));
        } catch (Exception ignored) { return new JSONObject(); }
    }

    static synchronized void write(Context context, String state, String detail) {
        try {
            File folder = directory(context);
            if (!folder.isDirectory() && !folder.mkdirs()) return;
            JSONObject value = readJson(new File(folder, "status.json"));
            value.put("state", state).put("detail", detail)
                .put("pid", android.os.Process.myPid()).put("progress", JSONObject.NULL);
            if ("stopped".equals(state) || "error".equals(state)) {
                value.put("manager_ready", false).put("transfer_active", false);
                for (String name : new String[]{"kira", "napcat"}) {
                    JSONObject component = component(value, name);
                    component.remove("health");
                    component.put("running", false).put("installing", false);
                    if (!component.optBoolean("unsafe_stop") && active(component)) component.put("state", "stopped").put("pid", 0);
                }
            }
            preserveUnsafeStop(value);
            save(folder, value);
        } catch (Exception ignored) { }
    }

    static synchronized void starting(Context context, String selected) {
        try {
            File folder = directory(context);
            if (!folder.isDirectory() && !folder.mkdirs()) return;
            JSONObject value = readJson(new File(folder, "status.json"));
            if (hasUnsafeStop(value)) { preserveUnsafeStop(value); save(folder, value); return; }
            JSONArray initial = new JSONArray(selected);
            for (String name : new String[]{"kira", "napcat"}) {
                boolean wanted = false;
                for (int index = 0; index < initial.length(); index++) if (name.equals(initial.optString(index))) wanted = true;
                JSONObject component = component(value, name);
                component.remove("health");
                component.put("running", false).put("installing", false);
                component.put("state", wanted ? "starting" : "stopped").put("pid", 0).remove("error");
                component.put("detail", wanted ? "等待共享环境准备" : "尚未启动");
            }
            value.put("state", "starting").put("detail", "正在启动手机 Linux 环境")
                .put("pid", android.os.Process.myPid()).put("manager_ready", false)
                .put("progress", JSONObject.NULL).put("transfer_active", false);
            save(folder, value);
        } catch (Exception ignored) { }
    }

    private static void save(File folder, JSONObject value) throws Exception {
            File temporary = new File(folder, "status.java.tmp");
            Files.write(temporary.toPath(), value.toString().getBytes(StandardCharsets.UTF_8));
            Files.move(temporary.toPath(), new File(folder, "status.json").toPath(),
                java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE);
    }

    static JSONObject component(JSONObject value, String name) {
        JSONObject components = value.optJSONObject("components");
        try {
            if (components == null) { components = new JSONObject(); value.put("components", components); }
            JSONObject component = components.optJSONObject(name);
            if (component == null) {
                component = new JSONObject().put("state", "kira".equals(name) ? value.optString("state", "stopped") : "stopped");
                components.put(name, component);
            }
            return component;
        } catch (Exception ignored) { return new JSONObject(); }
    }

    static boolean active(JSONObject component) {
        String state = component.optString("state", "stopped");
        return !"stopped".equals(state) && !"error".equals(state);
    }

    private static long integer(JSONObject object, String key) {
        Object value = object.opt(key);
        return value instanceof Integer || value instanceof Long ? ((Number) value).longValue() : -1;
    }

    static String panelHealth(Context context, JSONObject component) {
        return panelHealthAt(component, bootCount(context), SystemClock.elapsedRealtime());
    }

    static String panelHealthAt(JSONObject component, int bootCount, long elapsedMs) {
        JSONObject health = component.optJSONObject("health");
        if (health == null || !"ready".equals(component.optString("state")) || component.optBoolean("unsafe_stop")
                || bootCount < 0 || integer(health, "boot_count") != bootCount || integer(health, "pid") <= 0) return "unknown";
        String owner = component.optString("run_id", "");
        if (owner.isEmpty() || !owner.equals(health.optString("run_id", ""))) return "unknown";
        long observed = integer(health, "observed_elapsed_ms");
        if (observed < 0 || observed > elapsedMs) return "unknown";
        String state = health.optString("state", "unknown");
        long success = integer(health, "last_success_elapsed_ms");
        if (success > observed) return "unknown";
        if (elapsedMs - observed >= HEALTH_FRESH_MS) return "expired";
        if ("unreachable".equals(state)) return state;
        if ("reachable".equals(state) && success >= 0) return elapsedMs - success >= HEALTH_FRESH_MS ? "expired" : state;
        if ("expired".equals(state)) return state;
        return "unknown";
    }

    static boolean panelAvailable(Context context, JSONObject component, boolean live) {
        if (!live || !"ready".equals(component.optString("state")) || component.optBoolean("unsafe_stop")) return false;
        String health = panelHealth(context, component);
        return !"unreachable".equals(health) && !"expired".equals(health);
    }

    static String panelHealthLabel(String state) {
        switch (state) {
            case "reachable": return "面板可达";
            case "unreachable": return "面板暂不可达";
            case "expired": return "面板检测已过期";
            default: return "面板状态待确认";
        }
    }

    static boolean preparing(JSONObject value) {
        for (String name : new String[]{"kira", "napcat"}) {
            String state = component(value, name).optString("state", "stopped");
            if (!"ready".equals(state) && !"running".equals(state) && !"stopped".equals(state) && !"error".equals(state)) return true;
        }
        return "starting".equals(value.optString("state"));
    }

    static boolean hasComponentError(JSONObject value) {
        return "error".equals(component(value, "kira").optString("state"))
            || "error".equals(component(value, "napcat").optString("state"));
    }

    /** 外壳状态与连接到哪一个面板无关；只有真实运行的组件才显示运行中。 */
    static String shellState(JSONObject value, boolean live) {
        if (!live || "stopping".equals(value.optString("state"))) return "未运行";
        if (preparing(value)) return "环境准备中";
        for (String name : new String[]{"kira", "napcat"}) {
            String phase = component(value, name).optString("state", "stopped");
            if ("ready".equals(phase) || "running".equals(phase)) return "运行中";
        }
        return "未运行";
    }

    static boolean hasUnsafeStop(JSONObject value) {
        return component(value, "kira").optBoolean("unsafe_stop") || component(value, "napcat").optBoolean("unsafe_stop");
    }

    private static void preserveUnsafeStop(JSONObject value) {
        if (!hasUnsafeStop(value)) return;
        try {
            value.put("state", "error").put("detail", "无法确认组件进程已停止，请打开设备详情重试清理或核验解除")
                .put("manager_ready", false).put("transfer_active", false);
            for (String name : new String[]{"kira", "napcat"}) {
                JSONObject component = component(value, name);
                if (component.optBoolean("unsafe_stop")) component.put("state", "error")
                    .put("detail", "无法确认进程已停止；已保留进程归属，禁止再次启动");
            }
        } catch (Exception ignored) { }
    }

    static String phase(String value) {
        switch (value) {
            case "ready": case "running": return "运行中";
            case "error": return "失败";
            case "stopped": return "已停止";
            case "stopping": return "正在停止";
            case "updating": return "正在更新";
            case "backup": case "backing_up": return "正在备份";
            default: return "正在准备";
        }
    }

    static String summary(JSONObject value) {
        return "KiraAI：" + phase(component(value, "kira").optString("state", "stopped"))
            + " · NapCat：" + napcatPhase(component(value, "napcat"));
    }

    static String napcatPhase(JSONObject component) {
        String state = component.optString("state", "stopped");
        if (component.optBoolean("installing")) return "安装中";
        if (component.optBoolean("install_failed")) return "安装失败";
        if ("stopped".equals(state) && !component.optBoolean("installed", true)) return "未安装";
        return phase(state);
    }

    static File componentLog(Context context, JSONObject value, String name) {
        File folder = directory(context);
        String path = "environment".equals(name) ? "linux-bootstrap.log" : component(value, name).optString("log", name + ".log");
        try {
            File file = path.startsWith("/") ? new File(path) : new File(path.startsWith("linux-runtime/") ? context.getFilesDir() : folder, path);
            File canonical = file.getCanonicalFile();
            if (canonical.getPath().startsWith(folder.getCanonicalPath() + File.separator)) return canonical;
        } catch (Exception ignored) { }
        return new File(folder, "environment".equals(name) ? "linux-bootstrap.log" : name + ".log");
    }

    static File napcatConfig(Context context, JSONObject value) {
        return RuntimePaths.napcatConfig(context, value);
    }

    static int percent(JSONObject value, String key) {
        double number = value.isNull(key) ? Double.NaN : value.optDouble(key, Double.NaN);
        return Double.isFinite(number) ? (int) Math.max(0, Math.min(100, number)) : -1;
    }

    static String title(JSONObject value) {
        if (hasUnsafeStop(value)) return "无法确认组件已停止";
        switch (value.optString("state", "stopped")) {
            case "ready": return "本机组件正在运行";
            case "error": return "环境启动失败";
            case "stopped": return "运行环境已停止";
            case "stopping": return "正在停止运行环境";
            default: return "正在准备本机运行环境";
        }
    }

    static String bytes(double number) {
        if (number >= 1073741824d) return String.format(Locale.ROOT, "%.2f GB", number / 1073741824d);
        if (number >= 1048576d) return String.format(Locale.ROOT, "%.1f MB", number / 1048576d);
        if (number >= 1024d) return String.format(Locale.ROOT, "%.1f KB", number / 1024d);
        return String.format(Locale.ROOT, "%.0f B", Math.max(0, number));
    }

    static final class Transfer {
        final boolean active;
        final String text;
        final int percent;
        Transfer(boolean active, String text, int percent) {
            this.active = active;
            this.text = text;
            this.percent = percent;
        }
        static Transfer from(JSONObject value) {
            if (!value.optBoolean("transfer_active") || value.optBoolean("transfer_complete"))
                return new Transfer(false, "", -1);
            String kind = value.optString("transfer_kind");
            if (!"download".equals(kind) && !"install".equals(kind) && !"resolve".equals(kind))
                return new Transfer(false, "", -1);
            String heading = "download".equals(kind) ? "当前下载" : "install".equals(kind) ? "当前安装" : "正在解析依赖";
            String label = value.optString("transfer_label").replaceAll("(?i)https?://\\S+", "[链接]");
            if (label.length() > 180) label = label.substring(0, 180);
            int progress = RuntimeStatus.percent(value, "transfer_percent");
            String text = heading + (label.isEmpty() ? "" : " · " + label);
            if ("download".equals(kind)) {
                long done = Math.max(0, value.optLong("transfer_done_bytes"));
                long total = Math.max(0, value.optLong("transfer_total_bytes"));
                if (total > 0) text += "\n" + bytes(done) + " / " + bytes(total);
                else if (done > 0) text += "\n已下载 " + bytes(done) + " · 总大小未知";
                double speed = value.optDouble("transfer_bytes_per_second", 0);
                if (Double.isFinite(speed) && speed > 0) text += " · " + bytes(speed) + "/s";
            }
            if (progress >= 0) text += " · " + progress + "%";
            return new Transfer(true, text, progress);
        }
    }
}
