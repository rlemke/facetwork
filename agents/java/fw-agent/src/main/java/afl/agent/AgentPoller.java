// Copyright 2025 Ralph Lemke
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package afl.agent;

import afl.agent.model.TaskDocument;
import com.mongodb.client.MongoClient;
import com.mongodb.client.MongoClients;
import com.mongodb.client.MongoDatabase;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * AgentPoller polls for tasks and dispatches to registered handlers.
 */
public class AgentPoller implements AutoCloseable {

    private static final Logger logger = Logger.getLogger(AgentPoller.class.getName());

    private final AgentPollerConfig config;
    private final String serverId;
    private final Map<String, Handler> handlers = new ConcurrentHashMap<>();

    private MongoClient client;
    private MongoDatabase db;
    private MongoOps ops;
    private ServerRegistration registration;

    private java.util.function.Function<String, Map<String, Object>> metadataProvider;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private ScheduledExecutorService scheduler;
    private ExecutorService executor;
    private Semaphore semaphore;

    // Tasks this server is running, for lease renewal; value = still owned.
    private final Map<String, AtomicBoolean> activeTasks = new ConcurrentHashMap<>();
    private volatile long lastReclaimScanMs = 0;

    public AgentPoller(AgentPollerConfig config) {
        this.config = config;
        this.serverId = UUID.randomUUID().toString();
    }

    /**
     * Sets a metadata provider function for injecting _handler_metadata.
     */
    public void setMetadataProvider(java.util.function.Function<String, Map<String, Object>> provider) {
        this.metadataProvider = provider;
    }

    /**
     * Registers a handler for a qualified facet name.
     */
    public void register(String facetName, Handler handler) {
        handlers.put(facetName, handler);
    }

    /**
     * Returns a list of registered handler names.
     */
    public List<String> registeredHandlers() {
        return new ArrayList<>(handlers.keySet());
    }

    /**
     * Connects to MongoDB and begins the poll loop.
     * This method blocks until stop() is called.
     */
    public void start() throws InterruptedException {
        if (running.getAndSet(true)) {
            return;
        }

        // Connect to MongoDB
        client = MongoClients.create(config.mongoUrl());
        db = client.getDatabase(config.database());
        ops = new MongoOps(db);
        registration = new ServerRegistration(db);

        // Initialize concurrency control
        semaphore = new Semaphore(config.maxConcurrent());
        executor = Executors.newFixedThreadPool(config.maxConcurrent());
        scheduler = Executors.newScheduledThreadPool(2);

        // Register server
        List<String> handlerNames = registeredHandlers();
        registration.register(serverId, config, handlerNames);

        // Start heartbeat
        scheduler.scheduleAtFixedRate(
                this::heartbeat,
                config.heartbeatIntervalMs(),
                config.heartbeatIntervalMs(),
                TimeUnit.MILLISECONDS
        );

        // Start poll loop
        scheduler.scheduleAtFixedRate(
                this::pollCycle,
                0,
                config.pollIntervalMs(),
                TimeUnit.MILLISECONDS
        );

        // Block until stopped
        while (running.get()) {
            Thread.sleep(100);
        }
    }

