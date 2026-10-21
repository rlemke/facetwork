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

import afl.agent.model.StepAttribute;
import afl.agent.model.TaskDocument;
import com.mongodb.client.MongoCollection;
import com.mongodb.client.MongoDatabase;
import com.mongodb.client.model.FindOneAndUpdateOptions;
import com.mongodb.client.model.ReturnDocument;
import com.mongodb.client.model.Updates;
import org.bson.Document;
import org.bson.conversions.Bson;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static com.mongodb.client.model.Filters.*;

/**
 * MongoDB operations for the AFL agent protocol.
 */
public class MongoOps {

    private final MongoDatabase db;

    public MongoOps(MongoDatabase db) {
        this.db = db;
    }

    /**
     * Claims one task (protocol v1.2 {@code claim_task}, then optionally
     * {@code reclaim_expired_task}). Records {@code serverId} and a lease.
     *
     * <p>Besides name and task list the filter skips tasks in their retry
     * cooldown, tasks that need a script environment or FFL feature (an agent
     * runs no evaluator, so it declines both), and tasks whose {@code requires}
     * floor exceeds {@code resources}. A dimension the task names that is not
     * in {@code resources} declines the claim.
     *
     * @param reclaimExpired also try to take over a running task whose lease
     *                       expired; callers pace this (see
     *                       {@link Protocol#LEASE_RECLAIM_SCAN_MS})
     */
    public Optional<TaskDocument> claimTask(List<String> taskNames, List<String> taskLists,
                                            String serverId, Map<String, Object> resources,
                                            boolean reclaimExpired) {
        if (taskNames.isEmpty()) {
            return Optional.empty();
        }
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);
        long now = nowMillis();
        Document set = new Document("state", Protocol.TASK_STATE_RUNNING)
                .append("updated", now)
                .append("server_id", serverId)
                .append("lease_expires", now + Protocol.LEASE_MS);
        FindOneAndUpdateOptions after = new FindOneAndUpdateOptions().returnDocument(ReturnDocument.AFTER);

        Document pending = new Document("state", Protocol.TASK_STATE_PENDING)
                .append("task_list_name", taskListFilter(taskLists))
                .append("$and", List.of(
                        nameFilter(taskNames),
                        new Document("$or", List.of(
                                new Document("next_retry_after", new Document("$exists", false)),
                                new Document("next_retry_after", 0),
                                new Document("next_retry_after", new Document("$lte", now)))),
                        noEnvironment(),
                        new Document("$or", List.of(
                                new Document("required_features", new Document("$exists", false)),
                                new Document("required_features", List.of()))),
                        resourceFilter(resources)));
        Document doc = collection.findOneAndUpdate(pending, new Document("$set", set), after);
        if (doc != null) {
            return Optional.of(TaskDocument.fromDocument(doc));
        }
        if (!reclaimExpired) {
            return Optional.empty();
        }

