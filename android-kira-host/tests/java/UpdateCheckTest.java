package top.kira.host;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/** Offline controlled transports; no real repository, device, APK install, or account use. */
public final class UpdateCheckTest {
    private static final byte[] VALID = "valid manifest".getBytes(StandardCharsets.UTF_8);
    private static int passed;
    private static void require(boolean value, String detail) { if (!value) throw new AssertionError(detail); }
    private static String validate(byte[] bytes) throws IOException {
        String text = new String(bytes, StandardCharsets.UTF_8);
        if (!text.startsWith("valid ")) throw new IOException("Invalid test manifest identity");
        return text;
    }
    private static long elapsed(long start) { return TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start); }
    private static void pass(String name) { passed++; System.out.println("PASS " + name); }
    private static void waitFor(CountDownLatch signal) throws Exception {
        require(signal.await(2, TimeUnit.SECONDS), "Controlled reader did not start");
    }
    private static void firstValidWins() throws Exception {
        CountDownLatch both = new CountDownLatch(2);
        try (UpdateManifestFetch fetch = new UpdateManifestFetch(1500, (address, ticket) -> {
            both.countDown(); waitFor(both);
            if (address.equals("official")) { Thread.sleep(500); return "valid official".getBytes(StandardCharsets.UTF_8); }
            Thread.sleep(25); return "valid mirror".getBytes(StandardCharsets.UTF_8);
        })) {
            long start = System.nanoTime();
            String result = fetch.fetch(Arrays.asList("official", "mirror"), UpdateCheckTest::validate);
            require(result.equals("valid mirror"), "Slower official source won the race");
            require(elapsed(start) < 1000, "Check waited for slower losing request");
        }
        pass("first_valid_source_wins_without_waiting_for_slow_peer");
    }
    private static void errorDoesNotWin() throws Exception {
        for (String kind : new String[]{"404", "invalid"}) {
            try (UpdateManifestFetch fetch = new UpdateManifestFetch(1500, (address, ticket) -> {
                if (address.equals("failed")) {
                    if (kind.equals("404")) throw new FileNotFoundException("controlled 404");
                    return "wrong identity".getBytes(StandardCharsets.UTF_8);
                }
                Thread.sleep(70); return VALID;
            })) {
                require(fetch.fetch(Arrays.asList("failed", "working"), UpdateCheckTest::validate).equals("valid manifest"),
                    "Fast error beat a slower valid response");
            }
            pass("fast_" + kind + "_does_not_mask_valid_peer");
        }
    }
    private static void missingStaysMissing() throws Exception {
        AtomicInteger reads = new AtomicInteger();
        try (UpdateManifestFetch fetch = new UpdateManifestFetch(1000, (address, ticket) -> {
            reads.incrementAndGet(); throw new FileNotFoundException("controlled inaccessible manifest");
        })) {
            long start = System.nanoTime();
            try { fetch.fetch(Arrays.asList("official", "official"), UpdateCheckTest::validate); throw new AssertionError("404 became success"); }
            catch (FileNotFoundException expected) { }
            require(elapsed(start) < 800 && reads.get() == 1, "Missing manifest retried or duplicate source was fetched");
        }
        pass("inaccessible_manifest_fails_fast_and_duplicate_transport_is_merged");
    }
    private static void totalDeadline() throws Exception {
        CountDownLatch started = new CountDownLatch(2);
        try (UpdateManifestFetch fetch = new UpdateManifestFetch(6000, (address, ticket) -> {
            started.countDown(); waitFor(started); Thread.sleep(15000); return VALID;
        })) {
            long start = System.nanoTime();
            try { fetch.fetch(Arrays.asList("slow-official", "slow-mirror"), UpdateCheckTest::validate); throw new AssertionError("Stalled check succeeded"); }
            catch (InterruptedIOException expected) { require(expected.getMessage().contains("超时"), "Deadline misreported"); }
            long duration = elapsed(start);
            require(duration >= 5700 && duration < 7300, "Six-second total deadline was not honored: " + duration);
            System.out.println("METRIC total_deadline_ms=" + duration);
        }
        pass("two_stalled_sources_share_one_six_second_total_deadline");
    }
    private static void cancellationRejectsLateSuccess() throws Exception {
        CountDownLatch started = new CountDownLatch(1), release = new CountDownLatch(1);
        AtomicReference<Throwable> error = new AtomicReference<>();
        AtomicReference<String> value = new AtomicReference<>();
        try (UpdateManifestFetch fetch = new UpdateManifestFetch(4000, (address, ticket) -> {
            started.countDown();
            // Model a late callback that cannot be interrupted. The Ticket must still reject its result.
            boolean done = false;
            while (!done) { try { release.await(); done = true; } catch (InterruptedException ignored) { } }
            return VALID;
        })) {
            Thread caller = new Thread(() -> {
                try { value.set(fetch.fetch(Arrays.asList("delayed"), UpdateCheckTest::validate)); }
                catch (Throwable failure) { error.set(failure); }
            }, "controlled-update-caller"); caller.setDaemon(true); caller.start(); waitFor(started);
            long start = System.nanoTime(); fetch.cancel(); caller.join(1000);
            require(!caller.isAlive(), "Cancelled fetch remained blocked");
            require(error.get() instanceof InterruptedIOException && value.get() == null, "Cancellation delivered success or wrong failure");
            require(elapsed(start) < 800, "Cancellation waited for original deadline");
            release.countDown(); caller.join(500);
            require(value.get() == null, "Late reader completion escaped cancellation");
        } finally { release.countDown(); }
        pass("cancel_returns_promptly_and_rejects_late_reader_success");
    }
    private static void cacheTruthAndExpiry() {
        AtomicLong clock = new AtomicLong(1000);
        UpdateCheckCache<String> cache = new UpdateCheckCache<>(60000, clock::get);
        require(cache.find("source", "mirror") == null, "Empty cache returned a result");
        cache.put("source", "mirror", "checked release"); clock.set(3500);
        UpdateCheckCache.Hit<String> hit = cache.find("source", "mirror");
        require(hit != null && hit.value.equals("checked release") && hit.ageMillis == 2500, "Cache lost actual value/time");
        require(hit.caption().contains("上次检查结果") && hit.caption().contains("缓存") && hit.caption().contains("重新检查"), "Cache is presented as a fresh check");
        require(cache.find("other-source", "mirror") == null && cache.find("source", "other-mirror") == null, "Cache crossed source settings");
        clock.set(61001); require(cache.find("source", "mirror") == null, "Expired cache still returned a result");
        clock.set(999); require(cache.find("source", "mirror") == null, "Backward clock reused cache");
        pass("cache_is_labelled_as_previous_result_and_expires_with_source_identity");
    }
    public static void main(String[] args) throws Exception {
        firstValidWins(); errorDoesNotWin(); missingStaysMissing(); totalDeadline(); cancellationRejectsLateSuccess(); cacheTruthAndExpiry();
        System.out.println("RESULT " + passed + " passed");
    }
}
