package top.kira.host;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Runs the production export coordinator with controlled blocking providers, without an Android device. */
public final class BackupExportTaskTest {
    private static int passed;
    private static void require(boolean condition, String reason) { if (!condition) throw new AssertionError(reason); }
    private static void await(CountDownLatch latch) {
        try { require(latch.await(3, TimeUnit.SECONDS), "Controlled operation timed out"); }
        catch (InterruptedException error) { throw new AssertionError(error); }
    }
    private static void start(BackupExportTask task, Runnable work, Runnable finished) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
        while (!task.start(work, finished)) {
            require(System.nanoTime() < deadline, "Previous export slot was not released");
            Thread.sleep(5);
        }
    }
    private static void pass(String name) { passed++; System.out.println("PASS " + name); }

    private static void successfulCommit() throws Exception {
        AtomicInteger aborts = new AtomicInteger();
        AtomicReference<Throwable> error = new AtomicReference<>();
        BackupExportTask task = new BackupExportTask(aborts::incrementAndGet);
        CountDownLatch done = new CountDownLatch(1);
        start(task, () -> { try { task.commit(); } catch (Throwable failure) { error.set(failure); } }, done::countDown);
        await(done);
        require(error.get() == null && task.committed(), "Successful export was not committed");
        require(!task.cancel() && aborts.get() == 0, "Cancellation after commit affected a finished archive");
        pass("completed_archive_is_not_cancelled_or_deleted");
    }

    private static void closeUnblocksWrite() throws Exception {
        CountDownLatch attached = new CountDownLatch(1), blocked = new CountDownLatch(1), done = new CountDownLatch(1);
        AtomicReference<Throwable> error = new AtomicReference<>();
        BackupExportTask task = new BackupExportTask(() -> { });
        start(task, () -> {
            try {
                task.attach(blocked::countDown);
                attached.countDown();
                await(blocked);
                task.commit();
                error.set(new AssertionError("Cancelled write committed"));
            } catch (IOException expected) { }
            catch (Throwable failure) { error.set(failure); }
        }, done::countDown);
        await(attached);
        require(task.cancel(), "Cancellation was not accepted during write");
        await(done);
        require(error.get() == null && !task.committed(), "Cancellation did not stop publication");
        pass("cancel_closes_blocked_output_and_rejects_success");
    }

    private static void blockingAbortIsIsolatedAndBounded() throws Exception {
        CountDownLatch attached = new CountDownLatch(1), workRelease = new CountDownLatch(1), done = new CountDownLatch(1);
        CountDownLatch cancelEntered = new CountDownLatch(1), closeEntered = new CountDownLatch(1);
        CountDownLatch cancelRelease = new CountDownLatch(1), closeRelease = new CountDownLatch(1);
        AtomicInteger aborts = new AtomicInteger();
        AtomicReference<Throwable> error = new AtomicReference<>();
        BackupExportTask task = new BackupExportTask(() -> {
            aborts.incrementAndGet(); cancelEntered.countDown(); await(cancelRelease);
        });
        ExecutorService listing = Executors.newSingleThreadExecutor();
        try {
            start(task, () -> {
                try {
                    task.attach(() -> { closeEntered.countDown(); await(closeRelease); });
                    attached.countDown(); await(workRelease); task.checkpoint();
                    error.set(new AssertionError("Cancelled operation continued"));
                } catch (IOException expected) { }
                catch (Throwable failure) { error.set(failure); }
            }, done::countDown);
            await(attached);
            long started = System.nanoTime();
            require(task.cancel(), "First cancellation rejected");
            require(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 1000, "Provider blocked cancellation caller");
            require(!task.cancel(), "Repeated cancel launched another abort");
            await(cancelEntered); await(closeEntered);
            require(listing.submit(() -> 42).get(1, TimeUnit.SECONDS) == 42, "Backup listing was blocked");
            workRelease.countDown(); await(done);
            require(!new BackupExportTask(() -> { }).start(() -> { }, () -> { }), "Slot reused while aborts still blocked");
            cancelRelease.countDown();
            require(!new BackupExportTask(() -> { }).start(() -> { }, () -> { }), "Slot reused while close still blocked");
            require(aborts.get() == 1 && error.get() == null, "Cancellation was duplicated or lost");
        } finally {
            workRelease.countDown(); cancelRelease.countDown(); closeRelease.countDown(); listing.shutdownNow();
        }
        CountDownLatch nextDone = new CountDownLatch(1);
        start(new BackupExportTask(() -> { }), () -> { }, nextDone::countDown); await(nextDone);
        pass("unresponsive_provider_is_bounded_and_does_not_block_listing_or_ui");
    }

    private static void lateOpenIsClosed() throws Exception {
        CountDownLatch opening = new CountDownLatch(1), returned = new CountDownLatch(1), done = new CountDownLatch(1);
        AtomicInteger closes = new AtomicInteger(), cancels = new AtomicInteger();
        AtomicReference<Throwable> error = new AtomicReference<>();
        BackupExportTask task = new BackupExportTask(cancels::incrementAndGet);
        start(task, () -> {
            opening.countDown(); await(returned);
            try (ByteArrayOutputStream output = new ByteArrayOutputStream() {
                @Override public void close() { closes.incrementAndGet(); }
            }) {
                task.attach(output);
                error.set(new AssertionError("Late provider output was accepted after cancellation"));
            } catch (IOException expected) { }
            catch (Throwable failure) { error.set(failure); }
        }, done::countDown);
        await(opening); require(task.cancel(), "Cancellation while opening rejected"); returned.countDown(); await(done);
        require(closes.get() == 1 && error.get() == null && !task.committed(), "Late output leaked or committed");
        CountDownLatch nextDone = new CountDownLatch(1);
        start(new BackupExportTask(() -> { }), () -> { }, nextDone::countDown); await(nextDone);
        require(cancels.get() == 1, "Provider cancellation signal missing");
        pass("late_provider_return_is_closed_without_publication");
    }

    private static void cancelAndCommitAreExclusive() throws Exception {
        for (int i = 0; i < 50; i++) {
            CountDownLatch ready = new CountDownLatch(1), race = new CountDownLatch(1), done = new CountDownLatch(1);
            AtomicReference<Throwable> error = new AtomicReference<>();
            BackupExportTask task = new BackupExportTask(() -> { });
            start(task, () -> {
                ready.countDown(); await(race);
                try { task.commit(); } catch (IOException expected) { }
                catch (Throwable failure) { error.set(failure); }
            }, done::countDown);
            await(ready); race.countDown(); boolean cancelled = task.cancel(); await(done);
            require(error.get() == null && task.committed() != cancelled, "Accepted cancellation raced into committed success");
        }
        pass("accepted_cancellation_and_success_are_atomic_and_mutually_exclusive");
    }

    public static void main(String[] args) throws Exception {
        successfulCommit(); closeUnblocksWrite(); blockingAbortIsIsolatedAndBounded(); lateOpenIsClosed(); cancelAndCommitAreExclusive();
        System.out.println(passed + " backup export checks passed");
    }
}
