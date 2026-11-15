package top.kira.host;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.FileObserver;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;
import java.io.IOException;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentLinkedDeque;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

public final class HostService extends Service {
    public static final String STOP = "top.kira.host.STOP";
    public static final String CONTROL = "top.kira.host.CONTROL";
    public static final String COMPONENT = "component", ACTION = "component_action";
    public static final String RESUME = "resume_runtime";
    private static final String CHANNEL = "kira_linux_host";
    private static final int NOTIFICATION_ID = 51, ERROR_ID = 52;
    private static final int CONTROL_RETRY_MS = 250, NOTICE_COALESCE_MS = 250, NOTICE_FALLBACK_MS = 4_000;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final ScheduledExecutorService controls = Executors.newSingleThreadScheduledExecutor();
    private final ScheduledExecutorService notifications = Executors.newSingleThreadScheduledExecutor();
    private final ConcurrentLinkedDeque<Command> pending = new ConcurrentLinkedDeque<>();
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Object finalizationLock = new Object();
    private boolean bridgeInvoked, draining, noticeQueued, deliveryQueued;
    private ScheduledFuture<?> controlRetry;
    private FileObserver statusObserver;
    private Notice displayedNotice;
    private JSONObject pendingStatus;
    private volatile boolean running, stopping, finishing, destroyed;
    private volatile int generation, latestStartId;
    private String ownerToken;
    private boolean recovering;
    private volatile int resourcesFinishedGeneration = -1, controlsFinishedGeneration = -1;
    private volatile int stopPendingGeneration = -1;
    private Command restartAfterStop;
    private NotificationManager manager;
    private PowerManager.WakeLock wakeLock;

    private static final class Command {
        final String component, action;
        final boolean resume;
        Command(String component, String action, boolean resume) {
            this.component = component;
            this.action = action;
            this.resume = resume;
        }
        boolean valid() {
            if (resume) return "all".equals(component) && "start".equals(action);
            if (cleanup()) return true;
            return ("kira".equals(component) || "napcat".equals(component) || "all".equals(component))
                && ("start".equals(action) || "stop".equals(action) || "restart".equals(action)
                    || ("napcat".equals(component) && ("update".equals(action) || "install".equals(action) || "uninstall".equals(action)))
                    || ("kira".equals(component) && ("backup".equals(action) || "restore".equals(action))));
        }
        boolean cleanup() { return "all".equals(component) && ("retry_cleanup".equals(action) || "verify_cleanup".equals(action)); }
        boolean maintenance() {
            return cleanup() || "update".equals(action) || "install".equals(action) || "backup".equals(action)
                || "restore".equals(action) || "uninstall".equals(action);
        }
    }

