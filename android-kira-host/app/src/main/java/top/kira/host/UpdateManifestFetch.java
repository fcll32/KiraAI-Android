package top.kira.host;

import java.io.ByteArrayOutputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/** A check has one total deadline; a fast HTTP error cannot beat another valid source. */
final class UpdateManifestFetch implements AutoCloseable {
    interface Validator<T> { T validate(byte[] bytes) throws Exception; }
    interface Reader { byte[] read(String address, Ticket ticket) throws Exception; }
    static final class Ticket {
        private final long deadline;
        private volatile boolean cancelled;
        private final Set<HttpURLConnection> connections = Collections.synchronizedSet(new HashSet<>());
        Ticket(long timeoutMillis) { deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis); }
        long remainingMillis() throws InterruptedIOException {
            if (cancelled || Thread.currentThread().isInterrupted()) throw new InterruptedIOException("检查已取消");
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) throw new InterruptedIOException("检查超时，请检查网络或更换更新清单");
            return Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining));
        }
        void cancel() {
            cancelled = true;
            synchronized (connections) { for (HttpURLConnection request : connections) request.disconnect(); }
        }
        void register(HttpURLConnection connection) throws IOException {
            connections.add(connection);
            remainingMillis();
        }
        void release(HttpURLConnection connection) { connections.remove(connection); connection.disconnect(); }
    }

    private final ExecutorService requests = Executors.newFixedThreadPool(2, task -> {
        Thread thread = new Thread(task, "host-update-check"); thread.setDaemon(true); return thread;
    });
    private final Reader reader;
    private final long timeoutMillis;
    private volatile Ticket active;

    UpdateManifestFetch() { this(6000, UpdateManifestFetch::readHttps); }
    UpdateManifestFetch(long timeoutMillis, Reader reader) { this.timeoutMillis = timeoutMillis; this.reader = reader; }

    <T> T fetch(List<String> addresses, Validator<T> validator) throws Exception {
        Ticket ticket = new Ticket(timeoutMillis);
        active = ticket;
        List<Future<T>> futures = new ArrayList<>();
        CompletionService<T> completed = new ExecutorCompletionService<>(requests);
        Exception last = null;
        boolean allMissing = true;
        try {
            for (String address : new java.util.LinkedHashSet<>(addresses)) {
                futures.add(completed.submit(() -> {
                    byte[] bytes = reader.read(address, ticket);
                    ticket.remainingMillis();
                    T parsed = validator.validate(bytes);
                    ticket.remainingMillis();
                    return parsed;
                }));
            }
            if (futures.isEmpty()) throw new IOException("没有可用的更新清单地址");
            for (int pending = futures.size(); pending > 0; pending--) {
                Future<T> result;
                do { result = completed.poll(Math.min(50, ticket.remainingMillis()), TimeUnit.MILLISECONDS); }
                while (result == null);
                try { return result.get(); }
                catch (java.util.concurrent.ExecutionException failed) {
                    Throwable cause = failed.getCause();
                    last = cause instanceof Exception ? (Exception) cause : new IOException("更新清单读取失败", cause);
                    allMissing &= last instanceof FileNotFoundException;
                }
            }
            if (allMissing) throw new FileNotFoundException("更新清单不可访问");
            throw last == null ? new IOException("更新清单读取失败") : last;
        } finally {
            ticket.cancel();
            for (Future<T> future : futures) future.cancel(true);
            if (active == ticket) active = null;
        }
    }

    void cancel() { Ticket ticket = active; if (ticket != null) ticket.cancel(); }
    @Override public void close() { cancel(); requests.shutdownNow(); }

    private static byte[] readHttps(String address, Ticket ticket) throws Exception {
        URL url = new URL(https(address));
        for (int redirects = 0; redirects < 6; redirects++) {
            HttpURLConnection request = (HttpURLConnection) url.openConnection();
            try {
                ticket.register(request);
                request.setConnectTimeout((int) Math.min(3500, ticket.remainingMillis()));
                request.setReadTimeout((int) Math.min(3500, ticket.remainingMillis()));
                request.setInstanceFollowRedirects(false);
                request.setRequestProperty("User-Agent", "Kira-Android-Linux-Host");
                request.setRequestProperty("Accept", "application/json");
                request.setRequestProperty("Accept-Encoding", "identity");
                request.setUseCaches(false);
                int status = request.getResponseCode();
                if (status >= 300 && status <= 399) {
                    String location = request.getHeaderField("Location");
                    if (location == null) throw new IOException("更新地址重定向无效");
                    url = new URL(https(new URL(url, location).toString()));
                    continue;
                }
                if (status == 404 || status == 403) throw new FileNotFoundException("更新清单不存在或无公开访问权限");
                if (status != 200) throw new IOException("服务器返回 HTTP " + status);
                try (InputStream input = request.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                    byte[] buffer = new byte[8192]; int count;
                    while (true) {
                        request.setReadTimeout((int) Math.min(3500, ticket.remainingMillis()));
                        count = input.read(buffer);
                        if (count == -1) return bytes.toByteArray();
                        if (bytes.size() + count > 1024 * 1024) throw new IOException("更新清单过大");
                        bytes.write(buffer, 0, count);
                    }
                }
            } finally { ticket.release(request); }
        }
        throw new IOException("更新地址重定向过多");
    }

    private static String https(String value) throws Exception {
        URI uri = new URI(value);
        if (!"https".equals(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null || uri.getFragment() != null)
            throw new IOException("需要无凭据的 HTTPS 地址");
        return value;
    }
}
