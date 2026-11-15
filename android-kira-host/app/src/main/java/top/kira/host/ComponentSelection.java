package top.kira.host;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.JsonReader;
import android.util.JsonToken;
import org.json.JSONArray;
import java.io.IOException;
import java.io.StringReader;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

final class ComponentSelection {
    private static final String[] COMPONENTS = {"kira", "napcat"};

    static Set<String> load(Context context) {
        Path file = selectionPath(context);
        return Files.notExists(file, LinkOption.NOFOLLOW_LINKS) ? legacySelection(context) : read(file);
    }

    // Activity and foreground service live in separate Android processes.
    static synchronized Set<String> persist(Context context, String name, boolean enabled) throws IOException {
        Path file = selectionPath(context);
        Path directory = file.getParent();
        if (name == null && !Files.notExists(file, LinkOption.NOFOLLOW_LINKS)) return read(file);
        Files.createDirectories(directory);
        Path lock = directory.resolve("components.lock");
        try (FileChannel channel = FileChannel.open(lock, StandardOpenOption.CREATE, StandardOpenOption.WRITE);
             FileLock ignored = channel.lock()) {
            boolean absent = Files.notExists(file, LinkOption.NOFOLLOW_LINKS);
            Set<String> selected = absent ? legacySelection(context) : read(file);
            if (name == null && !absent) return selected;
            if (name != null) {
                if (enabled) selected.add(name);
                else selected.remove(name);
            }
            Path temporary = Files.createTempFile(directory, "components-", ".tmp");
            try {
                Files.write(temporary, json(selected).getBytes(StandardCharsets.UTF_8));
                Files.move(temporary, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } finally {
                Files.deleteIfExists(temporary);
            }
            return selected;
        }
    }

    static String json(Set<String> selected) {
        JSONArray array = new JSONArray();
        for (String name : COMPONENTS) if (selected.contains(name)) array.put(name);
        return array.toString();
    }

    private static Path selectionPath(Context context) {
        return RuntimeStatus.directory(context).toPath().resolve("components.json");
    }

    private static Set<String> read(Path file) {
        Set<String> selected = new LinkedHashSet<>();
        try {
            if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || Files.size(file) > 1024) return selected;
            try (JsonReader entries = new JsonReader(new StringReader(new String(Files.readAllBytes(file), StandardCharsets.UTF_8)))) {
                entries.beginArray();
                while (entries.hasNext()) {
                    if (entries.peek() != JsonToken.STRING) return new LinkedHashSet<>();
                    String entry = entries.nextString();
                    if (!("kira".equals(entry) || "napcat".equals(entry))) return new LinkedHashSet<>();
                    selected.add(entry);
                }
                entries.endArray();
                if (entries.peek() != JsonToken.END_DOCUMENT) selected.clear();
            }
        } catch (Exception ignored) {
            selected.clear();
        }
        return selected;
    }

    private static Set<String> legacySelection(Context context) {
        SharedPreferences legacy = context.getSharedPreferences("linux_host", Context.MODE_PRIVATE);
        Map<String, ?> preferences = legacy.getAll();
        Set<String> result = new LinkedHashSet<>();
        if (!Boolean.FALSE.equals(preferences.get("component_kira_enabled"))) result.add("kira");
        Object savedNapcat = preferences.get("component_napcat_enabled");
        boolean napcat = savedNapcat instanceof Boolean ? (Boolean) savedNapcat : legacyNapcat(context, false);
        if (napcat) result.add("napcat");
        return result;
    }

    static boolean legacyNapcat(Context context, boolean installed) {
        return RuntimePaths.legacyNapcat(context, installed);
    }
}
