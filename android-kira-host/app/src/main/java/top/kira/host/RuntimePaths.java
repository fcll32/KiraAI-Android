package top.kira.host;

import android.content.Context;
import org.json.JSONObject;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** Resolves Android readers against the same private base binding as the Linux host. */
final class RuntimePaths {
    private static final String RUNTIME = "linux-runtime";
    private static final String LEGACY = "kira-linux";
    private static final String ROOT_NAME = "rootfs-[0-9a-f]{16}";
    private static final String ROOT_LOCATION = "(" + RUNTIME + "|" + LEGACY + ")/" + ROOT_NAME;
    private static final String NAPCAT_PROGRAM = "root/Napcat";
    private static final String QQ_PROGRAM = NAPCAT_PROGRAM + "/opt/QQ";
    private static final String NAPCAT_CODE = QQ_PROGRAM + "/resources/app/app_launcher/napcat";
    private static final String NAPCAT_CONFIG = NAPCAT_CODE + "/config/webui.json";

    static File directory(Context context) { return new File(context.getFilesDir(), RUNTIME); }

    static File privateChild(File directory, String relative) {
        if (relative.isEmpty() || relative.length() > 512 || relative.indexOf('\\') >= 0 || relative.indexOf(':') >= 0) return null;
        for (String segment : relative.split("/", -1))
            if (segment.isEmpty() || ".".equals(segment) || "..".equals(segment)) return null;
        try {
            File child = new File(directory, relative);
            if (!child.getCanonicalPath().startsWith(directory.getCanonicalPath() + File.separator)) return null;
            return child;
        } catch (IOException ignored) { return null; }
    }

    private static String verifiedIdentity(File root) {
        File marker = new File(root, ".kira-rootfs.json");
        if (!root.getName().matches(ROOT_NAME) || !Files.isDirectory(root.toPath(), LinkOption.NOFOLLOW_LINKS)
                || !Files.isDirectory(root.getParentFile().toPath(), LinkOption.NOFOLLOW_LINKS)
                || !Files.isRegularFile(marker.toPath(), LinkOption.NOFOLLOW_LINKS)) return null;
        String checksum = RuntimeStatus.readJson(marker).optString("sha256");
        return checksum.matches("[0-9a-f]{64}") && root.getName().equals("rootfs-" + checksum.substring(0, 16)) ? checksum : null;
    }

    static List<File> readableRoots(Context context) {
        File files = context.getFilesDir();
        File binding = new File(directory(context), "base-binding.json");
        if (!Files.notExists(binding.toPath(), LinkOption.NOFOLLOW_LINKS)) {
            if (!Files.isRegularFile(binding.toPath(), LinkOption.NOFOLLOW_LINKS)) return Collections.emptyList();
            JSONObject current = RuntimeStatus.readJson(binding);
            String relative = current.optString("root");
            if (current.optInt("version") != 1 || !relative.matches(ROOT_LOCATION)) return Collections.emptyList();
            File root = privateChild(files, relative);
            if (root == null || !current.optString("sha256").equals(verifiedIdentity(root))) return Collections.emptyList();
            return Collections.singletonList(root);
        }
        List<File> verified = new ArrayList<>();
        for (String location : new String[]{RUNTIME, LEGACY}) {
            File parent = privateChild(files, location);
            if (parent == null || !Files.isDirectory(parent.toPath(), LinkOption.NOFOLLOW_LINKS)) continue;
            File[] roots = parent.listFiles((dir, name) -> name.matches(ROOT_NAME));
            if (roots != null) for (File root : roots) if (verifiedIdentity(root) != null) verified.add(root);
        }
        return verified;
    }

    static boolean legacyNapcat(Context context, boolean installed) {
        for (File root : readableRoots(context)) {
            if (!installed && (present(root, NAPCAT_PROGRAM, false) || present(root, "root/.config/QQ", false))) return true;
            if (installed && Files.notExists(new File(root, ".napcat-host-pending.json").toPath(), LinkOption.NOFOLLOW_LINKS)
                    && present(root, QQ_PROGRAM + "/qq", true)
                    && present(root, QQ_PROGRAM + "/resources/app/loadNapCat.js", true)
                    && present(root, NAPCAT_CODE + "/napcat.mjs", true)) return true;
        }
        return false;
    }

    static File napcatConfig(Context context, JSONObject state) {
        String relative = state.optString("rootfs");
        if (!relative.matches(ROOT_LOCATION)) return null;
        File root = privateChild(context.getFilesDir(), relative);
        if (root == null || !readableRoots(context).contains(root)) return null;
        JSONObject paths = state.optJSONObject("paths");
        String expected = relative + "/" + NAPCAT_CONFIG;
        if (paths != null && !expected.equals(paths.optString("napcat_config"))) return null;
        if (paths == null && !NAPCAT_CONFIG.equals(RuntimeStatus.component(state, "napcat").optString("config_path"))) return null;
        return privateChild(root, NAPCAT_CONFIG);
    }

    private static boolean present(File root, String relative, boolean regular) {
        File file = privateChild(root, relative);
        if (file == null) return false;
        return regular ? Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS) && file.length() > 0
            : Files.isDirectory(file.toPath(), LinkOption.NOFOLLOW_LINKS);
    }
}