    /**
     * Starts the poller without blocking.
     */
    public void startAsync() {
        new Thread(() -> {
            try {
                start();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }, "AgentPoller-main").start();
    }

    /**
     * Stops the poller and cleans up resources.
     */
    public void stop() {
        if (!running.getAndSet(false)) {
            return;
        }

        // Stop scheduler
        if (scheduler != null) {
            scheduler.shutdown();
            try {
                scheduler.awaitTermination(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        // Stop executor
        if (executor != null) {
            executor.shutdown();
            try {
                executor.awaitTermination(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }

        // Deregister server
        if (registration != null) {
            try {
                registration.deregister(serverId);
            } catch (Exception e) {
                logger.log(Level.WARNING, "Failed to deregister server", e);
            }
        }

        // Disconnect from MongoDB
        if (client != null) {
            client.close();
            client = null;
            db = null;
            ops = null;
            registration = null;
        }
    }

    @Override
    public void close() {
        stop();
    }

    /**
     * Performs a single poll cycle. Useful for testing.
     */
    public void pollOnce() {
        if (db == null) {
            // Connect if not already connected
            client = MongoClients.create(config.mongoUrl());
            db = client.getDatabase(config.database());
            ops = new MongoOps(db);
            registration = new ServerRegistration(db);
        }

        List<String> handlerNames = registeredHandlers();
        if (handlerNames.isEmpty()) {
            return;
        }

        Optional<TaskDocument> taskOpt = claimNext(handlerNames);
        taskOpt.ifPresent(this::processTask);
    }

    private void pollCycle() {
        try {
            List<String> handlerNames = registeredHandlers();
            if (handlerNames.isEmpty()) {
                return;
            }

            // Check concurrency limit
            if (!semaphore.tryAcquire()) {
                return;
            }

            // Try to claim a task
            Optional<TaskDocument> taskOpt = claimNext(handlerNames);
            if (taskOpt.isEmpty()) {
                semaphore.release();
                return;
            }

            // Process task asynchronously
            TaskDocument task = taskOpt.get();
            executor.submit(() -> {
                try {
                    processTask(task);
                } finally {
                    semaphore.release();
                }
            });
        } catch (Exception e) {
            logger.log(Level.WARNING, "Poll cycle error", e);
        }
    }

    /**
     * One claim (protocol v1.2): records this server and a lease, offers the
     * memory free now, and every {@link Protocol#LEASE_RECLAIM_SCAN_MS} also
     * looks for a running task whose lease expired.
     */
    private Optional<TaskDocument> claimNext(List<String> handlerNames) {
        long now = System.currentTimeMillis();
        boolean reclaim = now - lastReclaimScanMs >= Protocol.LEASE_RECLAIM_SCAN_MS;
        Optional<TaskDocument> task = ops.claimTask(handlerNames, List.of(config.taskList()),
                serverId, Resources.claimResources(), reclaim);
        if (task.isEmpty() && reclaim) {
            lastReclaimScanMs = now;
        }
        return task;
    }

    private void emitStepLog(String stepId, String workflowId, String facetName,
                             String level, String message) {
        try {
            ops.insertStepLog(stepId, workflowId, serverId, facetName,
                    Protocol.STEP_LOG_SOURCE_FRAMEWORK, level, message);
        } catch (Exception e) {
            // best-effort
        }
    }

    private void processTask(TaskDocument task) {
        // 1. Task claimed
        emitStepLog(task.stepId(), task.workflowId(), task.name(),
                Protocol.STEP_LOG_LEVEL_INFO, "Task claimed: " + task.name());

        // Find handler
        Handler handler = findHandler(task.name());
        if (handler == null) {
            // 2. No handler found
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_ERROR,
                    "Handler error: No handler registered for: " + task.name());
            logger.warning("No handler for task: " + task.name());
            ops.failTask(task, serverId, "no handler registered", false);
            return;
        }

        AtomicBoolean owned = new AtomicBoolean(true);
        activeTasks.put(task.uuid(), owned);

        try {
            // 3. Dispatching handler
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_INFO, "Dispatching handler: " + task.name());

            long dispatchStart = System.currentTimeMillis();

            // Read step parameters
            Map<String, Object> params = ops.readStepParams(task.stepId());

            // Inject handler-level step_log callback
            java.util.function.BiConsumer<String, String> stepLogCb = (message, level) -> {
                try {
                    ops.insertStepLog(task.stepId(), task.workflowId(), serverId,
                            task.name(), Protocol.STEP_LOG_SOURCE_HANDLER, level, message);
                } catch (Exception e) { /* best-effort */ }
            };
            params.put("_step_log", stepLogCb);

            // Inject _facet_name
            params.put("_facet_name", task.name());

            // Inject _handler_metadata if provider is available
            if (metadataProvider != null) {
                Map<String, Object> meta = metadataProvider.apply(task.name());
                if (meta != null) {
                    params.put("_handler_metadata", meta);
                }
            }

            // Inject _update_step callback for streaming partial results
            java.util.function.Consumer<Map<String, Object>> updateStepCb = (partial) -> {
                try {
                    ops.updateStepReturns(task.stepId(), partial);
                } catch (Exception e) { /* best-effort */ }
            };
            params.put("_update_step", updateStepCb);

            // Inject _task_heartbeat(progressPct, progressMessage): renews the
            // lease now (the heartbeat thread also renews it every interval).
            java.util.function.BiConsumer<Integer, String> heartbeatCb = (pct, msg) -> {
                try {
                    if (!ops.renewLease(task.uuid(), serverId, pct, msg)) {
                        owned.set(false);
                    }
                } catch (Exception e) { /* best-effort */ }
            };
            params.put("_task_heartbeat", heartbeatCb);

            // Inject _is_cancelled(): true once the task was canceled, finished
            // by someone else, or reclaimed. A long handler should check it at
            // batch boundaries and return early.
            java.util.function.Supplier<Boolean> cancelledCb = () -> {
                if (!owned.get()) {
                    return true;
                }
                try {
                    return ops.cancellationReason(task.uuid(), serverId).isPresent();
                } catch (Exception e) {
                    return false;
                }
            };
            params.put("_is_cancelled", cancelledCb);

            // Invoke handler
            Map<String, Object> result = handler.handle(params);

            long durationMs = System.currentTimeMillis() - dispatchStart;

            // Another execution may own this task now (reclaimed after a lost
            // lease, canceled, or finished by the timeout watchdog): write nothing.
            Optional<String> stop = ops.cancellationReason(task.uuid(), serverId);
            if (stop.isPresent()) {
                emitStepLog(task.stepId(), task.workflowId(), task.name(),
                        Protocol.STEP_LOG_LEVEL_WARNING,
                        "Handler result discarded: " + stop.get());
                logger.warning("Discarding result of " + task.name() + " " + task.uuid()
                        + ": " + stop.get());
                return;
            }

            // Write returns to step
            if (result != null && !result.isEmpty()) {
                ops.writeStepReturns(task.stepId(), result);
            }

            // Complete (ownership-gated), then signal the Python RunnerService.
            if (!ops.completeTask(task, serverId)) {
                emitStepLog(task.stepId(), task.workflowId(), task.name(),
                        Protocol.STEP_LOG_LEVEL_WARNING,
                        "Handler result discarded: task was reclaimed before completion");
                return;
            }
            ops.insertResumeTask(task.stepId(), task.workflowId(), task.taskListName(), task.name());

            // 4. Handler completed
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_SUCCESS,
                    "Handler completed: " + task.name() + " (" + durationMs + "ms)");

        } catch (Exception e) {
            // 5. Handler error: retry with backoff, or dead-letter (never 'failed').
            boolean permanent = e instanceof PermanentError;
            Optional<MongoOps.FailTransition> t = ops.failTask(task, serverId, e.getMessage(), permanent);
            String what = t.map(x -> x.deadLettered()
                            ? (permanent ? "dead-lettered (permanent)"
                                         : "dead-lettered after " + x.retryCount() + " attempts")
                            : "retry " + x.retryCount() + "/" + task.maxRetries() + " in "
                              + (x.nextRetryAfter() - System.currentTimeMillis()) / 1000 + "s")
                    .orElse("not recorded: task no longer held by this server");
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_ERROR, "Handler error (" + what + "): " + e.getMessage());
            logger.log(Level.WARNING, "Handler error for " + task.name() + " (" + what + ")", e);
        } finally {
            activeTasks.remove(task.uuid());
        }
    }

    private Handler findHandler(String taskName) {
        // Try exact match first
        Handler handler = handlers.get(taskName);
        if (handler != null) {
            return handler;
        }

        // Try short name fallback (ns.Facet -> Facet)
        int lastDot = taskName.lastIndexOf('.');
        if (lastDot >= 0) {
            String shortName = taskName.substring(lastDot + 1);
            return handlers.get(shortName);
        }

        return null;
    }

    private void heartbeat() {
        try {
            if (registration != null && !registration.heartbeat(serverId) && running.get()) {
                logger.warning("Server record " + serverId + " missing or marked shutdown"
                        + " -- registering again");
                registration.register(serverId, config, registeredHandlers());
            }
        } catch (Exception e) {
            logger.log(Level.WARNING, "Heartbeat error", e);
        }
        // Renew the lease of every task still running here.
        for (Map.Entry<String, AtomicBoolean> entry : activeTasks.entrySet()) {
            try {
                if (!ops.renewLease(entry.getKey(), serverId, null, null)) {
                    entry.getValue().set(false);
                }
            } catch (Exception e) {
                logger.log(Level.FINE, "Lease renewal failed for " + entry.getKey(), e);
            }
        }
    }
}