    @Override public void onCreate() {
        super.onCreate();
        manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "KiraAI 与 NapCat 运行进度", NotificationManager.IMPORTANCE_LOW));
        PowerManager power = getSystemService(PowerManager.class);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, getPackageName() + ":linux-runtime");
        wakeLock.setReferenceCounted(false);
        observeStatus();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (destroyed) return START_NOT_STICKY;
        latestStartId = startId;
        boolean explicit = intent != null && CONTROL.equals(intent.getAction());
        Command command = explicit ? new Command(intent.getStringExtra(COMPONENT), intent.getStringExtra(ACTION),
            intent.getBooleanExtra(RESUME, false)) : new Command("all", "start", true);
        if ((intent != null && STOP.equals(intent.getAction())) || ("all".equals(command.component) && "stop".equals(command.action))) {
            pending.clear();
            restartAfterStop = null;
            RuntimeStatus.setManuallyStopped(this, true);
            RuntimeStatus.recordExplicitStop(this);
            stopRuntime(true);
            return START_NOT_STICKY;
        }
        if (!command.valid() || (command.resume && RuntimeStatus.automaticStartPaused(this))) {
            if (!running && !finishing && !stopping) stopSelfResult(startId);
            return START_NOT_STICKY;
        }
        if (recovering && (running || finishing || stopping)) {
            android.widget.Toast.makeText(this, "正在核验停止状态，请稍候", android.widget.Toast.LENGTH_SHORT).show();
            return START_NOT_STICKY;
        }
        if (command.cleanup()) {
            RuntimeStatus.setManuallyStopped(this, true);
            RuntimeStatus.recordExplicitStop(this);
            if (running || finishing || stopping) {
                restartAfterStop = command;
                stopRuntime(true);
                return START_NOT_STICKY;
            }
        }
        if (command.resume && (running || stopping || finishing)) return START_NOT_STICKY;
        if (stopping) {
            if (!"stop".equals(command.action)) restartAfterStop = command;
            return START_NOT_STICKY;
        }
        if (finishing) { pending.add(command); return START_NOT_STICKY; }
        if (running) {
            pending.add(command);
            requestControlDrain();
        } else if (!"stop".equals(command.action)) {
            begin(command);
        } else stopSelfResult(startId);
        return START_NOT_STICKY;
    }

    private void begin(Command command) {
        synchronized (finalizationLock) {
            if (destroyed) return;
            if (running || finishing) { pending.add(command); return; }
            JSONObject previous = RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(this), "status.json"));
            if (!command.cleanup() && RuntimeStatus.requiresCleanup(this, previous)) {
                pending.clear();
                restartAfterStop = null;
                RuntimeStatus.write(this, "error", "无法确认组件进程已停止，禁止再次启动，请查看日志");
                JSONObject value = RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(this), "status.json"));
                try { manager.notify(ERROR_ID, buildNotification(value, true)); }
                catch (RuntimeException ignored) { }
                stopSelfResult(latestStartId);
                return;
            }
            boolean maintenance = command.maintenance();
            final String selected;
            try {
                Set<String> choices = ComponentSelection.persist(this, null, false);
                if (("update".equals(command.action) && !RuntimeStatus.napcatInstalled(this, previous))
                        || ("install".equals(command.action) && !choices.contains("napcat"))) {
                    stopSelfResult(latestStartId);
                    return;
                }
                if (maintenance) choices.clear();
                else {
                    if (!"all".equals(command.component)) choices.removeIf(name -> !name.equals(command.component));
                    if (choices.contains("napcat") && !RuntimeStatus.napcatInstalled(this, previous)) choices.remove("napcat");
                    if (choices.isEmpty()) {
                        stopSelfResult(latestStartId);
                        return;
                    }
                }
                selected = ComponentSelection.json(choices);
            } catch (IOException failure) {
                android.util.Log.w("KiraHost", "Cannot persist the component selection", failure);
                RuntimeStatus.write(this, "error", "无法保存组件选择，请检查存储后重试");
                stopSelfResult(latestStartId);
                return;
            }
            running = true;
            recovering = command.cleanup();
            stopping = false;
            final int runGeneration = ++generation;
            final String runOwner = UUID.randomUUID().toString();
            ownerToken = runOwner;
            resourcesFinishedGeneration = controlsFinishedGeneration = -1;
            bridgeInvoked = false;
            noticeQueued = deliveryQueued = false;
            pendingStatus = null;
            String initialActions = maintenance ? "[{\"component\":\"" + command.component + "\",\"action\":\"" + command.action + "\"}]" : "[]";
            RuntimeStatus.starting(this, selected);
            manager.cancel(ERROR_ID);
            try {
                Notice initial = notice(RuntimeStatus.readJson(new File(RuntimeStatus.directory(this), "status.json")), false);
                Notification notification = buildNotification(initial, false);
                if (Build.VERSION.SDK_INT >= 34)
                    startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
                else startForeground(NOTIFICATION_ID, notification);
                wakeLock.acquire();
                displayedNotice = initial;
            } catch (RuntimeException failure) {
                running = false;
                releaseWakeLock();
                RuntimeStatus.write(this, "error", "无法建立前台运行服务，请重新打开应用后重试");
                stopSelf();
                return;
            }
            worker.execute(() -> {
                try {
                    if (command.cleanup()) {
                        if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                        if (stopping || destroyed) return;
                        PyObject outcome = Python.getInstance().getModule("host_runtime").callAttr("recover_cleanup",
                            getFilesDir().getAbsolutePath(), getApplicationInfo().nativeLibraryDir, getPackageName(),
                            "retry_cleanup".equals(command.action), runOwner);
                        JSONObject result = new JSONObject(outcome.toString());
                        String detail = result.optString("detail", "停止核验未完成，请重试");
                        if (!result.optBoolean("released")) RuntimeStatus.write(this, "error", detail);
                        main.post(() -> {
                            if (!destroyed && runGeneration == generation)
                                android.widget.Toast.makeText(this, detail, android.widget.Toast.LENGTH_LONG).show();
                        });
                        return;
                    }
                    if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                    if (stopping || destroyed) return;
                    RuntimeAssets.Prepared assets = RuntimeAssets.prepare(this, () -> stopping || destroyed);
                    if (stopping || destroyed) return;
                    if (!enterBridge(runGeneration)) return;
                    Python.getInstance().getModule("host_runtime").callAttr("run", getFilesDir().getAbsolutePath(),
                        getApplicationInfo().nativeLibraryDir, assets.manifest, assets.seed.getAbsolutePath(), assets.abi,
                        RuntimeAssets.dns(this), selected, initialActions, RuntimeStatus.bootCount(this), command.resume, runOwner);
                } catch (Throwable failure) {
                    if (!stopping && !destroyed && runGeneration == generation) {
                        JSONObject value = RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(this), "status.json"));
                        if (!"error".equals(value.optString("state")))
                            RuntimeStatus.write(this, "error", "环境启动失败，请查看环境日志。" + failure.getClass().getSimpleName());
                        android.util.Log.e("KiraHost", "Linux host failed", failure);
                    }
                } finally {
                    finishRun(runGeneration);
                }
            });
        }
    }

    @SuppressWarnings("deprecation") // String paths also support API 28.
    private void observeStatus() {
        File folder = RuntimeStatus.directory(this);
        if (folder.isDirectory() || folder.mkdirs()) {
            statusObserver = new FileObserver(folder.getAbsolutePath(), FileObserver.CLOSE_WRITE | FileObserver.MOVED_TO) {
                @Override public void onEvent(int event, String name) {
                    if ("status.json".equals(name)) requestStatusRefresh();
                }
            };
            statusObserver.startWatching();
        }
        notifications.scheduleWithFixedDelay(this::requestStatusRefresh, NOTICE_FALLBACK_MS,
            NOTICE_FALLBACK_MS, TimeUnit.MILLISECONDS);
    }

    private void requestStatusRefresh() {
        synchronized (finalizationLock) {
            if (!running || finishing || destroyed || noticeQueued) return;
            noticeQueued = true;
            int expected = generation;
            notifications.schedule(() -> readStatusForNotice(expected), NOTICE_COALESCE_MS, TimeUnit.MILLISECONDS);
        }
    }

    private void readStatusForNotice(int expected) {
        synchronized (finalizationLock) {
            if (expected != generation) return;
            noticeQueued = false;
            if (!running || finishing || destroyed) return;
        }
        JSONObject value = RuntimeStatus.readJson(new File(RuntimeStatus.directory(this), "status.json"));
        if (value.length() == 0) return;
        synchronized (finalizationLock) {
            if (expected != generation || !running || finishing || destroyed) return;
            pendingStatus = value;
            if (!deliveryQueued) {
                deliveryQueued = true;
                main.post(() -> deliverStatus(expected));
            }
        }
    }

    private void deliverStatus(int expected) {
        synchronized (finalizationLock) {
            if (expected != generation || !running || finishing || destroyed) return;
            deliveryQueued = false;
            JSONObject value = pendingStatus;
            pendingStatus = null;
            if (value != null) refreshNotification(expected, value);
        }
    }

    private void refreshNotification(int expected, JSONObject value) {
        synchronized (finalizationLock) {
            if (expected != generation || !running || finishing || destroyed) return;
            Notice next = notice(value, false);
            if (next.equals(displayedNotice)) return;
            try {
                manager.notify(NOTIFICATION_ID, buildNotification(next, false));
                displayedNotice = next;
            } catch (RuntimeException ignored) { }
        }
    }

    /** Finalize the owned round before any queued command may create another one. */
    private void finishRun(int runGeneration) {
        synchronized (finalizationLock) {
            if (runGeneration != generation || finishing) return;
            finishing = true;
            cancelControlRetry();
            if (destroyed) { running = false; return; }
        }
        boolean cleaned = true;
        try { releaseWakeLock(); }
        catch (Throwable failure) {
            cleaned = false;
            android.util.Log.e("KiraHost", "Cannot release the completed round's wake lock", failure);
        }
        synchronized (finalizationLock) {
            // Destruction and notification delivery use this same ownership lock.
            if (runGeneration != generation) return;
            if (!destroyed) {
                try {
                    if (stopping) {
                        JSONObject stopped = RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(this), "status.json"));
                        if (RuntimeStatus.hasUnsafeStop(stopped))
                            RuntimeStatus.write(this, "error", "无法确认组件进程已停止，禁止再次启动，请查看日志");
                        else RuntimeStatus.write(this, "stopped", "全部组件已停止，再次启动会接着准备");
                    }
                    JSONObject value = RuntimeStatus.readJson(new java.io.File(RuntimeStatus.directory(this), "status.json"));
                    if ("error".equals(value.optString("state")) || RuntimeStatus.hasComponentError(value)) {
                        try { manager.notify(ERROR_ID, buildNotification(value, true)); }
                        catch (RuntimeException ignored) { }
                    }
                } catch (Throwable failure) {
                    cleaned = false;
                    android.util.Log.e("KiraHost", "Cannot finalize the completed round's status", failure);
                }
                // Always attempt foreground cleanup even if a preceding cleanup failed.
                try { stopForeground(STOP_FOREGROUND_REMOVE); }
                catch (Throwable failure) {
                    cleaned = false;
                    android.util.Log.e("KiraHost", "Cannot remove the completed round's foreground service", failure);
                }
            }
            running = false;
            if (destroyed) return;
            if (!cleaned) {
                RuntimeStatus.write(this, "error", "前台运行资源清理未完成，已禁止交接新一轮，请查看日志");
                return;
            }
            resourcesFinishedGeneration = runGeneration;
            // This acknowledgement comes after all older controls on the serial executor.
            if (!controls.isShutdown()) {
                try { controls.execute(() -> main.post(() -> completeRun(runGeneration))); }
                catch (java.util.concurrent.RejectedExecutionException failure) {
                    android.util.Log.w("KiraHost", "Final control barrier was unavailable", failure);
                }
            }
        }
    }

    private void completeRun(int completedGeneration) {
        synchronized (finalizationLock) {
            if (completedGeneration != generation || destroyed || resourcesFinishedGeneration != completedGeneration) return;
            controlsFinishedGeneration = completedGeneration;
            finishOrRestart(completedGeneration);
        }
    }

    private boolean enterBridge(int expected) {
        synchronized (finalizationLock) {
            if (expected != generation || !controlsActive()) return false;
            bridgeInvoked = true;
            requestControlDrain();
            return true;
        }
    }

    private boolean controlsActive() {
        return running && !finishing && !stopping && !destroyed && !controls.isShutdown();
    }

    private void requestControlDrain() {
        synchronized (finalizationLock) {
            if (!controlsActive() || !bridgeInvoked || draining || controlRetry != null || pending.isEmpty()) return;
            final int controlGeneration = generation;
            final String controlOwner = ownerToken;
            // Admit the task under the same lock used to enqueue the final control barrier.
            draining = true;
            controls.execute(() -> drainControls(controlGeneration, controlOwner));
        }
    }

    private void drainControls(int controlGeneration, String controlOwner) {
        boolean unavailable = false;
        try {
            Command command;
            while (running && !finishing && !stopping && !destroyed && controlGeneration == generation && (command = pending.poll()) != null) {
                try {
                    PyObject accepted = Python.getInstance().getModule("host_runtime")
                        .callAttr("control", command.component, command.action, controlOwner);
                    if (accepted != null && "False".equals(accepted.toString())) {
                        if (!"stop".equals(command.action)) {
                            JSONObject status = RuntimeStatus.readJson(new File(RuntimeStatus.directory(this), "status.json"));
                            if (status.optInt("pid", -1) == android.os.Process.myPid() && RuntimeStatus.hasUnsafeStop(status)) {
                                reportControlFailure(controlGeneration);
                                continue;
                            }
                        }
                        requeueRejectedControl(controlGeneration, command);
                        unavailable = true;
                        break;
                    }
                } catch (Throwable failure) {
                    android.util.Log.w("KiraHost", "Component control failed", failure);
                    reportControlFailure(controlGeneration);
                }
            }
        } finally {
            synchronized (finalizationLock) {
                draining = false;
                if (controlGeneration == generation && controlsActive() && !pending.isEmpty()) {
                    if (unavailable) {
                        controlRetry = controls.schedule(() -> retryControls(controlGeneration), CONTROL_RETRY_MS,
                            TimeUnit.MILLISECONDS);
                    } else requestControlDrain();
                }
            }
        }
    }

    private void reportControlFailure(int controlGeneration) {
        main.post(() -> {
            if (controlGeneration == generation && running && !finishing && !stopping && !destroyed)
                android.widget.Toast.makeText(this, "组件操作未成功，请重试并查看环境日志", android.widget.Toast.LENGTH_LONG).show();
        });
    }

    private void retryControls(int expected) {
        synchronized (finalizationLock) {
            if (expected != generation) return;
            controlRetry = null;
            requestControlDrain();
        }
    }

    private void cancelControlRetry() {
        if (controlRetry != null) controlRetry.cancel(false);
        controlRetry = null;
    }

    private void requeueRejectedControl(int controlGeneration, Command command) {
        synchronized (finalizationLock) {
            // Python can close its manager before this result reaches Java. Keep the command
            // available to the handoff unless STOP or destruction has canceled the queue.
            if (controlGeneration == generation && !stopping && !destroyed) pending.addFirst(command);
        }
    }

    private void finishOrRestart(int completedGeneration) {
        synchronized (finalizationLock) {
            if (completedGeneration != generation || running || destroyed || !finishing
                    || resourcesFinishedGeneration != completedGeneration || controlsFinishedGeneration != completedGeneration
                    || stopPendingGeneration == completedGeneration) return;
            finishing = false;
            if (restartAfterStop != null) {
                Command command = restartAfterStop;
                restartAfterStop = null;
                stopping = false;
                begin(command);
                return;
            }
            Command command;
            if (!stopping) while ((command = pending.poll()) != null) {
                if (!"stop".equals(command.action)) { begin(command); return; }
            }
            stopSelfResult(latestStartId);
        }
    }

    private static final class Notice {
        final String title, detail;
        final int progress;
        final boolean progressVisible, indeterminate, stopAction;

        Notice(String title, String detail, int progress, boolean progressVisible, boolean indeterminate, boolean stopAction) {
            this.title = title;
            this.detail = detail;
            this.progress = progress;
            this.progressVisible = progressVisible;
            this.indeterminate = indeterminate;
            this.stopAction = stopAction;
        }

        @Override public boolean equals(Object other) {
            if (!(other instanceof Notice)) return false;
            Notice notice = (Notice) other;
            return title.equals(notice.title) && detail.equals(notice.detail) && progress == notice.progress
                && progressVisible == notice.progressVisible && indeterminate == notice.indeterminate && stopAction == notice.stopAction;
        }

        @Override public int hashCode() {
            return Objects.hash(title, detail, progress, progressVisible, indeterminate, stopAction);
        }
    }

    private Notice notice(JSONObject value, boolean error) {
        boolean clearing = stopping;
        String detail = clearing ? "正在清理运行和安装进程" : value.optString("detail", "正在启动环境");
        RuntimeStatus.Transfer transfer = RuntimeStatus.Transfer.from(value);
        if (transfer.active && !clearing) detail = transfer.text;
        String components = RuntimeStatus.summary(value);
        if (!components.isEmpty()) detail += "\n" + components;
        boolean progressVisible = !error && (transfer.active || RuntimeStatus.preparing(value) || clearing);
        int percent = progressVisible ? (transfer.active ? transfer.percent : RuntimeStatus.percent(value, "progress")) : -1;
        boolean indeterminate = progressVisible && (percent < 0 || clearing);
        return new Notice(RuntimeStatus.title(value), detail, indeterminate ? 0 : Math.max(0, percent),
                          progressVisible, indeterminate, !error && !clearing);
    }

    private Notification buildNotification(JSONObject value, boolean error) {
        return buildNotification(notice(value, error), error);
    }

    private Notification buildNotification(Notice notice, boolean error) {
        Notification.Builder builder = new Notification.Builder(this, CHANNEL)
            .setSmallIcon(error ? android.R.drawable.stat_notify_error : android.R.drawable.ic_dialog_info)
            .setContentTitle(notice.title).setContentText(notice.detail)
            .setStyle(new Notification.BigTextStyle().bigText(notice.detail)).setOnlyAlertOnce(true)
            .setContentIntent(PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE))
            .setVisibility(Notification.VISIBILITY_PRIVATE).setShowWhen(false).setOngoing(!error).setAutoCancel(error);
        if (notice.progressVisible) builder.setProgress(100, notice.progress, notice.indeterminate);
        if (notice.stopAction) builder.addAction(new Notification.Action.Builder(null, "停止全部",
            PendingIntent.getService(this, 1, new Intent(this, HostService.class).setAction(STOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE)).build());
        return builder.build();
    }

    private void stopRuntime(boolean explicit) {
        synchronized (finalizationLock) {
            if (stopping) return;
            stopping = true;
            pending.clear();
            cancelControlRetry();
            requestStatusRefresh();
            if (!running) {
                if (!destroyed) {
                    if (finishing) finishOrRestart(generation);
                    else { releaseWakeLock(); stopSelf(); }
                }
                return;
            }
            final int stopGeneration = generation;
            final String stopOwner = ownerToken;
            stopPendingGeneration = stopGeneration;
            new Thread(() -> {
                try {
                    // Repeat across the asset-to-Python handoff so an early stop is kept.
                    for (int attempt = 0; attempt < 300 && running && stopGeneration == generation; attempt++) {
                        try {
                            if (Python.isStarted()) Python.getInstance().getModule("host_runtime").callAttr("stop", explicit, stopOwner);
                            Thread.sleep(200);
                        } catch (InterruptedException interrupted) {
                            Thread.currentThread().interrupt();
                            return;
                        } catch (Throwable failure) { android.util.Log.w("KiraHost", "Stop request failed", failure); }
                    }
                } finally { main.post(() -> stopCompleted(stopGeneration)); }
            }, "kira-host-stop").start();
        }
    }

    private void stopCompleted(int completedGeneration) {
        synchronized (finalizationLock) {
            if (stopPendingGeneration != completedGeneration) return;
            stopPendingGeneration = -1;
            if (!destroyed && generation == completedGeneration) finishOrRestart(completedGeneration);
        }
    }

    private synchronized void releaseWakeLock() { if (wakeLock != null && wakeLock.isHeld()) wakeLock.release(); }

    @Override public void onDestroy() {
        synchronized (finalizationLock) {
            destroyed = true;
            stopRuntime(false);
            if (statusObserver != null) statusObserver.stopWatching();
            notifications.shutdownNow();
            controls.shutdown();
            worker.shutdown();
            releaseWakeLock();
        }
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