        // A running task whose holder stopped renewing its lease. It is a RETRY,
        // so retry_count is bumped, and only while budget remains: an exhausted
        // one is left for the runtime's reaper, which dead-letters it.
        Document expired = new Document("state", Protocol.TASK_STATE_RUNNING)
                .append("task_list_name", taskListFilter(taskLists))
                .append("lease_expires", new Document("$lt", now).append("$gt", 0))
                .append("$and", List.of(
                        nameFilter(taskNames),
                        noEnvironment(),
                        new Document("$or", List.of(
                                new Document("max_retries", new Document("$lte", 0)),
                                new Document("$expr", new Document("$lt", List.of(
                                        new Document("$ifNull", List.of("$retry_count", 0)),
                                        new Document("$ifNull", List.of("$max_retries",
                                                Protocol.DEFAULT_MAX_RETRIES)))))))));
        doc = collection.findOneAndUpdate(expired,
                new Document("$set", set).append("$inc", new Document("retry_count", 1)), after);
        return doc == null ? Optional.empty() : Optional.of(TaskDocument.fromDocument(doc));
    }

    /** Reads one task by uuid, or empty when it does not exist. */
    public Optional<TaskDocument> getTask(String taskId) {
        Document doc = db.getCollection(Protocol.COLLECTION_TASKS).find(eq("uuid", taskId)).first();
        return doc == null ? Optional.empty() : Optional.of(TaskDocument.fromDocument(doc));
    }

    private static Object taskListFilter(List<String> taskLists) {
        return taskLists.size() == 1 ? taskLists.get(0) : new Document("$in", taskLists);
    }

    /** Exact names, or {@code name:} prefixes (the prefix is a literal, so quoted). */
    private static Document nameFilter(List<String> taskNames) {
        List<Document> or = new java.util.ArrayList<>();
        or.add(new Document("name", new Document("$in", taskNames)));
        for (String n : taskNames) {
            or.add(new Document("name", new Document("$regex",
                    "^" + java.util.regex.Pattern.quote(n) + ":")));
        }
        return new Document("$or", or);
    }

    private static Document noEnvironment() {
        return new Document("$or", List.of(
                new Document("environment_hash", new Document("$exists", false)),
                new Document("environment_hash", "")));
    }

    private static Document resourceFilter(Map<String, Object> resources) {
        List<Document> or = new java.util.ArrayList<>();
        or.add(new Document("requires", new Document("$exists", false)));
        or.add(new Document("requires", new Document()));
        Map<String, Object> have = new java.util.LinkedHashMap<>();
        if (resources != null) {
            resources.forEach((k, v) -> {
                if (v instanceof Number) {
                    have.put(k, v);
                }
            });
        }
        if (!have.isEmpty()) {
            List<Document> perDim = new java.util.ArrayList<>();
            perDim.add(new Document("requires_dims", new Document("$not",
                    new Document("$elemMatch", new Document("$nin", List.copyOf(have.keySet()))))));
            have.forEach((dim, cap) -> perDim.add(new Document("$or", List.of(
                    new Document("requires." + dim, new Document("$exists", false)),
                    new Document("requires." + dim, new Document("$lte", cap))))));
            or.add(new Document("$and", perDim));
        }
        return new Document("$or", or);
    }

    /**
     * Renews a running task's lease (protocol {@code renew_task_lease}).
     * Ownership-gated: returns false when the task is no longer this server's.
     */
    public boolean renewLease(String taskId, String serverId, Integer progressPct, String progressMessage) {
        long now = nowMillis();
        Document set = new Document("task_heartbeat", now).append("lease_expires", now + Protocol.LEASE_MS);
        if (progressPct != null) {
            set.append("progress_pct", Math.max(0, Math.min(100, progressPct)));
        }
        if (progressMessage != null) {
            set.append("progress_message", progressMessage);
        }
        return db.getCollection(Protocol.COLLECTION_TASKS).updateOne(
                and(eq("uuid", taskId), eq("state", Protocol.TASK_STATE_RUNNING), eq("server_id", serverId)),
                new Document("$set", set)).getMatchedCount() > 0;
    }

    /**
     * Why an in-flight execution should stop, or empty (protocol
     * {@code check_cancellation}): the task is gone, canceled, already terminal
     * (e.g. the runtime's timeout watchdog), or reclaimed by another server.
     */
    public Optional<String> cancellationReason(String taskId, String serverId) {
        Document doc = db.getCollection(Protocol.COLLECTION_TASKS)
                .find(eq("uuid", taskId))
                .projection(new Document("state", 1).append("server_id", 1).append("_id", 0))
                .first();
        if (doc == null) {
            return Optional.of("task record no longer exists");
        }
        String state = String.valueOf(doc.get("state"));
        if (Protocol.TASK_STATE_CANCELED.equals(state)) {
            return Optional.of("task was canceled (terminate-workflow)");
        }
        if (List.of(Protocol.TASK_STATE_COMPLETED, Protocol.TASK_STATE_FAILED,
                Protocol.TASK_STATE_DEAD_LETTER, Protocol.TASK_STATE_IGNORED).contains(state)) {
            return Optional.of("task already terminal (" + state + ")");
        }
        Object holder = doc.get("server_id");
        if (serverId != null && !serverId.equals(holder == null ? "" : holder.toString())) {
            return Optional.of("task was reclaimed by another runner");
        }
        return Optional.empty();
    }

    /**
     * Reads the params attribute from a step.
     */
    public Map<String, Object> readStepParams(String stepId) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);

        Document step = collection.find(eq("uuid", stepId)).first();
        if (step == null) {
            throw new IllegalArgumentException("Step not found: " + stepId);
        }

        Map<String, Object> result = new HashMap<>();

        Document attributes = step.get("attributes", Document.class);
        if (attributes != null) {
            Document params = attributes.get("params", Document.class);
            if (params != null) {
                for (String name : params.keySet()) {
                    Document attr = params.get(name, Document.class);
                    if (attr != null) {
                        result.put(name, attr.get("value"));
                    }
                }
            }
        }

        return result;
    }

    /**
     * Fetches the full snapshot of a referenced step.  Mirrors Python
     * {@code HandlerContext.fetch_step}: given a tagged JSON FacetRef
     * ({@code {_facet_ref:true, step_id, ...}}), return a map shaped
     * like {@code {step_id, workflow_id, facet_name, params, returns}}.
     * The snapshot is read-only by contract.
     */
    public Map<String, Object> fetchStep(Map<String, Object> ref) {
        Object idObj = ref.get("step_id");
        if (!(idObj instanceof String) || ((String) idObj).isEmpty()) {
            throw new IllegalArgumentException("fetch_step: ref missing 'step_id'");
        }
        String stepId = (String) idObj;

        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);
        Document step = collection.find(eq("uuid", stepId)).first();
        if (step == null) {
            throw new IllegalArgumentException("Step not found: " + stepId);
        }

        Map<String, Object> params = new HashMap<>();
        Map<String, Object> returns = new HashMap<>();
        Document attributes = step.get("attributes", Document.class);
        if (attributes != null) {
            Document p = attributes.get("params", Document.class);
            if (p != null) {
                for (String name : p.keySet()) {
                    Document attr = p.get(name, Document.class);
                    if (attr != null) {
                        params.put(name, attr.get("value"));
                    }
                }
            }
            Document r = attributes.get("returns", Document.class);
            if (r != null) {
                for (String name : r.keySet()) {
                    Document attr = r.get(name, Document.class);
                    if (attr != null) {
                        returns.put(name, attr.get("value"));
                    }
                }
            }
        }

        Map<String, Object> result = new HashMap<>();
        result.put("step_id", step.getString("uuid"));
        result.put("workflow_id", step.getString("workflow_id"));
        result.put("facet_name", step.getString("facet_name"));
        result.put("params", params);
        result.put("returns", returns);
        return result;
    }

    /**
     * Writes return attributes to a step.
     */
    public void writeStepReturns(String stepId, Map<String, Object> returns) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);

        Document setFields = new Document();
        for (Map.Entry<String, Object> entry : returns.entrySet()) {
            String name = entry.getKey();
            Object value = entry.getValue();

            Document attr = new Document()
                    .append("name", name)
                    .append("value", value)
                    .append("type_hint", StepAttribute.inferTypeHint(value));

            setFields.append("attributes.returns." + name, attr);
        }

        collection.updateOne(
                and(
                        eq("uuid", stepId),
                        eq("state", Protocol.STEP_STATE_EVENT_TRANSMIT)
                ),
                new Document("$set", setFields)
        );
    }

    /**
     * Merges partial return attributes into a step.
     * Unlike writeStepReturns, this does NOT require the step to be in EVENT_TRANSMIT state,
     * allowing handlers to stream partial results during execution.
     */
    public void updateStepReturns(String stepId, Map<String, Object> partial) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEPS);

        Document setFields = new Document();
        for (Map.Entry<String, Object> entry : partial.entrySet()) {
            String name = entry.getKey();
            Object value = entry.getValue();

            Document attr = new Document()
                    .append("name", name)
                    .append("value", value)
                    .append("type_hint", StepAttribute.inferTypeHint(value));

            setFields.append("attributes.returns." + name, attr);
        }

        collection.updateOne(
                eq("uuid", stepId),
                new Document("$set", setFields)
        );
    }

    /**
     * Marks a task completed (protocol {@code complete_task}), only while this
     * server still holds it. Returns false when the task was reclaimed or
     * terminated: another execution owns the outcome, so do NOT insert a resume.
     */
    public boolean completeTask(TaskDocument task, String serverId) {
        return db.getCollection(Protocol.COLLECTION_TASKS).updateOne(
                and(eq("uuid", task.uuid()), eq("state", Protocol.TASK_STATE_RUNNING),
                        eq("server_id", serverId)),
                Updates.combine(
                        Updates.set("state", Protocol.TASK_STATE_COMPLETED),
                        Updates.set("updated", nowMillis())
                )).getMatchedCount() > 0;
    }

    /** The outcome of {@link #failTransition}. */
    public record FailTransition(String state, int retryCount, long nextRetryAfter) {
        public boolean deadLettered() {
            return Protocol.TASK_STATE_DEAD_LETTER.equals(state);
        }
    }

    /**
     * The protocol's failure transition (conformance/retry_cases.json). A
     * permanent error dead-letters without spending budget; otherwise the
     * count goes up and the task is either dead-lettered (budget spent) or
     * returned to pending after an exponential backoff.
     */
    public static FailTransition failTransition(int retryCount, int maxRetries, boolean permanent, long now) {
        if (permanent) {
            return new FailTransition(Protocol.TASK_STATE_DEAD_LETTER, retryCount, 0);
        }
        int next = retryCount + 1;
        if (maxRetries > 0 && next >= maxRetries) {
            return new FailTransition(Protocol.TASK_STATE_DEAD_LETTER, next, 0);
        }
        return new FailTransition(Protocol.TASK_STATE_PENDING, next, now + Protocol.retryBackoffMs(next));
    }

    /**
     * Records a handler failure (protocol {@code fail_task}), only while this
     * server still holds the task. Returns the transition applied, or empty if
     * the task was no longer this server's.
     *
     * <p>Never writes {@code failed}: the runtime's stuck-step sweep sees no
     * active and no dead-lettered task for the step and creates a fresh one at
     * retry_count 0, so a failure would retry forever. A dead-lettered task makes
     * the sweep fail the step instead.
     */
    public Optional<FailTransition> failTask(TaskDocument task, String serverId, String errorMsg,
                                             boolean permanent) {
        long now = nowMillis();
        FailTransition t = failTransition(task.retryCount(), task.maxRetries(), permanent, now);
        Document error = new Document("message", errorMsg == null ? "" : errorMsg);
        Document set = new Document("state", t.state())
                .append("updated", now)
                .append("retry_count", t.retryCount());
        if (t.deadLettered()) {
            if (permanent) {
                error.append("permanent", true);
            }
        } else {
            set.append("server_id", "").append("next_retry_after", t.nextRetryAfter());
        }
        set.append("error", error);
        long matched = db.getCollection(Protocol.COLLECTION_TASKS).updateOne(
                and(eq("uuid", task.uuid()), eq("state", Protocol.TASK_STATE_RUNNING),
                        eq("server_id", serverId)),
                new Document("$set", set)).getMatchedCount();
        return matched > 0 ? Optional.of(t) : Optional.empty();
    }

    /**
     * Inserts a step log entry for dashboard observability.
     * Best-effort: errors are caught and logged.
     */
    public void insertStepLog(String stepId, String workflowId, String runnerId,
                              String facetName, String source, String level, String message) {
        try {
            MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_STEP_LOGS);

            long now = nowMillis();
            Document doc = new Document()
                    .append("uuid", UUID.randomUUID().toString())
                    .append("step_id", stepId)
                    .append("workflow_id", workflowId)
                    .append("runner_id", runnerId)
                    .append("facet_name", facetName)
                    .append("source", source)
                    .append("level", level)
                    .append("message", message)
                    .append("details", new Document())
                    .append("time", now);

            collection.insertOne(doc);
        } catch (Exception e) {
            java.util.logging.Logger.getLogger(MongoOps.class.getName())
                    .fine("Could not save step log for step " + stepId + ": " + e.getMessage());
        }
    }

    /**
     * Inserts an fw:resume task for the Python RunnerService.
     */
    public void insertResumeTask(String stepId, String workflowId, String taskList) {
        insertResumeTask(stepId, workflowId, taskList, "");
    }

    public void insertResumeTask(String stepId, String workflowId, String taskList, String facetName) {
        MongoCollection<Document> collection = db.getCollection(Protocol.COLLECTION_TASKS);

        String resumeName = (facetName != null && !facetName.isEmpty())
                ? Protocol.RESUME_TASK_NAME + ":" + facetName
                : Protocol.RESUME_TASK_NAME;
        long now = nowMillis();
        Document task = new Document()
                .append("uuid", UUID.randomUUID().toString())
                .append("name", resumeName)
                .append("runner_id", "")
                .append("workflow_id", workflowId)
                .append("flow_id", "")
                .append("step_id", stepId)
                .append("state", Protocol.TASK_STATE_PENDING)
                .append("created", now)
                .append("updated", now)
                .append("task_list_name", taskList)
                .append("data_type", "resume")
                .append("data", new Document()
                        .append("step_id", stepId)
                        .append("workflow_id", workflowId));

        collection.insertOne(task);
    }

    /**
     * Returns current time in milliseconds since Unix epoch.
     */
    public static long nowMillis() {
        return System.currentTimeMillis();
    }
}
