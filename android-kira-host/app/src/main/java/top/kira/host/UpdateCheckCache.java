package top.kira.host;

import java.util.function.LongSupplier;

/** Process-only successful results; a cache hit is always labelled separately from a fresh check. */
final class UpdateCheckCache<T> {
    static final class Hit<T> {
        final T value;
        final long ageMillis;
        Hit(T value, long ageMillis) { this.value = value; this.ageMillis = ageMillis; }
        String caption() { return "上次检查结果（" + Math.max(1, ageMillis / 1000) + "秒前缓存），可点重新检查联网刷新。\n"; }
    }
    private final LongSupplier clock;
    private final long ttlMillis;
    private String source, mirror;
    private T value;
    private long checkedAt;
    UpdateCheckCache(long ttlMillis, LongSupplier clock) { this.ttlMillis = ttlMillis; this.clock = clock; }
    synchronized void put(String source, String mirror, T value) {
        this.source = source; this.mirror = mirror; this.value = value; checkedAt = clock.getAsLong();
    }
    synchronized Hit<T> find(String source, String mirror) {
        long age = clock.getAsLong() - checkedAt;
        if (value == null || !source.equals(this.source) || !mirror.equals(this.mirror) || age < 0 || age > ttlMillis) return null;
        return new Hit<>(value, age);
    }
}
