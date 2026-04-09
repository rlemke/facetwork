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

package fw.agent;

import fw.agent.model.TaskDocument;
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

        Optional<TaskDocument> taskOpt = ops.claimTask(handlerNames, config.taskList());
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
            Optional<TaskDocument> taskOpt = ops.claimTask(handlerNames, config.taskList());
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
            ops.markTaskFailed(task, "no handler registered");
            return;
        }

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

            // Invoke handler
            Map<String, Object> result = handler.handle(params);

            long durationMs = System.currentTimeMillis() - dispatchStart;

            // Write returns to step
            if (result != null && !result.isEmpty()) {
                ops.writeStepReturns(task.stepId(), result);
            }

            // Insert resume task for Python RunnerService
            ops.insertResumeTask(task.stepId(), task.workflowId(), task.taskListName(), task.name());

            // Mark task completed
            ops.markTaskCompleted(task);

            // 4. Handler completed
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_SUCCESS,
                    "Handler completed: " + task.name() + " (" + durationMs + "ms)");

        } catch (Exception e) {
            // 5. Handler error
            emitStepLog(task.stepId(), task.workflowId(), task.name(),
                    Protocol.STEP_LOG_LEVEL_ERROR, "Handler error: " + e.getMessage());
            logger.log(Level.WARNING, "Handler error for " + task.name(), e);
            ops.markTaskFailed(task, e.getMessage());
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
            if (registration != null) {
                registration.heartbeat(serverId);
            }
        } catch (Exception e) {
            logger.log(Level.WARNING, "Heartbeat error", e);
        }
    }
}
