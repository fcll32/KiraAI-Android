package top.kira.host;

import android.content.Context;
import org.json.JSONObject;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.util.Iterator;
import java.util.Map;
import java.util.TreeMap;

/** Private guest-process settings. Values are data, never shell source. */
final class RuntimeEnvironment {
    private static final int MAX_BYTES = 65536;
    static final String DEFAULT_TEXT = "KIRA_TELEMETRY_ENABLED=0";

    private static File file(Context context) {
        return new File(RuntimeStatus.directory(context), "environment.json");
    }

    private static void validate(String key, String value) {
        if (!key.matches("[A-Za-z_][A-Za-z0-9_]{0,127}"))
            throw new IllegalArgumentException("变量名须以字母或下划线开头，只含字母、数字和下划线，最多128字符。");
        if (key.equals("KIRA_LINUX_RUN_ID") || key.equals("KIRA_LINUX_HOST_PID") || key.equals("KIRA_NODE_AVAILABLE"))
            throw new IllegalArgumentException("该变量由宿主管理，不能自定义。");
        if (value.indexOf('\0') >= 0 || value.indexOf('\r') >= 0 || value.indexOf('\n') >= 0)
            throw new IllegalArgumentException("每个变量值须放在同一行，不能含空字符。");
    }

    static String load(Context context) throws Exception {
        File source = file(context);
        if (!source.exists()) return DEFAULT_TEXT;
        if (Files.isSymbolicLink(source.toPath()) || !source.isFile() || source.length() > MAX_BYTES)
            throw new IllegalArgumentException("已保存的环境变量文件无效或超过64KiB。");
        byte[] bytes = Files.readAllBytes(source.toPath());
        if (bytes.length > MAX_BYTES) throw new IllegalArgumentException("环境变量合计不能超过64KiB。");
        String encoded = StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString();
        JSONObject object = new JSONObject(encoded);
        if (object.length() > 128) throw new IllegalArgumentException("最多保存128项环境变量。");
        TreeMap<String, String> values = new TreeMap<>();
        for (Iterator<String> keys = object.keys(); keys.hasNext();) {
            String key = keys.next();
            Object value = object.get(key);
            if (!(value instanceof String)) throw new IllegalArgumentException("环境变量的值必须为文字。");
            validate(key, (String) value);
            values.put(key, (String) value);
        }
        StringBuilder text = new StringBuilder();
        for (Map.Entry<String, String> item : values.entrySet()) {
            if (text.length() > 0) text.append('\n');
            text.append(item.getKey()).append('=').append(item.getValue());
        }
        return text.toString();
    }

    static void save(Context context, String text) throws Exception {
        JSONObject object = new JSONObject();
        String[] lines = text.split("\\r?\\n", -1);
        for (int index = 0; index < lines.length; index++) {
            String line = lines[index];
            if (line.trim().isEmpty() || line.trim().startsWith("#")) continue;
            int equals = line.indexOf('=');
            if (equals < 1) throw new IllegalArgumentException("第" + (index + 1) + "行请使用 NAME=VALUE 格式。");
            String key = line.substring(0, equals).trim();
            String value = line.substring(equals + 1);
            try { validate(key, value); }
            catch (IllegalArgumentException failure) {
                throw new IllegalArgumentException("第" + (index + 1) + "行：" + failure.getMessage());
            }
            if (object.has(key)) throw new IllegalArgumentException("第" + (index + 1) + "行的变量名重复。");
            object.put(key, value);
            if (object.length() > 128) throw new IllegalArgumentException("最多保存128项环境变量。");
        }
        byte[] bytes = (object.toString(2) + "\n").getBytes(StandardCharsets.UTF_8);
        if (bytes.length > MAX_BYTES) throw new IllegalArgumentException("环境变量合计不能超过64KiB。");
        File destination = file(context);
        File directory = destination.getParentFile();
        if (!directory.isDirectory() && !directory.mkdirs()) throw new java.io.IOException("无法保存环境变量。");
        File temporary = new File(directory, "environment.json.tmp");
        try {
            Files.write(temporary.toPath(), bytes);
            Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally { Files.deleteIfExists(temporary.toPath()); }
    }
}
