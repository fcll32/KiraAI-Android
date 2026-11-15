package top.kira.host;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import org.json.JSONArray;
import org.json.JSONObject;
import com.chaquo.python.Python;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.function.BooleanSupplier;

final class RuntimeAssets {
    private static final long IMPORT_MARGIN = 32L * 1024 * 1024;
    static final class Prepared {
        final String manifest, abi;
        final File seed;
        Prepared(String manifest, String abi, File seed) {
            this.manifest = manifest;
            this.abi = abi;
            this.seed = seed;
        }
    }

    static Prepared prepare(Context context, BooleanSupplier cancelled) throws Exception {
        String manifest;
        try (InputStream input = context.getAssets().open("host-runtime.json");
             ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[16384];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (output.size() + count > 1024 * 1024) throw new IOException("运行环境清单过大");
                output.write(buffer, 0, count);
            }
            manifest = output.toString(StandardCharsets.UTF_8.name());
        }
        byte[] header = new byte[20];
        try (DataInputStream input = new DataInputStream(new FileInputStream(
                new File(context.getApplicationInfo().nativeLibraryDir, "libproot.so")))) {
            input.readFully(header);
        }
        int machine = (header[18] & 255) | ((header[19] & 255) << 8);
        if (header[0] != 127 || header[1] != 'E' || header[2] != 'L' || header[3] != 'F')
            throw new IOException("Linux 原生运行器格式无效");
        String abi = machine == 183 ? "arm64-v8a" : machine == 62 ? "x86_64" : "";
        if (abi.isEmpty()) throw new IOException("手机架构不受支持");
        JSONObject metadata = new JSONObject(manifest);
        JSONObject section = metadata.getJSONObject("abis").getJSONObject(abi);
        JSONObject seed = metadata.getJSONObject("officialSeed");
        JSONObject[] records = {section.getJSONObject("rootfsArtifact"), section.getJSONObject("nodeArtifact"), seed};
        JSONObject plan = new JSONObject(Python.getInstance().getModule("runtime_cache").callAttr("plan",
            context.getFilesDir().getAbsolutePath(), manifest, abi).toString());
        if (plan.optBoolean("repair_required")) {
            String detail = plan.optString("detail", "现有环境需修复，数据已保留");
            RuntimeStatus.write(context, "error", detail);
            throw new IOException(detail);
        }
        JSONArray needed = plan.getJSONArray("needed");
        java.util.Set<String> requested = new java.util.HashSet<>();
        for (int index = 0; index < needed.length(); index++) requested.add(needed.getString(index));
        File cache = new File(RuntimeStatus.directory(context), "downloads");
        if (Files.isSymbolicLink(cache.toPath())) throw new IOException("运行环境缓存目录不能是链接");
        if (!cache.isDirectory() && !cache.mkdirs()) throw new IOException("无法创建运行环境缓存目录");
        long total = 0;
        for (JSONObject record : records)
            if (requested.contains(record.getString("filename"))) total += record.getLong("size");
        long done = 0, started = android.os.SystemClock.elapsedRealtime();
        progress(context, done, total, started);
        for (JSONObject record : records) {
            if (!requested.contains(record.getString("filename"))) continue;
            String name = record.getString("filename"), expected = record.getString("sha256"), asset = record.getString("asset");
            long size = record.getLong("size");
            if (!name.matches("[A-Za-z0-9][A-Za-z0-9._-]{0,159}") || !expected.matches("[0-9a-f]{64}")
                    || size < 1 || size > 256L * 1024 * 1024
                    || !(asset.equals("linux-runtime/" + abi + "/" + name + ".payload")
                         || (name.equals("official-kira-seed.zip") && asset.equals(name))))
                throw new IOException("运行环境文件清单无效");
            File target = new File(cache, name);
            if (Files.isSymbolicLink(target.toPath())) throw new IOException("运行环境缓存不能是链接");
            if (target.isFile() && target.length() == size && expected.equals(checksum(target, cancelled))) {
                done += size;
                progress(context, done, total, started);
                continue;
            }
            File partial = new File(cache, name + ".bundled.part");
            if (Files.isSymbolicLink(partial.toPath())) throw new IOException("运行环境暂存不能是链接");
            if (cancelled.getAsBoolean()) throw new InterruptedIOException("已取消准备环境");
            long reclaimed = 0;
            if (partial.isFile()) {
                android.system.StructStat info = android.system.Os.stat(partial.getAbsolutePath());
                if (info.st_nlink == 1) reclaimed = Math.min(size, info.st_blocks * 512);
            }
            long required = size - reclaimed + IMPORT_MARGIN;
            if (cache.getUsableSpace() < required) {
                String detail = "可用存储不足，准备 APK 内置环境预计需 " + RuntimeStatus.bytes(required) + " 空闲空间";
                RuntimeStatus.write(context, "error", detail);
                throw new IOException(detail);
            }
            MessageDigest hash = MessageDigest.getInstance("SHA-256");
            long copied = 0, last = 0;
            try (InputStream input = context.getAssets().open(asset); FileOutputStream output = new FileOutputStream(partial)) {
                byte[] buffer = new byte[65536];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (cancelled.getAsBoolean()) throw new InterruptedIOException("已取消准备环境");
                    copied += count;
                    if (copied > size) throw new IOException("运行环境文件超过声明大小");
                    hash.update(buffer, 0, count);
                    output.write(buffer, 0, count);
                    long now = android.os.SystemClock.elapsedRealtime();
                    if (now - last >= 250) {
                        progress(context, done + copied, total, started);
                        last = now;
                    }
                }
                output.getFD().sync();
            } catch (Exception error) { partial.delete(); throw error; }
            if (copied != size || !expected.equals(hex(hash.digest()))) {
                partial.delete();
                throw new IOException("运行环境文件完整性校验失败");
            }
            Files.move(partial.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            done += copied;
            progress(context, done, total, started);
        }
        return new Prepared(manifest, abi, new File(cache, seed.getString("filename")));
    }

    private static void progress(Context context, long done, long total, long started) throws Exception {
        long elapsed = Math.max(1, android.os.SystemClock.elapsedRealtime() - started);
        JSONObject value = new JSONObject().put("state", "starting").put("pid", android.os.Process.myPid())
            .put("progress", 1).put("detail", "正在校验并准备 APK 内置环境")
            .put("downloaded_bytes", done).put("total_bytes", total).put("bytes_per_second", done * 1000d / elapsed);
        File folder = RuntimeStatus.directory(context);
        File temporary = new File(folder, "status.assets.tmp");
        Files.write(temporary.toPath(), value.toString().getBytes(StandardCharsets.UTF_8));
        Files.move(temporary.toPath(), new File(folder, "status.json").toPath(),
            StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    private static String checksum(File file, BooleanSupplier cancelled) throws Exception {
        MessageDigest hash = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (cancelled.getAsBoolean()) throw new InterruptedIOException("已取消准备环境");
                hash.update(buffer, 0, count);
            }
        }
        return hex(hash.digest());
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder();
        for (byte value : bytes) result.append(String.format(Locale.ROOT, "%02x", value & 255));
        return result.toString();
    }

    static String dns(Context context) {
        JSONArray result = new JSONArray();
        try {
            ConnectivityManager manager = context.getSystemService(ConnectivityManager.class);
            LinkProperties properties = manager.getLinkProperties(manager.getActiveNetwork());
            if (properties != null) for (InetAddress address : properties.getDnsServers()) result.put(address.getHostAddress());
        } catch (RuntimeException ignored) { }
        return result.toString();
    }
}
