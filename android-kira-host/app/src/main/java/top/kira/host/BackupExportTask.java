package top.kira.host;

import java.io.Closeable;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.util.concurrent.Semaphore;

/** Isolates a document provider from backup listing, with one process-wide export slot. */
final class BackupExportTask {
    private static final Semaphore SLOT = new Semaphore(1);
    private final Runnable cancelProvider;
    private Closeable output;
    private boolean started, cancelled, committed, workerDone, released;
    private int pendingAborts;

    BackupExportTask(Runnable cancelProvider) { this.cancelProvider = cancelProvider; }

    // Advisory before opening the document picker; start() remains the atomic guard.
    static boolean available() { return SLOT.availablePermits() > 0; }

    synchronized boolean start(Runnable work, Runnable finished) {
        if (started) throw new IllegalStateException("Export already started");
        if (!SLOT.tryAcquire()) return false;
        started = true;
        try {
            launch("kira-backup-export", () -> {
                try { work.run(); }
                finally {
                    synchronized (this) { workerDone = true; releaseIfFinished(); }
                    finished.run();
                }
            });
        } catch (RuntimeException | Error failure) {
            workerDone = true;
            releaseIfFinished();
            throw failure;
        }
        return true;
    }

    synchronized void attach(Closeable stream) throws IOException {
        // The caller owns the try-with-resources scope, including a late provider return.
        checkpoint();
        output = stream;
    }

    synchronized void checkpoint() throws IOException {
        if (cancelled || Thread.currentThread().isInterrupted())
            throw new InterruptedIOException("Backup export cancelled");
    }

    synchronized void commit() throws IOException {
        // Called only after verification AND closing the destination successfully.
        checkpoint();
        committed = true;
    }

    synchronized boolean committed() { return committed; }

    boolean cancel() {
        Closeable stream;
        synchronized (this) {
            if (cancelled || committed || workerDone) return false;
            cancelled = true;
            stream = output;
            pendingAborts += stream == null ? 1 : 2;
        }
        // A provider can block in either cancel() or close(). Neither runs on the UI/listing thread.
        abort("kira-backup-provider-cancel", cancelProvider);
        if (stream != null) abort("kira-backup-provider-close", () -> {
            try { stream.close(); } catch (IOException ignored) { }
        });
        return true;
    }

    private void abort(String name, Runnable action) {
        try {
            launch(name, () -> {
                try { action.run(); }
                finally { abortFinished(); }
            });
        } catch (RuntimeException | Error failure) {
            abortFinished();
            throw failure;
        }
    }

    private synchronized void abortFinished() { pendingAborts--; releaseIfFinished(); }

    private void releaseIfFinished() {
        // Do not accumulate abandoned provider workers across Activity recreation or retries.
        if (started && workerDone && pendingAborts == 0 && !released) {
            released = true;
            SLOT.release();
        }
    }

    private static void launch(String name, Runnable action) {
        Thread thread = new Thread(action, name);
        thread.setDaemon(true);
        thread.start();
    }
}
